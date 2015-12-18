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

import eventlet
import six

from oslo_context import context as oslo_context
from oslo_log import log as logging

from senlin.common import exception
from senlin.common.i18n import _
from senlin.common.i18n import _LE
from senlin.drivers import base
from senlin.drivers.openstack import neutron_v2 as neutronclient
from senlin.engine import event

LOG = logging.getLogger(__name__)


class LoadBalancerV1Driver(base.DriverBase):
    """Load-balancing driver based on Neutron LBaaS service."""

    def __init__(self, params):
        super(LoadBalancerV1Driver, self).__init__(params)
        self._nc = None

    def nc(self):
        if self._nc:
            return self._nc

        self._nc = neutronclient.NeutronClient(self.conn_params)
        return self._nc

    def member_add(self, node, pool_id, port):
        """Add a member to Neutron lbaas pool.

        :param node: A node object to be added to the specified pool.
        :param pool_id: The ID of the pool for receiving the node.
        :param port: The port for the new LB member to be created.
        :returns: The ID of the new LB member or None if errors occurred.
        """
        addresses = self._get_node_address(node, version=4)
        if not addresses:
            LOG.error(_LE('Node (%(n)s) does not have valid IPv4 address.'),
                      {'n': node.id})
            return None

        pool_obj = self.nc().pool_v1_get(pool_id)
        if pool_obj is None:
            LOG.error(_LE('Failed in getting Pool (%(n)s .'), {'n': pool_id})

        try:
            subnet_obj = self.nc().subnet_get(pool_obj.subnet_id)
            net_id = subnet_obj.network_id
            net = self.nc().network_get(net_id)
        except exception.InternalError as ex:
            resource = 'subnet' if subnet in ex.message else 'network'
            msg = _LE('Failed in getting %(resource)s: %(msg)s.'
                      ) % {'resource': resource, 'msg': six.text_type(ex)}
            LOG.exception(msg)
            event.warning(oslo_context.get_current(), self,
                          resource.upper()+'_GET', 'ERROR', msg)
            return None

        address = addresses.get(net.name, addresses.popitem()[1])
        try:
            member = self.nc().pool_member_v1_create(pool_id, address, port)
        except exception.InternalError as ex:
            msg = _LE('Failed in creating lb pool member: %s.'
                      ) % six.text_type(ex)
            LOG.exception(msg)
            event.warning(oslo_context.get_current(), self,
                          'POOL_MEMBER_CREATE', 'ERROR', msg)
            return None

        return member.id

    def member_remove(self, member_id):
        """Delete a member from Neutron lbaas pool.

        :param member_id: The ID of the LB member.
        :returns: True if the operation succeeded or False if errors occurred.
        """
        try:
            self.nc().pool_member_v1_delete(member_id)
        except exception.InternalError as ex:
            msg = _LE('Failed in removing member %(m)s:'
                      '%(ex)s') % {'m': member_id, 'ex': six.text_type(ex)}
            LOG.exception(msg)
            event.warning(oslo_context.get_current(), self,
                          'POOL_MEMBER_DELETE', 'ERROR', msg)
            return None

        return True

    def _get_node_address(self, node, version=4):
        """Get IP address of node with specific version"""

        node_detail = node.get_details(oslo_context.get_current())
        node_addresses = node_detail.get('addresses', {})

        address = {}

        for network in node_addresses:
            for addr in node_addresses[network]:
                if addr['version'] == version:
                    address[network] = addr['addr']

        return address
