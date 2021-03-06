# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_log import log as logging

from senlin.tests.functional import api as test_api
from senlin.tests.functional import base
from senlin.tests.functional.utils import test_utils

LOG = logging.getLogger(__name__)


class TestNode(base.SenlinFunctionalTest):
    def setUp(self):
        super(TestNode, self).setUp()
        # Create profile
        self.profile = test_api.create_profile(
            self.client, test_utils.random_name('profile'),
            test_utils.spec_nova_server)

    def tearDown(self):
        # Delete profile
        test_api.delete_profile(self.client, self.profile['id'])
        super(TestNode, self).tearDown()

    def test_node_create_delete_with_target_cluster(self):
        # Create a cluster
        desired_capacity = 1
        min_size = 1
        max_size = 3
        cluster = test_api.create_cluster(self.client,
                                          test_utils.random_name('cluster'),
                                          self.profile['id'], desired_capacity,
                                          min_size, max_size)
        cluster = test_utils.wait_for_status(test_api.get_cluster, self.client,
                                             cluster['id'], 'ACTIVE')

        # Create a node belongs to the cluster
        node1 = test_api.create_node(self.client,
                                     test_utils.random_name('node1'),
                                     self.profile['id'],
                                     cluster_id=cluster['id'],
                                     role='master')

        node1 = test_utils.wait_for_status(test_api.get_node, self.client,
                                           node1['id'], 'ACTIVE')
        self.assertEqual(cluster['id'], node1['cluster_id'])
        self.assertEqual(2, node1['index'])
        cluster = test_api.get_cluster(self.client, cluster['id'])
        self.assertEqual(2, cluster['desired_capacity'])
        self.assertEqual(2, len(cluster['nodes']))
        self.assertIn(node1['id'], cluster['nodes'])

        # List nodes
        nodes = test_api.list_nodes(self.client)
        nodes_id = [n['id'] for n in nodes]
        self.assertIn(node1['id'], nodes_id)
        for n_id in cluster['nodes']:
            self.assertIn(n_id, nodes_id)

        # Delete node1 from cluster
        test_api.delete_node(self.client, node1['id'])
        test_utils.wait_for_status(test_api.get_node, self.client,
                                   node1['id'], 'DELETED',
                                   ignore_missing=True)
        cluster = test_api.get_cluster(self.client, cluster['id'])
        self.assertEqual(1, len(cluster['nodes']))
        self.assertNotIn(node1['id'], cluster['nodes'])

        # Delete cluster
        test_api.delete_cluster(self.client, cluster['id'])
        test_utils.wait_for_status(test_api.get_cluster, self.client,
                                   cluster['id'], 'DELETED',
                                   ignore_missing=True)

    def test_node_create_join_leave_cluster(self):
        # Create a cluster
        desired_capacity = 0
        min_size = 0
        max_size = 3
        cluster = test_api.create_cluster(self.client,
                                          test_utils.random_name('cluster'),
                                          self.profile['id'], desired_capacity,
                                          min_size, max_size)
        cluster = test_utils.wait_for_status(test_api.get_cluster, self.client,
                                             cluster['id'], 'ACTIVE')

        # Create a node and let it join the cluster
        node1 = test_api.create_node(self.client,
                                     test_utils.random_name('node1'),
                                     self.profile['id'])
        node1 = test_utils.wait_for_status(test_api.get_node, self.client,
                                           node1['id'], 'ACTIVE')
        action_id = test_api.action_node(self.client, node1['id'], 'join',
                                         {'cluster_id': cluster['id']})
        test_utils.wait_for_status(test_api.get_action, self.client,
                                   action_id, 'SUCCEEDED')

        node1 = test_api.get_node(self.client, node1['id'])
        self.assertEqual(cluster['id'], node1['cluster_id'])
        self.assertEqual(1, node1['index'])
        cluster = test_api.get_cluster(self.client, cluster['id'])
        self.assertEqual(1, len(cluster['nodes']))
        self.assertIn(node1['id'], cluster['nodes'])

        # Let node1 leave the cluster
        action_id = test_api.action_node(self.client, node1['id'], 'leave')
        test_utils.wait_for_status(test_api.get_action, self.client,
                                   action_id, 'SUCCEEDED')

        node1 = test_api.get_node(self.client, node1['id'])
        self.assertIsNone(node1['cluster_id'])
        self.assertEqual(-1, node1['index'])
        cluster = test_api.get_cluster(self.client, cluster['id'])
        self.assertEqual(0, len(cluster['nodes']))

        # Delete node1
        test_api.delete_node(self.client, node1['id'])
        test_utils.wait_for_status(test_api.get_node, self.client,
                                   node1['id'], 'DELETED',
                                   ignore_missing=True)

        # Delete cluster
        test_api.delete_cluster(self.client, cluster['id'])
        test_utils.wait_for_status(test_api.get_cluster, self.client,
                                   cluster['id'], 'DELETED',
                                   ignore_missing=True)
