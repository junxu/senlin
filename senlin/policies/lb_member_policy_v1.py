# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_context import context as oslo_context
from oslo_log import log as logging

from senlin.common import constraints
from senlin.common import consts
from senlin.common.i18n import _
from senlin.common.i18n import _LW
from senlin.common import schema
from senlin.db import api as db_api
from senlin.drivers import base as driver_base
from senlin.engine import cluster_policy
from senlin.engine import node as node_mod
from senlin.policies import base

LOG = logging.getLogger(__name__)


class LBMemberPolicyV1(base.Policy):
    '''Policy for load balancing among members of a cluster.

    This policy is expected to be enforced after the member list of a cluster
    is changed. We need to reload the load-balancer specified (or internally
    created) when these actions are performed.
    '''
    VERSION = '1.0'

    TARGET = [
        ('AFTER', consts.CLUSTER_ADD_NODES),
        ('AFTER', consts.CLUSTER_DEL_NODES),
        ('AFTER', consts.CLUSTER_SCALE_OUT),
        ('AFTER', consts.CLUSTER_SCALE_IN),
        ('AFTER', consts.CLUSTER_RESIZE),
    ]

    PROFILE_TYPE = [
        'os.nova.server-1.0',
    ]


    KEYS = (
        POOL, PROTOCOL_PORT,
    ) = (
        'pool', 'protocol_port',
    )

    properties_schema = {
        POOL: schema.String(
            _('id of lb pool.'),
            required=True,
        ),
        PROTOCOL_PORT: schema.Integer(
            _('Port on which servers are running on the nodes, default=80.'),
            default=80,
        )
    }

    def __init__(self, name, spec, **kwargs):
        super(LBMemberPolicyV1, self).__init__(name, spec, **kwargs)

        self.pool = self.properties[self.POOL]
        self.protocol_port = self.properties[self.PROTOCOL_PORT]
        self.validate()
        self.lb = None

    def validate(self):
        super(LBMemberPolicyV1, self).validate()

    def _add_members(self, cluster, nodes):
        params = self._build_conn_params(cluster)
        lb_driver = driver_base.SenlinDriver().loadbalancing_v1(params)
       
        for node in nodes:
            # add checking this node is not in this pool.
            # if a adress+port already in a pool, there wiil raise a excetiopn
            LOG.info(_LW('Create member for node %(n)s with port %(o)s in lb pool %(p)s.'),
                {'n': node_id, 'o': self.protocol_port, 'p': self.pool})
            member_id = lb_driver.member_add(node, self.pool, self.protocol_port)
            # TODO(Anyone): Ingore member add fail
            if member_id is not None:
                node.data.update({'lb_member': member_id})
                node.store(oslo_context.get_current())

    def _remove_members(self, cluster, nodes):
        params = self._build_conn_params(cluster)
        lb_driver = driver_base.SenlinDriver().loadbalancing_v1(params)

        for node in nodes:
            if 'lb_member' in node.data:
                member_id = node.data.pop('lb_member')
                node.store(oslo_context.get_current())
                LOG.info(_LW('Remove member for node %(n)s with port %(o)s in lb pool %(p)s.'),
                   {'n': node_id, 'o': self.protocol_port, 'p': self.pool})
                lb_driver.member_remove(member_id) 

    def attach(self, cluster):
        """Routine to be invoked when policy is to be attached to a cluster.

        :param cluster: The target cluster to be attached to;
        :returns: When the operation was successful, returns a tuple (True,
                  message); otherwise, return a tuple (False, error).
        """
        res, data = super(LBMemberPolicyV1, self).attach(cluster)
        if res is False:
            return False, data

        nodes = node_mod.Node.load_all(oslo_context.get_current(),
                                       cluster_id=cluster.id)

        self._add_members(cluster, nodes)

        return True, None

    def detach(self, cluster):
        """Routine to be called when the policy is detached from a cluster.

        :param cluster: The cluster from which the policy is to be detached.
        :returns: When the operation was successful, returns a tuple of
            (True, data) where the data contains references to the resources
            created; otherwise returns a tuple of (False, err) where the err
            contains a error message.
        """
        reason = _('LB member resources deletion succeeded.')

        cp = cluster_policy.ClusterPolicy.load(oslo_context.get_current(),
                                               cluster.id, self.id)

        nodes = node_mod.Node.load_all(oslo_context.get_current(),
                                       cluster_id=cluster.id)
        self._remove_members(cluster, nodes)

        return True, reason

    def post_op(self, cluster_id, action):
        """Routine to be called after an action has been executed.

        For this particular policy, we take this chance to update the pool
        maintained by the load-balancer.

        :param cluster_id: The ID of the cluster on which a relevant action
            has been executed.
        :param action: The action object that triggered this operation.
        :returns: Nothing.
        """
        nodes_added = action.outputs.get('nodes_added', [])
        nodes_removed = action.outputs.get('nodes_removed', [])
        if ((len(nodes_added) == 0) and (len(nodes_removed) == 0)):
            return

        db_cluster = db_api.cluster_get(action.context, cluster_id)
        params = self._build_conn_params(db_cluster)
        lb_driver = driver_base.SenlinDriver().loadbalancing_v1(params)

        # Remove nodes that have been deleted from lb pool
        for node_id in nodes_removed:
            node = node_mod.Node.load(action.context, node_id=node_id,
                                      show_deleted=True)
            member_id = node.data.get('lb_member', None)
            if member_id is None:
                LOG.warning(_LW('Node %(n)s not found in lb pool %(p)s.'),
                            {'n': node_id, 'p': self.pool})
                continue

            LOG.info(_LW('Remove member for node %(n)s with port %(o)s in lb pool %(p)s.'),
                {'n': node_id, 'o': self.protocol_port, 'p': self.pool})
            res = lb_driver.member_remove(member_id)
            #if res is not True:
            #    action.data['status'] = base.CHECK_ERROR
            #    action.data['reason'] = _('Failed in removing deleted '
            #                              'node(s) from lb pool.')
            #    return

        # Add new nodes to lb pool
        for node_id in nodes_added:
            node = node_mod.Node.load(action.context, node_id=node_id,
                                      show_deleted=True)
            member_id = node.data.get('lb_member', None)
            if member_id:
                LOG.warning(_LW('Node %(n)s already in lb pool %(p)s.'),
                            {'n': node_id, 'p': self.pool})
                continue

            LOG.info(_LW('Create member for node %(n)s with port %(o)s in lb pool %(p)s.'),
                {'n': node_id, 'o': self.protocol_port, 'p': self.pool})
            member_id = lb_driver.member_add(node, self.pool, self.protocol_port)
            if member_id is None:
                action.data['status'] = base.CHECK_ERROR
                action.data['reason'] = _('Failed in adding new node(s) '
                                          'into lb pool.')
                return

            node.data.update({'lb_member': member_id})
            node.store(action.context)

        return
