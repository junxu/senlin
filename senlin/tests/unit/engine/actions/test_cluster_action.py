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

from senlin.common import exception
from senlin.common import scaleutils
from senlin.db.sqlalchemy import api as db_api
from senlin.engine.actions import base as base_action
from senlin.engine.actions import cluster_action as ca
from senlin.engine import cluster as cluster_mod
from senlin.engine import dispatcher
from senlin.engine import event as event_mod
from senlin.engine import node as node_mod
from senlin.engine import scheduler
from senlin.engine import senlin_lock
from senlin.policies import base as policy_base
from senlin.tests.unit.common import base
from senlin.tests.unit.common import utils


class ClusterActionWaitTest(base.SenlinTestCase):
    scenarios = [
        ('wait_ready', dict(
            statuses=[
                base_action.Action.WAITING,
                base_action.Action.READY
            ],
            cancelled=[False, False],
            timeout=[False, False],
            failed=[False, False],
            code=base_action.Action.RES_OK,
            rescheduled_times=1,
            message='All dependents ended with success')
         ),
        ('wait_fail', dict(
            statuses=[
                base_action.Action.WAITING,
                base_action.Action.FAILED
            ],
            cancelled=[False, False],
            timeout=[False, False],
            code=base_action.Action.RES_ERROR,
            rescheduled_times=1,
            message='ACTION [FAKE_ID] failed')
         ),
        ('wait_wait_cancel', dict(
            statuses=[
                base_action.Action.WAITING,
                base_action.Action.WAITING,
                base_action.Action.WAITING,
            ],
            cancelled=[False, False, True],
            timeout=[False, False, False],
            code=base_action.Action.RES_CANCEL,
            rescheduled_times=2,
            message='ACTION [FAKE_ID] cancelled')
         ),
        ('wait_wait_timeout', dict(
            statuses=[
                base_action.Action.WAITING,
                base_action.Action.WAITING,
                base_action.Action.WAITING,
            ],
            cancelled=[False, False, False],
            timeout=[False, False, True],
            code=base_action.Action.RES_TIMEOUT,
            rescheduled_times=2,
            message='ACTION [FAKE_ID] timeout')
         ),

    ]

    def setUp(self):
        super(ClusterActionWaitTest, self).setUp()
        self.ctx = utils.dummy_context()

    @mock.patch.object(cluster_mod.Cluster, 'load')
    @mock.patch.object(scheduler, 'reschedule')
    def test_wait_dependents(self, mock_reschedule, mock_load):
        action = ca.ClusterAction('ID', 'ACTION', self.ctx)
        action.id = 'FAKE_ID'
        self.patchobject(action, 'get_status', side_effect=self.statuses)
        self.patchobject(action, 'is_cancelled', side_effect=self.cancelled)
        self.patchobject(action, 'is_timeout', side_effect=self.timeout)

        res_code, res_msg = action._wait_for_dependents()
        self.assertEqual(self.code, res_code)
        self.assertEqual(self.message, res_msg)
        self.assertEqual(self.rescheduled_times, mock_reschedule.call_count)


