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

import mock

from senlin.drivers.openstack import heat_v1
from senlin.drivers.openstack import sdk
from senlin.tests.unit.common import base
from senlin.tests.unit.common import utils


class TestHeatV1(base.SenlinTestCase):

    def setUp(self):
        super(TestHeatV1, self).setUp()

        self.context = utils.dummy_context()
        self.conn_params = self.context.to_dict()
        self.mock_conn = mock.Mock()
        self.mock_create = self.patchobject(sdk, 'create_connection',
                                            return_value=self.mock_conn)
        self.orch = self.mock_conn.orchestration
        self.hc = heat_v1.HeatClient(self.conn_params)

    def test_init(self):
        self.mock_create.assert_called_once_with(self.conn_params)
        self.assertEqual(self.mock_conn, self.hc.conn)

    def test_stack_create(self):
        fake_params = {
            'disable_rollback': True,
            'stack_name': 'fake_stack',
            }
        self.hc.stack_create(**fake_params)
        self.orch.create_stack.assert_called_once_with(**fake_params)

    def test_stack_get(self):
        self.hc.stack_get('stack_id')
        self.orch.get_stack.assert_called_once_with('stack_id')

    def test_stack_find(self):
        self.hc.stack_find('name_or_id')
        self.orch.find_stack.assert_called_once_with('name_or_id')

    def test_stack_list(self):
        self.hc.stack_list()
        self.orch.stacks.assert_called_once_with()

    def test_stack_update(self):
        fake_params = {
            "name": "new_name",
        }
        self.hc.stack_update('stack_id', **fake_params)
        self.orch.update_stack.assert_called_once_with('stack_id',
                                                       **fake_params)

    def test_stack_delete(self):
        self.hc.stack_delete('stack_id', ignore_missing=True)
        self.orch.delete_stack.assert_called_once_with('stack_id', True)
