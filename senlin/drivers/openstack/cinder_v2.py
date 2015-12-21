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

from oslo_config import cfg
from openstack import resource

from senlin.drivers import base
from senlin.drivers.openstack import sdk


class CinderClient(base.DriverBase):
    '''Cinder V2 driver.'''

    def __init__(self, params):
        super(CinderClient, self).__init__(params)
        self.conn = sdk.create_connection(params)
        self.session = self.conn.session

    @sdk.translate_exception
    def volume_create(self, **attrs):
        server_obj = self.conn.block_store.create_volume(**attrs)
        return server_obj

    @sdk.translate_exception
    def wait_for_volume_create(self, value, status='available', failures=['error'],
                        interval=2, timeout=None):
        '''Wait for volume creation complete'''
        if timeout is None:
            timeout = cfg.CONF.default_action_timeout

        resource.wait_for_status(self.conn.block_store.session, value,
                                 status=status, failures=failures,
                                 interval=interval, wait=timeout)
        return

    @sdk.translate_exception
    def volume_get(self, value):
        return self.conn.block_store.get_volume(value)

    @sdk.translate_exception
    def volume_delete(self, value, ignore_missing=True):
        return self.conn.block_store.delete_volume(value, ignore_missing)

    @sdk.translate_exception
    def wait_for_volume_delete(self, value, timeout=None):
        '''Wait for volume deleting complete'''
        if timeout is None:
            timeout = cfg.CONF.default_action_timeout

        volume_obj = self.conn.block_store.get_volume(value)
        if server_obj:
            self.conn.block_store.wait_for_delete(volume_obj, wait=timeout)

        return

