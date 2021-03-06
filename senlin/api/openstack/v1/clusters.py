#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Cluster endpoint for Senlin v1 ReST API.
"""

from oslo_config import cfg
from oslo_log import log as logging
import six
from webob import exc

from senlin.api.openstack.v1 import util
from senlin.common import consts
from senlin.common import exception as senlin_exc
from senlin.common.i18n import _
from senlin.common import serializers
from senlin.common import utils
from senlin.common import wsgi
from senlin.rpc import client as rpc_client

LOG = logging.getLogger(__name__)


class ClusterData(object):
    '''The data accompanying a POST/PUT request to create/update a cluster.'''

    def __init__(self, data):
        self.name = data.get(consts.CLUSTER_NAME, None)
        self.profile = data.get(consts.CLUSTER_PROFILE, None)
        self.parent = data.get(consts.CLUSTER_PARENT, None)
        self.metadata = data.get(consts.CLUSTER_METADATA, None)

        self.desired_capacity = data.get(consts.CLUSTER_DESIRED_CAPACITY, None)
        self.min_size = data.get(consts.CLUSTER_MIN_SIZE, None)
        self.max_size = data.get(consts.CLUSTER_MAX_SIZE, None)
        self.timeout = data.get(consts.CLUSTER_TIMEOUT,
                                cfg.CONF.default_action_timeout)

    def _enforce_data_types(self):
        if self.desired_capacity is not None:
            self.desired_capacity = utils.parse_int_param(
                consts.CLUSTER_DESIRED_CAPACITY, self.desired_capacity,
                allow_zero=True)

        if self.min_size is not None:
            self.min_size = utils.parse_int_param(
                consts.CLUSTER_MIN_SIZE, self.min_size, allow_zero=True)

        if self.max_size is not None:
            self.max_size = utils.parse_int_param(
                consts.CLUSTER_MAX_SIZE, self.max_size, allow_zero=True,
                allow_negative=True)

        if self.timeout is not None:
            self.timeout = utils.parse_int_param(
                consts.CLUSTER_TIMEOUT, self.timeout, allow_zero=True)

    def validate_for_create(self):
        self._enforce_data_types()

        if self.name is None:
            raise exc.HTTPBadRequest(_("No cluster name specified."))

        if self.desired_capacity is None:
            raise exc.HTTPBadRequest(_("No cluster desired capacity "
                                       "provided."))

        if self.profile is None:
            raise exc.HTTPBadRequest(_("No cluster profile provided."))

        if self.min_size is not None and self.min_size > self.desired_capacity:
            msg = _("Cluster min_size, if specified, must be less than or "
                    "equal to its desired capacity.")
            raise exc.HTTPBadRequest(msg)

        if self.max_size is not None and self.max_size >= 0:
            if self.max_size < self.desired_capacity:
                msg = _("Cluster max_size, if specified, must be greater than "
                        "or equal to its desired capacity. Setting max_size "
                        "to -1 means no upper limit on cluster size.")
                raise exc.HTTPBadRequest(msg)

    def validate_for_update(self):
        self._enforce_data_types()

        if self.min_size is not None and self.desired_capacity is not None:
            if self.min_size > self.desired_capacity:
                msg = _("Cluster min_size, if specified, must be less than"
                        " or equal to its desired capacity.")
                raise exc.HTTPBadRequest(msg)

        if self.max_size is not None and self.desired_capacity is not None:
            if self.max_size >= 0 and self.max_size < self.desired_capacity:
                msg = _("Cluster max_size, if specified, must be greater than "
                        "or equal to its desired capacity. Setting max_size "
                        "to -1 means no upper limit on cluster size.")
                raise exc.HTTPBadRequest(msg)

        # The following checking is necessary because desired_capacity may
        # be not specified in an update request
        if self.min_size is not None and self.max_size is not None:
            if self.max_size >= 0 and self.max_size < self.min_size:
                msg = _("Cluster max_size, if specified, must be greater than "
                        "or equal to its min_size. Setting max_size to -1 "
                        "means no upper limit on cluster size.")
                raise exc.HTTPBadRequest(msg)


class ClusterController(object):
    '''WSGI controller for clusters resource in Senlin v1 API.'''

    # Define request scope (must match what is in policy.json)
    REQUEST_SCOPE = 'clusters'

    SUPPORTED_ACTIONS = (
        ADD_NODES, DEL_NODES, SCALE_OUT, SCALE_IN, RESIZE,
        POLICY_ATTACH, POLICY_DETACH, POLICY_UPDATE,
    ) = (
        'add_nodes', 'del_nodes', 'scale_out', 'scale_in', 'resize',
        'policy_attach', 'policy_detach', 'policy_update',
    )

    def __init__(self, options):
        self.options = options
        self.rpc_client = rpc_client.EngineClient()

    def default(self, req, **args):
        raise exc.HTTPNotFound()

    @util.policy_enforce
    def index(self, req):
        filter_whitelist = {
            'status': 'mixed',
            'name': 'mixed',
        }
        param_whitelist = {
            'limit': 'single',
            'marker': 'single',
            'sort_dir': 'single',
            'sort_keys': 'multi',
            'show_deleted': 'single',
            'show_nested': 'single',
            'global_project': 'single',
        }
        params = util.get_allowed_params(req.params, param_whitelist)
        filters = util.get_allowed_params(req.params, filter_whitelist)

        key = consts.PARAM_SHOW_DELETED
        if key in params:
            params[key] = utils.parse_bool_param(key, params[key])

        key = consts.PARAM_SHOW_NESTED
        if key in params:
            params[key] = utils.parse_bool_param(key, params[key])

        key = consts.PARAM_GLOBAL_PROJECT
        if key in params:
            project_safe = not utils.parse_bool_param(key, params[key])
            del params[key]
            params['project_safe'] = project_safe

        if not filters:
            filters = None

        clusters = self.rpc_client.cluster_list(req.context,
                                                filters=filters,
                                                **params)
        return {'clusters': clusters}

    @util.policy_enforce
    def create(self, req, body):
        '''Create a new cluster.'''

        cluster_data = body.get('cluster')
        if cluster_data is None:
            raise exc.HTTPBadRequest(_("Malformed request data, missing "
                                       "'cluster' key in request body."))

        data = ClusterData(cluster_data)
        data.validate_for_create()

        cluster = self.rpc_client.cluster_create(
            req.context, data.name, data.desired_capacity, data.profile,
            data.min_size, data.max_size, data.parent, data.metadata,
            data.timeout)

        result = {
            'cluster': cluster,
            'location': '/clusters/%s' % cluster['id'],
        }
        return result

    @util.policy_enforce
    def get(self, req, cluster_id):
        '''Gets detailed information for a cluster.'''

        cluster = self.rpc_client.cluster_get(req.context, cluster_id)
        return {'cluster': cluster}

    @util.policy_enforce
    def update(self, req, cluster_id, body):
        '''Update an existing cluster with new parameters.'''

        cluster_data = body.get('cluster')
        if cluster_data is None:
            raise exc.HTTPBadRequest(_("Malformed request data, missing "
                                       "'cluster' key in request body."))

        data = ClusterData(cluster_data)
        data.validate_for_update()

        cluster = self.rpc_client.cluster_update(
            req.context, cluster_id, data.name, data.profile, data.parent,
            data.metadata, data.timeout)

        result = {
            'cluster': cluster,
            'location': '/clusters/%s' % cluster['id'],
        }
        return result

    def _do_resize(self, req, cluster_id, this_action, body):
        data = body.get(this_action)
        adj_type = data.get(consts.ADJUSTMENT_TYPE)
        number = data.get(consts.ADJUSTMENT_NUMBER)
        min_size = data.get(consts.ADJUSTMENT_MIN_SIZE)
        max_size = data.get(consts.ADJUSTMENT_MAX_SIZE)
        min_step = data.get(consts.ADJUSTMENT_MIN_STEP)
        strict = data.get(consts.ADJUSTMENT_STRICT)
        if adj_type is not None:
            if adj_type not in consts.ADJUSTMENT_TYPES:
                raise senlin_exc.InvalidParameter(name='adjustment_type',
                                                  value=adj_type)
            if number is None:
                msg = _("Missing number value for resize operation.")
                raise exc.HTTPBadRequest(msg)

        if number is not None:
            if adj_type is None:
                msg = _("Missing adjustment_type value for resize "
                        "operation.")
                raise exc.HTTPBadRequest(msg)
            number = utils.parse_int_param('number', number,
                                           allow_negative=True)

        if min_size is not None:
            min_size = utils.parse_int_param('min_size', min_size)
        if max_size is not None:
            max_size = utils.parse_int_param('max_size', max_size,
                                             allow_negative=True)
        if (min_size is not None and max_size is not None and
                max_size > 0 and min_size > max_size):
            msg = _("The specified min_size (%(n)s) is greater than the "
                    "specified max_size (%(m)s).") % {'m': max_size,
                                                      'n': min_size}
            raise exc.HTTPBadRequest(msg)

        if min_step is not None:
            min_step = utils.parse_int_param('min_step', min_step)
        if strict is not None:
            strict = utils.parse_bool_param('strict', strict)

        result = self.rpc_client.cluster_resize(req.context, cluster_id,
                                                adj_type, number, min_size,
                                                max_size, min_step, strict)
        location = {'location': '/actions/%s' % result['action']}
        result.update(location)
        return result

    def _sanitize_policy(self, data):
        """Validate dict body of policy attach or update.

        :param dict data: A dictionary containing the properties of the policy
                          to be attached/updated including the policy ID.
        :returns: A sanitized dict containing the policy properties.
        :raises: :class:`~webob.exception.HTTPBadRequest` if the policy dict
                 contains invalid property values.
        """
        if not isinstance(data, dict):
            msg = _("The data provided is not a map.")
            raise exc.HTTPBadRequest(msg)

        if consts.CP_POLICY_ID not in data:
            msg = _("The 'policy_id' field is missing in the request.")
            raise exc.HTTPBadRequest(msg)

        if consts.CP_PRIORITY in data:
            pri = data.get(consts.CP_PRIORITY)
            try:
                pri = utils.parse_int_param('priority', pri, lower_limit=0,
                                            upper_limit=100)
            except senlin_exc.InvalidParameter as ex:
                raise exc.HTTPBadRequest(six.text_type(ex))

            data[consts.CP_PRIORITY] = pri

        if consts.CP_LEVEL in data:
            lvl = data.get(consts.CP_LEVEL)
            try:
                lvl = utils.parse_int_param('level', lvl, lower_limit=0,
                                            upper_limit=100)
            except senlin_exc.InvalidParameter as ex:
                raise exc.HTTPBadRequest(six.text_type(ex))
            data[consts.CP_LEVEL] = lvl

        if consts.CP_COOLDOWN in data:
            cdn = data.get(consts.CP_COOLDOWN)
            try:
                cdn = utils.parse_int_param('cooldown', cdn, lower_limit=0)
            except senlin_exc.InvalidParameter as ex:
                raise exc.HTTPBadRequest(six.text_type(ex))
            data[consts.CP_COOLDOWN] = cdn

        if consts.CP_ENABLED in data:
            enb = data.get(consts.CP_ENABLED)
            try:
                enb = utils.parse_bool_param('enabled', enb)
            except senlin_exc.InvalidParameter as ex:
                raise exc.HTTPBadRequest(six.text_type(ex))
            data[consts.CP_ENABLED] = enb

        return data

    @util.policy_enforce
    def action(self, req, cluster_id, body=None):
        '''Perform specified action on a cluster.'''
        body = body or {}
        if len(body) < 1:
            raise exc.HTTPBadRequest(_('No action specified'))

        if len(body) > 1:
            raise exc.HTTPBadRequest(_('Multiple actions specified'))

        this_action = list(body.keys())[0]
        if this_action not in self.SUPPORTED_ACTIONS:
            msg = _("Unrecognized action '%s' specified") % this_action
            raise exc.HTTPBadRequest(msg)

        if this_action == self.ADD_NODES:
            nodes = body.get(this_action).get('nodes')
            if nodes is None or not isinstance(nodes, list) or len(nodes) == 0:
                raise exc.HTTPBadRequest(_('No node to add'))
            res = self.rpc_client.cluster_add_nodes(
                req.context, cluster_id, nodes)
        elif this_action == self.DEL_NODES:
            nodes = body.get(this_action).get('nodes')
            if nodes is None or not isinstance(nodes, list) or len(nodes) == 0:
                raise exc.HTTPBadRequest(_('No node to delete'))
            res = self.rpc_client.cluster_del_nodes(
                req.context, cluster_id, nodes)
        elif this_action == self.RESIZE:
            return self._do_resize(req, cluster_id, this_action, body)
        elif this_action == self.SCALE_OUT:
            count = body.get(this_action).get('count')
            res = self.rpc_client.cluster_scale_out(req.context, cluster_id,
                                                    count)
        elif this_action == self.SCALE_IN:
            count = body.get(this_action).get('count')
            res = self.rpc_client.cluster_scale_in(req.context, cluster_id,
                                                   count)
        elif this_action == self.POLICY_ATTACH:
            raw_data = body.get(this_action)
            data = self._sanitize_policy(raw_data)
            res = self.rpc_client.cluster_policy_attach(req.context,
                                                        cluster_id,
                                                        **data)
        elif this_action == self.POLICY_DETACH:
            data = body.get(this_action)
            policy_id = data.get('policy_id', None)
            if not policy_id:
                raise exc.HTTPBadRequest(_('No policy specified for detach.'))
            res = self.rpc_client.cluster_policy_detach(req.context,
                                                        cluster_id, policy_id)
        else:
            # this_action == self.POLICY_UPDATE:
            # Note the POLICY_UPDATE action includes policy-enable/disable
            raw_data = body.get(this_action)
            data = self._sanitize_policy(raw_data)
            res = self.rpc_client.cluster_policy_update(req.context,
                                                        cluster_id, **data)
        location = {'location': '/actions/%s' % res['action']}
        res.update(location)
        return res

    @util.policy_enforce
    def delete(self, req, cluster_id):
        cluster = self.rpc_client.cluster_delete(req.context,
                                                 cluster_id,
                                                 cast=False)
        result = {'location': '/clusters/%s' % cluster}
        return result


def create_resource(options):
    '''Clusters resource factory method.'''

    return wsgi.Resource(ClusterController(options),
                         wsgi.JSONRequestDeserializer(),
                         serializers.JSONResponseSerializer())