@mock.patch.object(cluster_mod.Cluster, 'load')
class ClusterActionTest(base.SenlinTestCase):

    def setUp(self):
        super(ClusterActionTest, self).setUp()
        self.ctx = utils.dummy_context()

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'cluster_next_index')
    @mock.patch.object(node_mod, 'Node')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__create_nodes_single(self, mock_wait, mock_start, mock_dep,
                                  mock_node, mock_index, mock_update,
                                  mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        cluster.profile_id = 'FAKE_PROFILE'
        cluster.user = 'FAKE_USER'
        cluster.project = 'FAKE_PROJECT'
        cluster.domain = 'FAKE_DOMAIN'
        mock_index.return_value = 123
        node = mock.Mock()
        node.id = 'NODE_ID'
        mock_node.return_value = node

        mock_load.return_value = cluster
        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        mock_wait.return_value = (action.RES_OK, 'All dependents completed')

        # node_action is faked
        n_action = mock.Mock()
        n_action.id = 'NODE_ACTION_ID'
        mock_action = self.patchobject(base_action, 'Action',
                                       return_value=n_action)

        # do it
        res_code, res_msg = action._create_nodes(1)

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('All dependents completed', res_msg)
        mock_index.assert_called_once_with(action.context, 'CLUSTER_ID')
        mock_node.assert_called_once_with('node-CLUSTER_-123',
                                          'FAKE_PROFILE',
                                          'CLUSTER_ID',
                                          context=action.context,
                                          user='FAKE_USER',
                                          project='FAKE_PROJECT',
                                          domain='FAKE_DOMAIN',
                                          index=123, metadata={})
        node.store.assert_called_once_with(action.context)
        mock_action.assert_called_once_with('NODE_ID', 'NODE_CREATE',
                                            user=self.ctx.user,
                                            project=self.ctx.project,
                                            domain=self.ctx.domain,
                                            name='node_create_NODE_ID',
                                            cause='Derived Action')
        mock_dep.assert_called_once_with(action.context, 'NODE_ACTION_ID',
                                         'CLUSTER_ACTION_ID')
        mock_update.assert_called_once_with(
            action.context, n_action.id,
            {'status': n_action.READY})
        mock_start.assert_called_once_with(action_id='NODE_ACTION_ID')
        mock_wait.assert_called_once_with()
        self.assertEqual({'nodes_added': ['NODE_ID']}, action.outputs)

    @mock.patch.object(db_api, 'cluster_get')
    def test_create_nodes_zero(self, mock_get, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_get.return_value = mock.Mock()
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)

        res_code, res_msg = action._create_nodes(0)

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('', res_msg)

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'cluster_next_index')
    @mock.patch.object(node_mod, 'Node')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__create_nodes_multiple(self, mock_wait, mock_start, mock_dep,
                                    mock_node, mock_index, mock_update,
                                    mock_load):
        cluster = mock.Mock()
        cluster.id = '01234567-123434'
        node1 = mock.Mock()
        node1.id = '01234567-abcdef'
        node1.data = {'placement': {'region': 'regionOne'}}
        node2 = mock.Mock()
        node2.id = 'abcdefab-123456'
        node2.data = {'placement': {'region': 'regionTwo'}}
        mock_node.side_effect = [node1, node2]
        mock_index.side_effect = [123, 124]

        mock_load.return_value = cluster
        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.data = {
            'placement': {
                'count': 2,
                'placements': [
                    {'region': 'regionOne'},
                    {'region': 'regionTwo'}
                ]
            }
        }
        mock_wait.return_value = (action.RES_OK, 'All dependents completed')

        # node_action is faked
        node_action_1 = mock.Mock()
        node_action_2 = mock.Mock()
        mock_action = self.patchobject(
            base_action, 'Action', side_effect=[node_action_1, node_action_2])

        # do it
        res_code, res_msg = action._create_nodes(2)

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('All dependents completed', res_msg)
        self.assertEqual(2, mock_index.call_count)
        self.assertEqual(2, mock_node.call_count)
        node1.store.assert_called_once_with(action.context)
        node2.store.assert_called_once_with(action.context)
        self.assertEqual(2, mock_action.call_count)
        self.assertEqual(2, mock_dep.call_count)

        update_calls = [
            mock.call(action.context, node_action_1.id,
                      {'status': node_action_1.READY}),
            mock.call(action.context, node_action_2.id,
                      {'status': node_action_2.READY})
        ]
        mock_update.assert_has_calls(update_calls)
        self.assertEqual(2, mock_start.call_count)
        mock_wait.assert_called_once_with()
        self.assertEqual({'nodes_added': [node1.id, node2.id]}, action.outputs)
        self.assertEqual({'region': 'regionOne'}, node1.data['placement'])
        self.assertEqual({'region': 'regionTwo'}, node2.data['placement'])
        mock_node_calls = [
            mock.call('node-01234567-123', mock.ANY, '01234567-123434',
                      user=mock.ANY, project=mock.ANY, domain=mock.ANY,
                      index=123, context=mock.ANY, metadata={},
                      data={'placement': {'region': 'regionOne'}}),
            mock.call('node-01234567-124', mock.ANY, '01234567-123434',
                      user=mock.ANY, project=mock.ANY, domain=mock.ANY,
                      index=124, context=mock.ANY, metadata={},
                      data={'placement': {'region': 'regionTwo'}})
        ]

        mock_node.assert_has_calls(mock_node_calls)
        cluster.add_node.assert_has_calls([
            mock.call(node1), mock.call(node2)])

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'cluster_get')
    @mock.patch.object(node_mod, 'Node')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__create_nodes_multiple_failed_wait(self, mock_wait, mock_start,
                                                mock_dep, mock_node, mock_get,
                                                mock_update, mock_load):
        cluster = mock.Mock()
        cluster.id = '01234567-123434'
        db_cluster = mock.Mock()
        db_cluster.next_index = 1
        mock_get.return_value = db_cluster
        node1 = mock.Mock()
        node1.id = '01234567-abcdef'
        node1.data = {}
        node2 = mock.Mock()
        node2.id = 'abcdefab-123456'
        node2.data = {}
        mock_node.side_effect = [node1, node2]

        mock_load.return_value = cluster
        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.data = {
            'placement': {
                'count': 2,
                'placements': [
                    {'region': 'regionOne'},
                    {'region': 'regionTwo'}
                ]
            }
        }
        mock_wait.return_value = (action.RES_ERROR, 'Waiting timed out')

        # node_action is faked
        n_action_1 = mock.Mock()
        n_action_2 = mock.Mock()
        self.patchobject(base_action, 'Action',
                         side_effect=[n_action_1, n_action_2])

        # do it
        res_code, res_msg = action._create_nodes(2)

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Waiting timed out', res_msg)

    def test_do_create_success(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.do_create.return_value = True
        cluster.set_status = mock.Mock()
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)

        x_create_nodes = self.patchobject(action, '_create_nodes',
                                          return_value=(action.RES_OK, 'OK'))
        # do it
        res_code, res_msg = action.do_create()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster creation succeeded.', res_msg)
        x_create_nodes.assert_called_once_with(cluster.desired_capacity)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster creation succeeded.')

    def test_do_create_failed_create_cluster(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.do_create.return_value = False
        cluster.set_status = mock.Mock()
        cluster.ERROR = 'ERROR'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)

        # do it
        res_code, res_msg = action.do_create()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Cluster creation failed.', res_msg)
        cluster.set_status.assert_called_once_with(
            action.context, 'ERROR', 'Cluster creation failed.')

    def test_do_create_failed_create_nodes(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_ID'
        cluster.do_create.return_value = True
        cluster.set_status = mock.Mock()
        cluster.ERROR = 'ERROR'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)

        # do it
        for code in [action.RES_CANCEL, action.RES_TIMEOUT, action.RES_ERROR]:
            self.patchobject(action, '_create_nodes',
                             return_value=(code, 'Really Bad'))

            res_code, res_msg = action.do_create()

            self.assertEqual(code, res_code)
            self.assertEqual('Really Bad', res_msg)
            cluster.set_status.assert_called_once_with(
                action.context, 'ERROR', 'Really Bad')
            cluster.set_status.reset_mock()

    def test_do_create_failed_for_retry(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_ID'
        cluster.INIT = 'INIT'
        cluster.do_create.return_value = True
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        self.patchobject(action, '_create_nodes',
                         return_value=(action.RES_RETRY, 'retry'))

        # do it
        res_code, res_msg = action.do_create()

        self.assertEqual(action.RES_RETRY, res_code)
        self.assertEqual('retry', res_msg)
        cluster.set_status.assert_called_once_with(action.context, 'INIT')

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test_do_update_multi(self, mock_wait, mock_start, mock_dep,
                             mock_update, mock_load):
        node1 = mock.Mock()
        node1.id = 'fake id 1'
        node2 = mock.Mock()
        node2.id = 'fake id 2'
        cluster = mock.Mock()
        cluster.id = 'FAKE_ID'
        cluster.nodes = [node1, node2]
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'new_profile_id': 'FAKE_PROFILE'}

        n_action_1 = mock.Mock()
        n_action_2 = mock.Mock()
        mock_action = self.patchobject(base_action, 'Action',
                                       side_effect=[n_action_1, n_action_2])
        mock_wait.return_value = (action.RES_OK, 'OK')

        # do it
        res_code, res_msg = action.do_update()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster update completed.', res_msg)
        self.assertEqual(2,  mock_action.call_count)
        n_action_1.store.assert_called_once_with(action.context)
        n_action_2.store.assert_called_once_with(action.context)
        self.assertEqual(2, mock_dep.call_count)
        update_calls = [
            mock.call(action.context, n_action_1.id,
                      {'status': n_action_1.READY}),
            mock.call(action.context, n_action_2.id,
                      {'status': n_action_2.READY})
        ]
        mock_update.assert_has_calls(update_calls)

        self.assertEqual(2, mock_start.call_count)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster update completed.',
            profile_id='FAKE_PROFILE')

    def test_do_update_empty_cluster(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_ID'
        cluster.nodes = []
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'new_profile_id': 'FAKE_PROFILE'}

        # do it
        res_code, res_msg = action.do_update()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster update completed.', res_msg)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster update completed.',
            profile_id='FAKE_PROFILE')

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test_do_update_failed_wait(self, mock_wait, mock_start, mock_dep,
                                   mock_update, mock_load):
        node = mock.Mock()
        node.id = 'fake node id'
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.nodes = [node]
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'new_profile_id': 'FAKE_PROFILE'}

        n_action = mock.Mock()
        mock_action = self.patchobject(base_action, 'Action',
                                       return_value=n_action)
        mock_wait.return_value = (action.RES_TIMEOUT, 'Timeout')

        # do it
        res_code, res_msg = action.do_update()

        # assertions
        self.assertEqual(action.RES_TIMEOUT, res_code)
        self.assertEqual('Timeout', res_msg)
        self.assertEqual(1,  mock_action.call_count)
        n_action.store.assert_called_once_with(action.context)
        self.assertEqual(1, mock_dep.call_count)
        mock_update.assert_called_once_with(
            action.context, n_action.id, {'status': n_action.READY})
        self.assertEqual(1, mock_start.call_count)
        mock_wait.assert_called_once_with()

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__delete_nodes_single(self, mock_wait, mock_start, mock_dep,
                                  mock_update, mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        # cluster action is real
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        mock_wait.return_value = (action.RES_OK, 'All dependents completed')

        # n_action is faked
        n_action = mock.Mock()
        n_action.id = 'NODE_ACTION_ID'
        mock_action = self.patchobject(base_action, 'Action',
                                       return_value=n_action)
        # do it
        res_code, res_msg = action._delete_nodes(['NODE_ID'])

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('All dependents completed', res_msg)
        mock_action.assert_called_once_with(
            'NODE_ID', 'NODE_DELETE', user=self.ctx.user,
            project=self.ctx.project, domain=self.ctx.domain,
            name='node_delete_NODE_ID', cause='Derived Action')
        n_action.store.assert_called_once_with(action.context)
        mock_dep.assert_called_once_with(action.context, 'NODE_ACTION_ID',
                                         'CLUSTER_ACTION_ID')
        mock_update.assert_called_once_with(
            action.context, n_action.id, {'status': n_action.READY})
        mock_start.assert_called_once_with(action_id='NODE_ACTION_ID')
        mock_wait.assert_called_once_with()
        self.assertEqual(['NODE_ID'], action.outputs['nodes_removed'])
        cluster.remove_node.assert_called_once_with('NODE_ID')

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__delete_nodes_multi(self, mock_wait, mock_start, mock_dep,
                                 mock_update, mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster

        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        mock_wait.return_value = (action.RES_OK, 'All dependents completed')

        # node_action is faked
        n_action_1 = mock.Mock()
        n_action_1.id = 'NODE_ACTION_1'
        n_action_2 = mock.Mock()
        n_action_2.id = 'NODE_ACTION_1'
        mock_action = self.patchobject(base_action, 'Action',
                                       side_effect=[n_action_1, n_action_2])
        # do it
        res_code, res_msg = action._delete_nodes(['NODE_1', 'NODE_2'])

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('All dependents completed', res_msg)
        self.assertEqual(2, mock_action.call_count)
        n_action_1.store.assert_called_once_with(action.context)
        n_action_2.store.assert_called_once_with(action.context)
        update_calls = [
            mock.call(action.context, n_action_1.id,
                      {'status': n_action_1.READY}),
            mock.call(action.context, n_action_2.id,
                      {'status': n_action_2.READY})
        ]
        mock_update.assert_has_calls(update_calls)
        self.assertEqual(2, mock_dep.call_count)
        self.assertEqual(2, mock_start.call_count)
        mock_wait.assert_called_once_with()
        self.assertEqual({'nodes_removed': ['NODE_1', 'NODE_2']},
                         action.outputs)
        cluster.remove_node.assert_has_calls([
            mock.call('NODE_1'), mock.call('NODE_2')])

    def test__delete_empty(self, mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)

        # do it
        res_code, res_msg = action._delete_nodes([])

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('', res_msg)

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__delete_nodes_with_pd(self, mock_wait, mock_start, mock_dep,
                                   mock_update, mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster
        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.data = {
            'deletion': {
                'destroy_after_deletion': False
            }
        }
        mock_wait.return_value = (action.RES_OK, 'All dependents completed')

        # n_action is faked
        n_action = mock.Mock()
        n_action.id = 'NODE_ACTION_ID'
        mock_action = self.patchobject(base_action, 'Action',
                                       return_value=n_action)
        # do it
        res_code, res_msg = action._delete_nodes(['NODE_ID'])

        # assertions (other assertions are skipped)
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('All dependents completed', res_msg)
        mock_action.assert_called_once_with(
            'NODE_ID', 'NODE_LEAVE', user=self.ctx.user,
            project=self.ctx.project, domain=self.ctx.domain,
            name='node_delete_NODE_ID', cause='Derived Action')

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test__delete_nodes_failed_wait(self, mock_wait, mock_start, mock_dep,
                                       mock_update, mock_load):
        # prepare mocks
        cluster = mock.Mock()
        cluster.id = 'ID'
        mock_load.return_value = cluster
        # cluster action is real
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.data = {}
        mock_wait.return_value = (action.RES_TIMEOUT, 'Timeout!')

        # n_action is faked
        n_action = mock.Mock()
        n_action.id = 'NODE_ACTION_ID'
        self.patchobject(base_action, 'Action', return_value=n_action)

        # do it
        res_code, res_msg = action._delete_nodes(['NODE_ID'])

        # assertions (other assertions are skipped)
        self.assertEqual(action.RES_TIMEOUT, res_code)
        self.assertEqual('Timeout!', res_msg)
        self.assertEqual({}, action.data)

    def test_do_delete_success(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        node1 = mock.Mock()
        node1.id = 'NODE_1'
        node2 = mock.Mock()
        node2.id = 'NODE_2'

        cluster.nodes = [node1, node2]
        cluster.DELETING = 'DELETING'
        cluster.do_delete.return_value = True

        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.data = {}

        mock_delete = self.patchobject(action, '_delete_nodes',
                                       return_value=(action.RES_OK, 'Good'))

        # do it
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Good', res_msg)
        self.assertEqual({'deletion': {'destroy_after_deletion': True}},
                         action.data)
        cluster.set_status.assert_called_once_with(action.context, 'DELETING',
                                                   'Deletion in progress.')
        mock_delete.assert_called_once_with(['NODE_1', 'NODE_2'])
        cluster.do_delete.assert_called_once_with(action.context)

    def test_do_delete_failed_delete_nodes(self, mock_load):
        node = mock.Mock()
        node.id = 'NODE_1'
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.nodes = [node]
        cluster.ACTIVE = 'ACTIVE'
        cluster.DELETING = 'DELETING'
        cluster.WARNING = 'WARNING'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.data = {}

        # timeout
        self.patchobject(action, '_delete_nodes',
                         return_value=(action.RES_TIMEOUT, 'Timeout!'))
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_TIMEOUT, res_code)
        self.assertEqual('Timeout!', res_msg)
        cluster.set_status.assert_has_calls([
            mock.call(action.context, 'DELETING', 'Deletion in progress.'),
            mock.call(action.context, 'WARNING', 'Timeout!')])
        cluster.set_status.reset_mock()

        # error
        self.patchobject(action, '_delete_nodes',
                         return_value=(action.RES_ERROR, 'Error!'))
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Error!', res_msg)
        cluster.set_status.assert_has_calls([
            mock.call(action.context, 'DELETING', 'Deletion in progress.'),
            mock.call(action.context, 'WARNING', 'Error!')])
        cluster.set_status.reset_mock()

        # cancel
        self.patchobject(action, '_delete_nodes',
                         return_value=(action.RES_CANCEL, 'Cancelled!'))
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_CANCEL, res_code)
        self.assertEqual('Cancelled!', res_msg)
        cluster.set_status.assert_has_calls([
            mock.call(action.context, 'DELETING', 'Deletion in progress.'),
            mock.call(action.context, 'ACTIVE', 'Cancelled!')])

        # retry
        self.patchobject(action, '_delete_nodes',
                         return_value=(action.RES_RETRY, 'Busy!'))
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_RETRY, res_code)
        self.assertEqual('Busy!', res_msg)

    def test_do_delete_failed_delete_cluster(self, mock_load):
        node = mock.Mock()
        node.id = 'NODE_1'
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.nodes = [node]
        cluster.DELETING = 'DELETING'
        cluster.do_delete.return_value = False
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.data = {}

        self.patchobject(action, '_delete_nodes',
                         return_value=(action.RES_OK, 'Good'))
        # do it
        res_code, res_msg = action.do_delete()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Cannot delete cluster object.', res_msg)

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test_do_add_nodes_single(self, mock_wait, mock_start, mock_dep,
                                 mock_load_node, mock_update, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.inputs = {'nodes': ['NODE_1']}
        action.data = {}
        action.outputs = {}

        node = mock.Mock()
        node.id = 'NODE_1'
        node.cluster_id = None
        node.status = node.ACTIVE
        mock_load_node.return_value = node

        node_action = mock.Mock()
        node_action.id = 'NODE_ACTION_ID'
        mock_action = self.patchobject(base_action, 'Action',
                                       return_value=node_action)
        mock_wait.return_value = (action.RES_OK, 'Good to go!')

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Completed adding nodes.', res_msg)
        self.assertEqual({'nodes_added': ['NODE_1']}, action.outputs)

        mock_load_node.assert_called_once_with(action.context, 'NODE_1')
        mock_action.assert_called_once_with(
            'NODE_1', 'NODE_JOIN', user=self.ctx.user,
            project=self.ctx.project, domain=self.ctx.domain,
            name='node_join_NODE_1', cause='Derived Action',
            inputs={'cluster_id': 'CLUSTER_ID'})
        node_action.store.assert_called_once_with(action.context)
        mock_dep.assert_called_once_with(action.context, 'NODE_ACTION_ID',
                                         'CLUSTER_ACTION_ID')
        mock_update.assert_called_once_with(
            action.context, 'NODE_ACTION_ID', {'status': node_action.READY})
        mock_start.assert_called_once_with(action_id='NODE_ACTION_ID')
        mock_wait.assert_called_once_with()
        cluster.add_node.assert_called_once_with(node)

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test_do_add_nodes_multiple(self, mock_wait, mock_start, mock_dep,
                                   mock_load_node, mock_update, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.inputs = {'nodes': ['NODE_1', 'NODE_2']}
        action.outputs = {}

        node1 = mock.Mock()
        node1.id = 'NODE_1'
        node1.cluster_id = None
        node1.status = node1.ACTIVE
        node2 = mock.Mock()
        node2.id = 'NODE_2'
        node2.cluster_id = None
        node2.status = node2.ACTIVE
        mock_load_node.side_effect = [node1, node2]

        node_action_1 = mock.Mock()
        node_action_1.id = 'NODE_ACTION_ID_1'
        node_action_2 = mock.Mock()
        node_action_2.id = 'NODE_ACTION_ID_2'

        mock_action = self.patchobject(
            base_action, 'Action', side_effect=[node_action_1, node_action_2])
        mock_wait.return_value = (action.RES_OK, 'Good to go!')

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Completed adding nodes.', res_msg)
        self.assertEqual({'nodes_added': ['NODE_1', 'NODE_2']}, action.outputs)

        mock_load_node.assert_has_calls([
            mock.call(action.context, 'NODE_1'),
            mock.call(action.context, 'NODE_2')])
        mock_action.assert_has_calls([
            mock.call('NODE_1', 'NODE_JOIN', user=self.ctx.user,
                      project=self.ctx.project, domain=self.ctx.domain,
                      name='node_join_NODE_1', cause='Derived Action',
                      inputs={'cluster_id': 'CLUSTER_ID'}),
            mock.call('NODE_2', 'NODE_JOIN', user=self.ctx.user,
                      project=self.ctx.project, domain=self.ctx.domain,
                      name='node_join_NODE_2', cause='Derived Action',
                      inputs={'cluster_id': 'CLUSTER_ID'})])

        node_action_1.store.assert_called_once_with(action.context)
        node_action_2.store.assert_called_once_with(action.context)
        mock_dep.assert_has_calls([
            mock.call(action.context, 'NODE_ACTION_ID_1',
                      'CLUSTER_ACTION_ID'),
            mock.call(action.context, 'NODE_ACTION_ID_2',
                      'CLUSTER_ACTION_ID')])
        mock_update.assert_has_calls([
            mock.call(action.context, 'NODE_ACTION_ID_1',
                      {'status': node_action_1.READY}),
            mock.call(action.context, 'NODE_ACTION_ID_2',
                      {'status': node_action_2.READY})
        ])
        mock_start.assert_has_calls([
            mock.call(action_id='NODE_ACTION_ID_1'),
            mock.call(action_id='NODE_ACTION_ID_2')])

        mock_wait.assert_called_once_with()
        cluster.add_node.assert_has_calls([
            mock.call(node1), mock.call(node2)])

    @mock.patch.object(node_mod.Node, 'load')
    def test_do_add_nodes_node_not_found(self, mock_load_node, mock_load):
        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'nodes': ['NODE_1']}
        mock_load_node.side_effect = exception.NodeNotFound(node='NODE_1')

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Node [NODE_1] is not found.", res_msg)

    @mock.patch.object(node_mod.Node, 'load')
    def test_do_add_nodes_node_already_member(self, mock_load_node, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'nodes': ['NODE_1']}
        action.data = {}

        node = mock.Mock()
        node.cluster_id = 'FAKE_CLUSTER'
        mock_load_node.return_value = node

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Node [NODE_1] is already owned by cluster "
                         "[FAKE_CLUSTER].", res_msg)
        self.assertEqual({}, action.data)

    @mock.patch.object(node_mod.Node, 'load')
    def test_do_add_nodes_node_in_other_cluster(self, mock_load_node,
                                                mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'nodes': ['NODE_1']}
        action.data = {}

        node = mock.Mock()
        node.cluster_id = 'ANOTHER_CLUSTER'
        mock_load_node.return_value = node

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Node [NODE_1] is already owned by cluster "
                         "[ANOTHER_CLUSTER].", res_msg)

    @mock.patch.object(node_mod.Node, 'load')
    def test_do_add_nodes_node_not_active(self, mock_load_node, mock_load):
        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'nodes': ['NODE_1']}
        action.data = {}

        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        node = mock.Mock()
        node.cluster_id = None
        node.status = node.ERROR
        mock_load_node.return_value = node

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Node [NODE_1] is not in ACTIVE status.", res_msg)

    @mock.patch.object(db_api, 'action_update')
    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'action_add_dependency')
    @mock.patch.object(dispatcher, 'start_action')
    @mock.patch.object(ca.ClusterAction, '_wait_for_dependents')
    def test_do_add_nodes_failed_waiting(self, mock_wait, mock_start, mock_dep,
                                         mock_load_node, mock_update,
                                         mock_load):
        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.inputs = {'nodes': ['NODE_1']}
        action.data = {}

        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        node = mock.Mock()
        node.id = 'NODE_1'
        node.cluster_id = None
        node.status = node.ACTIVE
        mock_load_node.return_value = node

        node_action = mock.Mock()
        node_action.id = 'NODE_ACTION_ID'
        self.patchobject(base_action, 'Action', return_value=node_action)
        mock_wait.return_value = (action.RES_TIMEOUT, 'Timeout!')

        # do it
        res_code, res_msg = action.do_add_nodes()

        # assertions
        mock_update.assert_called_once_with(
            action.context, 'NODE_ACTION_ID',
            {'status': node_action.READY})
        self.assertEqual(action.RES_TIMEOUT, res_code)
        self.assertEqual('Timeout!', res_msg)
        self.assertEqual({}, action.data)

    @mock.patch.object(ca.ClusterAction, '_wait_before_deletion')
    @mock.patch.object(db_api, 'node_get')
    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_del_nodes(self, mock_delete, mock_get, mock_wait, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.id = 'CLUSTER_ACTION_ID'
        action.inputs = {'candidates': ['NODE_1', 'NODE_2']}
        action.data = {}

        node1 = mock.Mock()
        node1.id = 'NODE_1'
        node1.cluster_id = 'FAKE_CLUSTER'
        node2 = mock.Mock()
        node2.id = 'NODE_2'
        node2.cluster_id = 'FAKE_CLUSTER'
        mock_get.side_effect = [node1, node2]
        mock_delete.return_value = (action.RES_OK, 'Good to go!')

        # do it
        res_code, res_msg = action.do_del_nodes()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Completed deleting nodes.', res_msg)
        self.assertEqual({'deletion': {'destroy_after_deletion': False}},
                         action.data)

        mock_get.assert_has_calls([
            mock.call(action.context, 'NODE_1', show_deleted=False,
                      project_safe=True),
            mock.call(action.context, 'NODE_2', show_deleted=False,
                      project_safe=True)])
        mock_delete.assert_called_once_with(['NODE_1', 'NODE_2'])

        # deletion policy is attached to the action
        action.data = {
            'deletion': {
                'count': 2,
                'grace_period': 2,
                'destroy_after_deletion': True,
                'candidates': ['NODE_1', 'NODE_2']
            }
        }
        mock_get.side_effect = [node1, node2]
        mock_delete.return_value = (action.RES_OK, 'Good to go!')
        res_code, res_msg = action.do_del_nodes()
        self.assertTrue(action.data['deletion']['destroy_after_deletion'])
        mock_wait.assert_called_once_with(2)

    @mock.patch.object(db_api, 'node_get')
    def test_do_del_nodes_node_not_found(self, mock_get, mock_load):
        cluster = mock.Mock()
        mock_load.return_value = cluster
        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'candidates': ['NODE_1']}
        mock_get.side_effect = exception.NodeNotFound(node='NODE_1')

        # do it
        res_code, res_msg = action.do_del_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Node [NODE_1] is not found.", res_msg)
        self.assertEqual({'deletion': {'destroy_after_deletion': False}},
                         action.data)

    @mock.patch.object(db_api, 'node_get')
    def test_do_del_nodes_node_not_member(self, mock_get, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'nodes': ['NODE_1', 'NODE_2']}
        node1 = mock.Mock()
        node1.cluster_id = None
        node2 = mock.Mock()
        node2.cluster_id = 'ANOTHER_CLUSTER'
        mock_get.side_effect = [node1, node2]

        # do it
        res_code, res_msg = action.do_del_nodes()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual("Completed deleting nodes.", res_msg)
        self.assertEqual({'deletion': {'destroy_after_deletion': False}},
                         action.data)

    @mock.patch.object(db_api, 'node_get')
    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_del_nodes_failed_delete(self, mock_delete, mock_get,
                                        mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'candidates': ['NODE_1']}
        action.data = {}
        node1 = mock.Mock()
        node1.cluster_id = 'FAKE_CLUSTER'
        mock_get.side_effect = [node1]
        mock_delete.return_value = (action.RES_ERROR, 'Things went bad.')

        # do it
        res_code, res_msg = action.do_del_nodes()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual("Things went bad.", res_msg)

    @mock.patch.object(ca.ClusterAction, '_get_action_data')
    @mock.patch.object(scaleutils, 'parse_resize_params')
    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_resize_grow(self, mock_create, mock_parse, mock_get,
                            mock_load):
        cluster = mock.Mock()
        cluster.id = 'ID'
        cluster.desired_capacity = 10
        cluster.nodes = []
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        mock_parse.return_value = 'OK', ''

        def _action_data():
            d = [0]

            def get_data(self):
                if d[0] == 0:
                    d[0] = 1
                    return 0, 0, None
                else:
                    return 2, 12, None
            return get_data
        mock_get.side_effect = _action_data()
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {'creation': {'count': 2}}

        mock_create.return_value = (action.RES_OK, 'All dependents completed.')

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster resize succeeded.', res_msg)

        mock_create.assert_called_once_with(2)
        self.assertEqual({'creation': {'count': 2}}, action.data)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster resize succeeded.',
            desired_capacity=12)

    @mock.patch.object(ca.ClusterAction, '_get_action_data')
    @mock.patch.object(scaleutils, 'parse_resize_params')
    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_resize_grow_with_new_constraints(self, mock_create, mock_parse,
                                                 mock_get, mock_load):
        cluster = mock.Mock()
        cluster.id = 'ID'
        cluster.desired_capacity = 10
        cluster.nodes = []
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        mock_parse.return_value = 'OK', ''

        def _action_data():
            d = [0]

            def get_data(self):
                if d[0] == 0:
                    d[0] = 1
                    return 0, 0, None
                else:
                    return 2, 12, None
            return get_data
        mock_get.side_effect = _action_data()
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {'creation': {'count': 2}}
        action.inputs = {
            'adjustment_type': 'CHANGE_IN_CAPACITY',
            'min_size': 5,
            'max_size': 20
        }

        mock_create.return_value = (action.RES_OK, 'All dependents completed.')

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster resize succeeded.', res_msg)

        mock_create.assert_called_once_with(2)
        self.assertEqual({'creation': {'count': 2}}, action.data)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster resize succeeded.',
            desired_capacity=12, min_size=5, max_size=20)

    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_resize_grow_failed_creation(self, mock_create, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_ID'
        cluster.desired_capacity = 3
        cluster.nodes = []
        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {
            'creation': {'count': 5}
        }

        mock_create.return_value = (action.RES_ERROR, 'Things out of control.')

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Things out of control.', res_msg)

        mock_create.assert_called_once_with(5)
        self.assertEqual({'creation': {'count': 5}}, action.data)

    @mock.patch.object(ca.ClusterAction, '_wait_before_deletion')
    @mock.patch.object(ca.ClusterAction, '_get_action_data')
    @mock.patch.object(scaleutils, 'parse_resize_params')
    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_resize_shrink(self, mock_delete, mock_parse, mock_get,
                              mock_wait, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.desired_capacity = 10
        cluster.nodes = []
        for n in range(10):
            node = mock.Mock()
            node.id = 'NODE-ID-%s' % (n + 1)
            cluster.nodes.append(node)

        cluster.ACTIVE = 'ACTIVE'
        mock_parse.return_value = 'OK', ''
        mock_load.return_value = cluster

        def _action_data():
            d = [0]

            def get_data(self):
                if d[0] == 0:
                    d[0] = 1
                    return 0, 0, None
                else:
                    return 2, 8, []
            return get_data
        mock_get.side_effect = _action_data()
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {
            'deletion': {'count': 2}
        }
        mock_delete.return_value = (action.RES_OK, 'All dependents completed.')

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster resize succeeded.', res_msg)

        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(2, len(mock_delete.call_args[0][0]))
        self.assertEqual({'deletion': {'count': 2}}, action.data)
        cluster.set_status.assert_called_once_with(
            action.context, 'ACTIVE', 'Cluster resize succeeded.',
            desired_capacity=8)

        # deletion policy is attached to the action
        cluster.nodes = []
        for n in range(10):
            node = mock.Mock()
            node.id = 'NODE-ID-%s' % (n + 1)
            cluster.nodes.append(node)
        mock_get.return_value = (2, 9, [cluster.nodes[0]])
        action.data = {
            'deletion': {
                'count': 1,
                'grace_period': 2,
                'destroy_after_deletion': True
            }
        }
        res_code, res_msg = action.do_resize()
        self.assertEqual({'deletion': {'count': 1, 'grace_period': 2,
                                       'destroy_after_deletion': True}},
                         action.data)
        mock_wait.assert_called_once_with(2)

    def test_do_resize_failed_checking(self, mock_load):
        cluster = mock.Mock()
        cluster.desired_capacity = 8
        cluster.nodes = []
        mock_load.return_value = cluster
        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.inputs = {
            'min_size': 10,
            'strict': True
        }

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('The target capacity (8) is less than the specified '
                         'min_size (10).', res_msg)

    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_resize_shrink_failed_delete(self, mock_delete, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLID'
        cluster.desired_capacity = 3
        cluster.nodes = []
        for n in range(3):
            node = mock.Mock()
            node.id = 'NODE-ID-%s' % (n + 1)
            cluster.nodes.append(node)

        cluster.ACTIVE = 'ACTIVE'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {
            'deletion': {
                'count': 2,
                'grace_period': 2
            }
        }
        action.inputs = {
            'adjustment_type': 'CHANGE_IN_CAPACITY',
            'number': -2,
        }

        mock_delete.return_value = (action.RES_ERROR, 'Bad things happened.')

        # do it
        res_code, res_msg = action.do_resize()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Bad things happened.', res_msg)

        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(2, len(mock_delete.call_args[0][0]))
        self.assertEqual({'deletion': {'count': 2, 'grace_period': 2}},
                         action.data)
        self.assertEqual(0, cluster.set_status.call_count)

    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_scale_out_no_pd_no_inputs(self, mock_create, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {}
        mock_create.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_out()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # creating 1 nodes
        mock_create.assert_called_once_with(1)
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=1)

    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_scale_out_with_pd_no_inputs(self, mock_create, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {'creation': {'count': 3}}
        action.inputs = {}
        mock_create.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_out()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # creating 3 nodes
        mock_create.assert_called_once_with(3)
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=3)

    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_scale_out_no_pd_with_inputs(self, mock_create, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        mock_load.return_value = cluster

        action = ca.ClusterAction('ID', 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 2}
        mock_create.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_out()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # creating 2 nodes, given that the cluster is empty now
        mock_create.assert_called_once_with(2)
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=2)

    def test_do_scale_out_count_negative(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': -2}

        # do it
        res_code, res_msg = action.do_scale_out()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Invalid count (-2) for scaling out.', res_msg)

    def test_do_scale_out_failed_checking(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.desired_capacity = 3
        cluster.min_size = 1
        cluster.max_size = 4
        cluster.nodes = [mock.Mock() for i in range(3)]
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 2}

        # do it
        res_code, res_msg = action.do_scale_out()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('The target capacity (5) is greater than the '
                         'cluster\'s max_size (4).', res_msg)

    @mock.patch.object(ca.ClusterAction, '_create_nodes')
    def test_do_scale_out_failed_create_nodes(self, mock_create, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.desired_capacity = 3
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = [mock.Mock()]
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 2}

        # Error cases
        for result in (action.RES_ERROR, action.RES_CANCEL,
                       action.RES_TIMEOUT):
            mock_create.return_value = result, 'Too hot to work!'
            # do it
            res_code, res_msg = action.do_scale_out()
            # assertions
            self.assertEqual(result, res_code)
            self.assertEqual('Too hot to work!', res_msg)
            cluster.set_status.assert_called_once_with(
                action.context, cluster.ERROR, 'Too hot to work!')
            cluster.set_status.reset_mock()
            mock_create.assert_called_once_with(2)
            mock_create.reset_mock()

        # Timeout case
        mock_create.return_value = action.RES_RETRY, 'Not good time!'
        # do it
        res_code, res_msg = action.do_scale_out()
        # assertions
        self.assertEqual(action.RES_RETRY, res_code)
        self.assertEqual('Not good time!', res_msg)
        self.assertEqual(0, cluster.set_status.call_count)
        mock_create.assert_called_once_with(2)

    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_scale_in_no_pd_no_inputs(self, mock_delete, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(10):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {}
        mock_delete.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # deleting 1 nodes
        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(1, len(mock_delete.call_args[0][0]))
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=9)

    @mock.patch.object(ca.ClusterAction, '_wait_before_deletion')
    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_scale_in_with_pd_no_input(self, mock_delete, mock_wait,
                                          mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(5):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {
            'deletion': {
                'count': 2,
                'grace_period': 2,
                'candidates': ['NODE_ID_3', 'NODE_ID_4']
            }
        }
        action.inputs = {}
        mock_delete.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # deleting 2 nodes
        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(2, len(mock_delete.call_args[0][0]))
        self.assertIn('NODE_ID_3', mock_delete.call_args[0][0])
        self.assertIn('NODE_ID_4', mock_delete.call_args[0][0])
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=3)
        mock_wait.assert_called_once_with(2)

    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_scale_in_no_pd_with_input(self, mock_delete, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(5):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 3}
        mock_delete.return_value = (action.RES_OK, 'Life is beautiful.')

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        self.assertEqual(action.RES_OK, res_code)

        # deleting 3 nodes
        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(3, len(mock_delete.call_args[0][0]))
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=2)

    def test_do_scale_in_invalid_count(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(5):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': -3}

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual('Invalid count (-3) for scaling in.', res_msg)
        self.assertEqual(action.RES_ERROR, res_code)

    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_scale_in_best_effort(self, mock_delete, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 0
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(2):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 3}
        mock_delete.return_value = (action.RES_OK, 'Deletion done.')

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Cluster scaling succeeded.', res_msg)
        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(2, len(mock_delete.call_args[0][0]))
        cluster.set_status.assert_called_once_with(
            action.context, cluster.ACTIVE, 'Cluster scaling succeeded.',
            desired_capacity=0)

    def test_do_scale_in_failed_check(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(2):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 3}

        # do it
        res_code, res_msg = action.do_scale_in()

        # assertions
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('The target capacity (0) is less than the '
                         'cluster\'s min_size (1).', res_msg)

    @mock.patch.object(ca.ClusterAction, '_delete_nodes')
    def test_do_scale_in_failed_delete_nodes(self, mock_delete, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        cluster.desired_capacity = 3
        cluster.min_size = 1
        cluster.max_size = -1
        cluster.nodes = []
        for i in range(5):
            node = mock.Mock()
            node.id = 'NODE_ID_%s' % (i + 1)
            cluster.nodes.append(node)
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.data = {}
        action.inputs = {'count': 2}

        # Error cases
        for result in (action.RES_ERROR, action.RES_CANCEL,
                       action.RES_TIMEOUT):
            mock_delete.return_value = result, 'Too cold to work!'
            # do it
            res_code, res_msg = action.do_scale_in()
            # assertions
            self.assertEqual(result, res_code)
            self.assertEqual('Too cold to work!', res_msg)
            cluster.set_status.assert_called_once_with(
                action.context, cluster.ERROR, 'Too cold to work!')
            cluster.set_status.reset_mock()
            mock_delete.assert_called_once_with(mock.ANY)
            self.assertEqual(2, len(mock_delete.call_args[0][0]))
            mock_delete.reset_mock()

        # Timeout case
        mock_delete.return_value = action.RES_RETRY, 'Not good time!'
        # do it
        res_code, res_msg = action.do_scale_in()
        # assertions
        self.assertEqual(action.RES_RETRY, res_code)
        self.assertEqual('Not good time!', res_msg)
        self.assertEqual(0, cluster.set_status.call_count)
        mock_delete.assert_called_once_with(mock.ANY)
        self.assertEqual(2, len(mock_delete.call_args[0][0]))

    def test_do_attach_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.policies = []
        cluster.attach_policy.return_value = True, 'OK'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {
            'policy_id': 'FAKE_POLICY',
            'FOO': 'BAR'
        }

        # do it
        res_code, res_msg = action.do_attach_policy()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('OK', res_msg)
        cluster.attach_policy.assert_called_once_with(
            action.context, 'FAKE_POLICY', {'FOO': 'BAR'})

    def test_do_attach_policy_missing_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {}

        # do it
        res_code, res_msg = action.do_attach_policy()
        # assertion
        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Policy not specified.', res_msg)

    def test_do_detach_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.detach_policy.return_value = True, 'Success'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'policy_id': 'FAKE_POLICY'}

        # do it
        res_code, res_msg = action.do_detach_policy()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Success', res_msg)
        cluster.detach_policy.assert_called_once_with(action.context,
                                                      'FAKE_POLICY')

    def test_do_detach_policy_missing_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {}

        # do it
        res_code, res_msg = action.do_detach_policy()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Policy not specified.', res_msg)

    def test_do_detach_policy_failed(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.detach_policy.return_value = False, 'Failure.'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'policy_id': 'FAKE_POLICY'}

        # do it
        res_code, res_msg = action.do_detach_policy()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Failure.', res_msg)
        cluster.detach_policy.assert_called_once_with(action.context,
                                                      'FAKE_POLICY')

    def test_do_update_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.update_policy.return_value = True, 'Success.'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {
            'policy_id': 'FAKE_POLICY',
            'foo': 'bar',
        }

        # do it
        res_code, res_msg = action.do_update_policy()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Success.', res_msg)
        cluster.update_policy.assert_called_once_with(
            action.context, 'FAKE_POLICY', foo='bar')

    def test_do_update_policy_failed_update(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        cluster.update_policy.return_value = False, 'Something is wrong.'
        mock_load.return_value = cluster

        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {
            'policy_id': 'FAKE_POLICY',
            'foo': 'bar',
        }

        # do it
        res_code, res_msg = action.do_update_policy()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Something is wrong.', res_msg)
        cluster.update_policy.assert_called_once_with(
            action.context, 'FAKE_POLICY', foo='bar')

    def test_do_update_policy_missing_policy(self, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_ACTION', self.ctx)
        action.inputs = {'priority': 30}

        # do it
        res_code, res_msg = action.do_update_policy()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Policy not specified.', res_msg)

    @mock.patch.object(base_action.Action, 'policy_check')
    def test__execute(self, mock_check, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_FLY', self.ctx)
        action.do_fly = mock.Mock(return_value=(action.RES_OK, 'Good!'))
        action.data = {
            'status': policy_base.CHECK_OK,
            'reason': 'Policy checking passed'
        }

        res_code, res_msg = action._execute()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('Good!', res_msg)
        mock_check.assert_has_calls([
            mock.call('FAKE_CLUSTER', 'BEFORE'),
            mock.call('FAKE_CLUSTER', 'AFTER')])

    @mock.patch.object(event_mod, 'error')
    @mock.patch.object(base_action.Action, 'policy_check')
    def test_execute_failed_policy_check(self, mock_check, mock_error,
                                         mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_FLY', self.ctx)
        action.do_fly = mock.Mock(return_value=(action.RES_OK, 'Good!'))
        action.data = {
            'status': policy_base.CHECK_ERROR,
            'reason': 'Something is wrong.'
        }

        res_code, res_msg = action._execute()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Policy check failure: Something is wrong.', res_msg)
        mock_check.assert_called_once_with('FAKE_CLUSTER', 'BEFORE')
        mock_error.assert_called_once_with(
            action.context, cluster, 'CLUSTER_FLY', 'Failed',
            'Policy check failure: Something is wrong.')

    @mock.patch.object(event_mod, 'error')
    @mock.patch.object(base_action.Action, 'policy_check')
    def test_execute_unsupported_action(self, mock_check, mock_error,
                                        mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DANCE', self.ctx)
        action.data = {
            'status': policy_base.CHECK_OK,
            'reason': 'All is going well.'
        }

        res_code, res_msg = action._execute()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Unsupported action: CLUSTER_DANCE.', res_msg)
        mock_check.assert_called_once_with('FAKE_CLUSTER', 'BEFORE')
        mock_error.assert_called_once_with(
            action.context, cluster, 'CLUSTER_DANCE', 'Failed',
            'Unsupported action: CLUSTER_DANCE.')

    @mock.patch.object(event_mod, 'error')
    def test_execute_post_check_failed(self, mock_error, mock_load):
        def fake_check(cluster_id, target):
            if target == 'BEFORE':
                action.data = {
                    'status': policy_base.CHECK_OK,
                    'reason': 'Policy checking passed.'
                }
            else:
                action.data = {
                    'status': policy_base.CHECK_ERROR,
                    'reason': 'Policy checking failed.'
                }

        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_FLY', self.ctx)
        action.do_fly = mock.Mock(return_value=(action.RES_OK, 'Cool!'))
        mock_check = self.patchobject(action, 'policy_check',
                                      side_effect=fake_check)

        res_code, res_msg = action._execute()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Policy check failure: Policy checking failed.',
                         res_msg)
        mock_check.assert_has_calls([
            mock.call('FAKE_CLUSTER', 'BEFORE'),
            mock.call('FAKE_CLUSTER', 'AFTER')])
        mock_error.assert_called_once_with(
            action.context, cluster, 'CLUSTER_FLY', 'Failed',
            'Policy check failure: Policy checking failed.')

    @mock.patch.object(senlin_lock, 'cluster_lock_acquire')
    @mock.patch.object(senlin_lock, 'cluster_lock_release')
    def test_execute(self, mock_release, mock_acquire, mock_load):
        cluster = mock.Mock()
        cluster.id = 'FAKE_CLUSTER'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_FLY', self.ctx)
        action.id = 'ACTION_ID'
        self.patchobject(action, '_execute',
                         return_value=(action.RES_OK, 'success'))
        mock_acquire.return_value = action

        res_code, res_msg = action.execute()

        self.assertEqual(action.RES_OK, res_code)
        self.assertEqual('success', res_msg)
        mock_load.assert_called_once_with(action.context, 'FAKE_CLUSTER')
        mock_acquire.assert_called_once_with(
            self.ctx, 'FAKE_CLUSTER', 'ACTION_ID',
            senlin_lock.CLUSTER_SCOPE, False)
        mock_release.assert_called_once_with(
            'FAKE_CLUSTER', 'ACTION_ID', senlin_lock.CLUSTER_SCOPE)

    @mock.patch.object(senlin_lock, 'cluster_lock_acquire')
    def test_execute_failed_locking(self, mock_acquire, mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        mock_acquire.return_value = None

        res_code, res_msg = action.execute()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Failed in locking cluster.', res_msg)
        mock_load.assert_called_once_with(action.context, cluster.id)

    @mock.patch.object(senlin_lock, 'cluster_lock_acquire')
    @mock.patch.object(senlin_lock, 'cluster_lock_release')
    def test_execute_failed_execute(self, mock_release, mock_acquire,
                                    mock_load):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        mock_load.return_value = cluster
        action = ca.ClusterAction(cluster.id, 'CLUSTER_DELETE', self.ctx)
        action.id = 'ACTION_ID'
        mock_acquire.return_value = action
        self.patchobject(action, '_execute',
                         return_value=(action.RES_ERROR, 'Failed execution.'))

        res_code, res_msg = action.execute()

        self.assertEqual(action.RES_ERROR, res_code)
        self.assertEqual('Failed execution.', res_msg)
        mock_load.assert_called_once_with(action.context, cluster.id)
        mock_acquire.assert_called_once_with(
            self.ctx, 'CLUSTER_ID', 'ACTION_ID',
            senlin_lock.CLUSTER_SCOPE, True)
        mock_release.assert_called_once_with(
            'CLUSTER_ID', 'ACTION_ID', senlin_lock.CLUSTER_SCOPE)

    def test_cancel(self, mock_load):
        action = ca.ClusterAction('ID', 'CLUSTER_DELETE', self.ctx)
        res = action.cancel()
        self.assertEqual(action.RES_OK, res)
