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

from oslo_log import log

from senlin.drivers import base
from senlin.drivers.openstack import sdk

LOG = log.getLogger(__name__)


class NeutronClient(base.DriverBase):
    '''Neutron V2 driver.'''

    def __init__(self, params):
        super(NeutronClient, self).__init__(params)
        self.conn = sdk.create_connection(params)

    def network_get(self, name_or_id):
        network = self.conn.network.find_network(name_or_id)
        return network

    def subnet_get(self, name_or_id):
        subnet = self.conn.network.find_subnet(name_or_id)
        return subnet

    def loadbalancer_get(self, name_or_id):
        lb = self.conn.network.find_load_balancer(name_or_id)
        return lb

    def loadbalancer_list(self):
        lbs = [lb for lb in self.conn.network.load_balancers()]
        return lbs

    def loadbalancer_create(self, vip_subnet_id, vip_address=None,
                            admin_state_up=True, name=None, description=None):

        kwargs = {
            'vip_subnet_id': vip_subnet_id,
            'admin_state_up': admin_state_up,
        }

        if vip_address is not None:
            kwargs['vip_address'] = vip_address
        if name is not None:
            kwargs['name'] = name
        if description is not None:
            kwargs['description'] = description

        res = self.conn.network.create_load_balancer(**kwargs)
        return res

    def loadbalancer_delete(self, lb_id, ignore_missing=True):
        self.conn.network.delete_load_balancer(
            lb_id, ignore_missing=ignore_missing)
        return

    def listener_get(self, name_or_id):
        listener = self.conn.network.find_listener(name_or_id)
        return listener

    def listener_list(self):
        listeners = [i for i in self.conn.network.listeners()]
        return listeners

    def listener_create(self, loadbalancer_id, protocol, protocol_port,
                        connection_limit=None,
                        admin_state_up=True, name=None, description=None):

        kwargs = {
            'loadbalancer_id': loadbalancer_id,
            'protocol': protocol,
            'protocol_port': protocol_port,
            'admin_state_up': admin_state_up,
        }

        if connection_limit is not None:
            kwargs['connection_limit'] = connection_limit
        if name is not None:
            kwargs['name'] = name
        if description is not None:
            kwargs['description'] = description

        res = self.conn.network.create_listener(**kwargs)
        return res

    def listener_delete(self, listener_id, ignore_missing=True):
        self.conn.network.delete_listener(listener_id,
                                          ignore_missing=ignore_missing)
        return

    def pool_get(self, name_or_id):
        pool = self.conn.network.find_pool(name_or_id)
        return pool

    def pool_list(self):
        pools = [p for p in self.conn.network.pools()]
        return pools

    def pool_create(self, lb_algorithm, listener_id, protocol,
                    admin_state_up=True, name=None, description=None):

        kwargs = {
            'lb_algorithm': lb_algorithm,
            'listener_id': listener_id,
            'protocol': protocol,
            'admin_state_up': admin_state_up,
        }

        if name is not None:
            kwargs['name'] = name
        if description is not None:
            kwargs['description'] = description

        res = self.conn.network.create_pool(**kwargs)
        return res

    def pool_delete(self, pool_id, ignore_missing=True):
        self.conn.network.delete_pool(pool_id,
                                      ignore_missing=ignore_missing)
        return

    def pool_member_get(self, pool_id, name_or_id):
        member = self.conn.network.find_pool_member(name_or_id,
                                                    pool_id)
        return member

    def pool_member_list(self, pool_id):
        members = [m for m in self.conn.network.pool_members(pool_id)]
        return members

    def pool_member_create(self, pool_id, address, protocol_port, subnet_id,
                           weight=None, admin_state_up=True):

        kwargs = {
            'pool_id': pool_id,
            'address': address,
            'protocol_port': protocol_port,
            'admin_state_up': admin_state_up,
            'subnet_id': subnet_id,
        }

        if weight is not None:
            kwargs['weight'] = weight

        res = self.conn.network.create_pool_member(**kwargs)
        return res

    def pool_member_delete(self, pool_id, member_id, ignore_missing=True):
        self.conn.network.delete_pool_member(
            member_id, pool_id, ignore_missing=ignore_missing)
        return

    def healthmonitor_get(self, name_or_id):
        hm = self.conn.network.find_health_monitor(name_or_id)
        return hm

    def healthmonitor_list(self):
        hms = [hm for hm in self.conn.network.health_monitors()]
        return hms

    def healthmonitor_create(self, hm_type, delay, timeout, max_retries,
                             pool_id, admin_state_up=True,
                             http_method=None, url_path=None,
                             expected_codes=None):
        kwargs = {
            'type': hm_type,
            'delay': delay,
            'timeout': timeout,
            'max_retries': max_retries,
            'pool_id': pool_id,
            'admin_state_up': admin_state_up,
        }

        # TODO(anyone): verify if this is correct
        if hm_type == 'HTTP':
            if http_method is not None:
                kwargs['http_method'] = http_method
            if url_path is not None:
                kwargs['url_path'] = url_path
            if expected_codes is not None:
                kwargs['expected_codes'] = expected_codes

        res = self.conn.network.create_health_monitor(**kwargs)
        return res

    def healthmonitor_delete(self, hm_id, ignore_missing=True):
        self.conn.network.delete_health_monitor(
            hm_id, ignore_missing=ignore_missing)
        return
