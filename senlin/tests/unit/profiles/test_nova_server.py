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

import base64
import copy

import mock
from oslo_utils import encodeutils
import six

from senlin.common import exception
from senlin.common.i18n import _
from senlin.common import utils as common_utils
from senlin.drivers import base as driver_base
from senlin.profiles.os.nova import server
from senlin.tests.unit.common import base
from senlin.tests.unit.common import utils


class TestNovaServerProfile(base.SenlinTestCase):

    def setUp(self):
        super(TestNovaServerProfile, self).setUp()

        self.context = utils.dummy_context()
        self.spec = {
            'type': 'os.nova.server',
            'version': '1.0',
            'properties': {
                'context': {},
                'adminPass': 'adminpass',
                'auto_disk_config': True,
                'availability_zone': 'FAKE_AZ',
                'block_device_mapping': [{
                    'device_name': 'FAKE_NAME',
                    'volume_size': 1000,
                }],
                'config_drive': False,
                'flavor': 'FLAV',
                'image': 'FAKE_IMAGE',
                'key_name': 'FAKE_KEYNAME',
                "metadata": {"meta var": "meta val"},
                'name': 'FAKE_SERVER_NAME',
                'networks': [{
                    'port': 'FAKE_PORT',
                    'fixed-ip': 'FAKE_IP',
                    'network': 'FAKE_NET',
                }],
                'personality': [{
                    'path': '/etc/motd',
                    'contents': 'foo',
                }],
                'scheduler_hints': {
                    'same_host': 'HOST_ID',
                },
                'security_groups': ['HIGH_SECURITY_GROUP'],
                'user_data': 'FAKE_USER_DATA',
            }
        }

    def test_init(self):
        profile = server.ServerProfile('t', self.spec)

        self.assertIsNone(profile._novaclient)
        self.assertIsNone(profile._neutronclient)
        self.assertIsNone(profile.server_id)

    @mock.patch.object(driver_base, 'SenlinDriver')
    def test_nova_client(self, mock_senlindriver):
        obj = mock.Mock()
        sd = mock.Mock()
        nc = mock.Mock()
        sd.compute.return_value = nc
        mock_senlindriver.return_value = sd

        profile = server.ServerProfile('t', self.spec)

        # cached will be returned
        profile._novaclient = nc
        self.assertEqual(nc, profile.nova(obj))

        # new nc created if no cache found
        profile._novaclient = None
        params = mock.Mock()
        mock_param = self.patchobject(profile, '_build_conn_params',
                                      return_value=params)
        res = profile.nova(obj)
        self.assertEqual(nc, res)
        self.assertEqual(nc, profile._novaclient)
        mock_param.assert_called_once_with(obj.user, obj.project)
        sd.compute.assert_called_once_with(params)

    @mock.patch.object(driver_base, 'SenlinDriver')
    def test_neutron_client(self, mock_senlindriver):
        obj = mock.Mock()
        sd = mock.Mock()
        nc = mock.Mock()
        sd.network.return_value = nc
        mock_senlindriver.return_value = sd

        profile = server.ServerProfile('t', self.spec)

        # cached will be returned
        profile._neutronclient = nc
        self.assertEqual(nc, profile.neutron(obj))

        # new nc created if no cache found
        profile._neutronclient = None
        params = mock.Mock()
        mock_param = self.patchobject(profile, '_build_conn_params',
                                      return_value=params)
        res = profile.neutron(obj)
        self.assertEqual(nc, res)
        self.assertEqual(nc, profile._neutronclient)
        mock_param.assert_called_once_with(obj.user, obj.project)
        sd.network.assert_called_once_with(params)

    def test_do_validate(self):
        profile = server.ServerProfile('t', self.spec)
        res = profile.do_validate(mock.Mock())
        self.assertTrue(res)

    @mock.patch.object(common_utils, 'random_name')
    def test_do_create(self, mock_random_name):
        mock_random_name.return_value = '12345678'
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        test_server = mock.Mock()
        test_server.name = 'TEST_SERVER'
        test_server.cluster_id = 'FAKE_CLUSTER_ID'
        test_server.data = {
            'placement': {
                'zone': 'AZ1'
            }
        }
        image = mock.Mock()
        image.id = 'FAKE_IMAGE_ID'
        novaclient.image_find.return_value = image
        flavor = mock.Mock()
        flavor.id = 'FAKE_FLAVOR_ID'
        novaclient.flavor_find.return_value = flavor
        net = mock.Mock()
        net.id = 'FAKE_NETWORK_ID'
        neutronclient.network_get.return_value = net

        nova_server = mock.Mock()
        nova_server.id = 'FAKE_NOVA_SERVER_ID'
        novaclient.server_create.return_value = nova_server

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = novaclient
        profile._neutronclient = neutronclient

        server_id = profile.do_create(test_server)

        mock_random_name.assert_called_once_with(8)
        novaclient.image_find.assert_called_once_with('FAKE_IMAGE')
        novaclient.flavor_find.assert_called_once_with('FLAV', False)
        neutronclient.network_get.assert_called_once_with('FAKE_NET')

        attrs = dict(
            adminPass='adminpass',
            auto_disk_config=True,
            # availability_zone='FAKE_AZ',
            block_device_mapping=[{
                'volume_size': 1000,
                'device_name': 'FAKE_NAME'
            }],
            config_drive=False,
            flavorRef='FAKE_FLAVOR_ID',
            imageRef='FAKE_IMAGE_ID',
            key_name='FAKE_KEYNAME',
            metadata={
                'cluster': 'FAKE_CLUSTER_ID',
                'meta var': 'meta val'
            },
            name='TEST_SERVER-12345678',
            networks=[{
                'fixed-ip': 'FAKE_IP',
                'port': 'FAKE_PORT',
                'uuid': 'FAKE_NETWORK_ID',
            }],
            personality=[{
                'path': '/etc/motd',
                'contents': 'foo'
            }],
            scheduler_hints={
                'same_host': 'HOST_ID'
            },
            security_groups=['HIGH_SECURITY_GROUP'],
            user_data='FAKE_USER_DATA',
            availability_zone='AZ1',
        )

        ud = encodeutils.safe_encode('FAKE_USER_DATA')
        attrs['user_data'] = encodeutils.safe_decode(base64.b64encode(ud))

        novaclient.server_create.assert_called_once_with(**attrs)
        self.assertEqual(nova_server.id, server_id)

    @mock.patch.object(common_utils, 'random_name')
    def test_do_create_port_and_fixedip_not_defined(self, mock_random_name):
        mock_random_name.return_value = '12345678'
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        test_server = mock.Mock()
        test_server.name = 'TEST_SERVER'
        test_server.cluster_id = 'FAKE_CLUSTER_ID'
        test_server.data = {}
        image = mock.Mock()
        image.id = 'FAKE_IMAGE_ID'
        novaclient.image_find.return_value = image
        flavor = mock.Mock()
        flavor.id = 'FAKE_FLAVOR_ID'
        novaclient.flavor_find.return_value = flavor
        net = mock.Mock()
        net.id = 'FAKE_NETWORK_ID'
        neutronclient.network_get.return_value = net

        nova_server = mock.Mock()
        nova_server.id = 'FAKE_NOVA_SERVER_ID'
        novaclient.server_create.return_value = nova_server

        spec = {
            'type': 'os.nova.server',
            'version': '1.0',
            'properties': {
                'flavor': 'FLAV',
                'image': 'FAKE_IMAGE',
                'key_name': 'FAKE_KEYNAME',
                'name': 'FAKE_SERVER_NAME',
                'networks': [{
                    'network': 'FAKE_NET'
                }]
            }
        }

        profile = server.ServerProfile('s2', spec)
        profile._novaclient = novaclient
        profile._neutronclient = neutronclient
        server_id = profile.do_create(test_server)

        mock_random_name.assert_called_once_with(8)
        attrs = dict(auto_disk_config=True,
                     flavorRef='FAKE_FLAVOR_ID',
                     imageRef='FAKE_IMAGE_ID',
                     key_name='FAKE_KEYNAME',
                     metadata={'cluster': 'FAKE_CLUSTER_ID'},
                     name='TEST_SERVER-12345678',
                     networks=[{'uuid': 'FAKE_NETWORK_ID'}])

        novaclient.server_create.assert_called_once_with(**attrs)
        self.assertEqual(nova_server.id, server_id)

    @mock.patch.object(common_utils, 'random_name')
    def test_do_create_server_attrs_not_defined(self, mock_random_name):
        mock_random_name.return_value = '12345678'
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        test_server = mock.Mock()
        test_server.name = 'TEST_SERVER'
        test_server.cluster_id = 'FAKE_CLUSTER_ID'
        test_server.data = {}
        flavor = mock.Mock()
        flavor.id = 'FAKE_FLAVOR_ID'
        novaclient.flavor_find.return_value = flavor
        net = mock.Mock()
        net.id = 'FAKE_NETWORK_ID'
        neutronclient.network_get.return_value = net

        nova_server = mock.Mock()
        nova_server.id = 'FAKE_NOVA_SERVER_ID'
        novaclient.server_create.return_value = nova_server

        # Assume image/scheduler_hints/user_data were not defined in spec file
        spec = {
            'type': 'os.nova.server',
            'version': '1.0',
            'properties': {
                'flavor': 'FLAV',
                'name': 'FAKE_SERVER_NAME',
                'security_groups': ['HIGH_SECURITY_GROUP'],
            }
        }

        profile = server.ServerProfile('s1', spec)
        profile._novaclient = novaclient
        profile._neutronclient = neutronclient
        server_id = profile.do_create(test_server)

        mock_random_name.assert_called_once_with(8)
        attrs = dict(auto_disk_config=True,
                     flavorRef='FAKE_FLAVOR_ID',
                     name='TEST_SERVER-12345678',
                     metadata={
                         'cluster': 'FAKE_CLUSTER_ID',
                     },
                     security_groups=['HIGH_SECURITY_GROUP'])

        novaclient.server_create.assert_called_once_with(**attrs)
        self.assertEqual(nova_server.id, server_id)

    def test_do_create_obj_name_cluster_id_is_none(self):
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        test_server = mock.Mock()
        test_server.name = None
        test_server.cluster_id = None
        test_server.data = {}
        flavor = mock.Mock()
        flavor.id = 'FAKE_FLAVOR_ID'
        novaclient.flavor_find.return_value = flavor
        net = mock.Mock()
        net.id = 'FAKE_NETWORK_ID'
        neutronclient.network_get.return_value = net

        nova_server = mock.Mock()
        nova_server.id = 'FAKE_NOVA_SERVER_ID'
        novaclient.server_create.return_value = nova_server

        spec = {
            'type': 'os.nova.server',
            'version': '1.0',
            'properties': {
                'flavor': 'FLAV',
                'name': 'FAKE_SERVER_NAME',
                'security_groups': ['HIGH_SECURITY_GROUP'],
            }
        }

        profile = server.ServerProfile('t', spec)
        profile._novaclient = novaclient
        profile._neutronclient = neutronclient
        server_id = profile.do_create(test_server)

        attrs = dict(auto_disk_config=True,
                     flavorRef='FAKE_FLAVOR_ID',
                     name='FAKE_SERVER_NAME',
                     metadata={},
                     security_groups=['HIGH_SECURITY_GROUP'])

        novaclient.server_create.assert_called_once_with(**attrs)
        self.assertEqual(nova_server.id, server_id)

    def test_do_create_bdm_v2(self):
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        test_server = mock.Mock()
        test_server.name = None
        test_server.cluster_id = None
        test_server.data = {}
        flavor = mock.Mock()
        flavor.id = 'FAKE_FLAVOR_ID'
        novaclient.flavor_find.return_value = flavor
        net = mock.Mock()
        net.id = 'FAKE_NETWORK_ID'
        neutronclient.network_get.return_value = net

        nova_server = mock.Mock()
        nova_server.id = 'FAKE_NOVA_SERVER_ID'
        novaclient.server_create.return_value = nova_server
        bdm_v2 = [
            {
                'volume_size': 1,
                'uuid': '6ce0be68',
                'source_type': 'image',
                'destination_type': 'volume',
                'boot_index': 0,
            },
            {
                'volume_size': 2,
                'source_type': 'blank',
                'destination_type': 'volume',
            }
        ]
        expected_volume = {
            'guest_format': None,
            'boot_index': 0,
            'uuid': '6ce0be68',
            'volume_size': 1,
            'device_name': None,
            'disk_bus': None,
            'source_type': 'image',
            'device_type': None,
            'destination_type': 'volume',
            'delete_on_termination': None
        }

        spec = {
            'type': 'os.nova.server',
            'version': '1.0',
            'properties': {
                'flavor': 'FLAV',
                'name': 'FAKE_SERVER_NAME',
                'security_groups': ['HIGH_SECURITY_GROUP'],
                'block_device_mapping_v2': bdm_v2,
            }
        }

        profile = server.ServerProfile('t', spec)
        self.assertDictEqual(profile.properties['block_device_mapping_v2'][0],
                             expected_volume)

        profile._novaclient = novaclient
        profile._neutronclient = neutronclient
        server_id = profile.do_create(test_server)

        attrs = dict(auto_disk_config=True,
                     flavorRef='FAKE_FLAVOR_ID',
                     name='FAKE_SERVER_NAME',
                     metadata={},
                     security_groups=['HIGH_SECURITY_GROUP'],
                     block_device_mapping_v2=bdm_v2)

        novaclient.server_create.assert_called_once_with(**attrs)
        self.assertEqual(nova_server.id, server_id)

    def test_do_delete_no_physical_id(self):
        # Test path where server doesn't already exist
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        test_server = mock.Mock()
        test_server.physical_id = None

        self.assertTrue(profile.do_delete(test_server))

    def test_do_delete_successful(self):
        profile = server.ServerProfile('t', self.spec)

        nc = mock.Mock()
        nc.server_delete.return_value = None
        profile._novaclient = nc

        test_server = mock.Mock()
        test_server.physical_id = 'FAKE_ID'

        res = profile.do_delete(test_server)
        self.assertTrue(res)

    def test_do_delete_wait_for_server_delete_timeout(self):
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        nc.wait_for_server_delete.side_effect = exception.InternalError(
            code=500, message='timeout')

        res = profile.do_delete(obj)
        self.assertFalse(res)
        nc.wait_for_server_delete.assert_called_once_with('FAKE_ID')

    def test_do_delete_with_delete_exception(self):
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc

        err = exception.ProfileOperationTimeout(message='exception')
        nc.server_delete.side_effect = err

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        # Test specific exception path
        res = profile.do_delete(obj)
        self.assertFalse(res)
        nc.server_delete.assert_called_once_with('FAKE_ID')

    @mock.patch.object(server.ServerProfile, '_update_network')
    def test_do_update_network_successful_no_definition_overlap(
            self, mock_update_network):

        mock_update_network.return_value = True
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        networks_delete = [{
            'port': 'FAKE_PORT',
            'fixed-ip': 'FAKE_IP',
            'network': 'FAKE_NET',
        }]
        new_networks = [{
            'port': 'FAKE_PORT_NEW',
            'fixed-ip': 'FAKE_IP_NEW',
            'network': 'FAKE_NET_NEW',
        }]
        new_spec = copy.deepcopy(self.spec)
        new_spec['properties']['networks'] = new_networks
        new_profile = server.ServerProfile('t', new_spec)

        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)
        mock_update_network.assert_called_with(obj, new_networks,
                                               networks_delete)

    @mock.patch.object(server.ServerProfile, '_update_network')
    def test_do_update_network_successful_definition_overlap(
            self, mock_update_network):

        mock_update_network.return_value = True
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        networks_delete = [{
            'port': 'FAKE_PORT',
            'fixed-ip': 'FAKE_IP',
            'network': 'FAKE_NET',
        }]
        new_networks = [{
            'port': 'FAKE_PORT_NEW',
            'fixed-ip': 'FAKE_IP_NEW',
            'network': 'FAKE_NET_NEW',
        }]
        new_spec = copy.deepcopy(self.spec)
        new_spec['properties']['networks'] = [new_networks[0],
                                              networks_delete[0]]
        new_profile = server.ServerProfile('t', new_spec)

        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)
        mock_update_network.assert_called_with(obj, new_networks, [])

    def test_do_update_without_profile(self):
        profile = server.ServerProfile('t', self.spec)
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        new_profile = None
        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)

    def test_update_network(self):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        novaclient = mock.Mock()
        neutronclient = mock.Mock()
        server_obj = mock.Mock()
        net1 = mock.Mock()
        net2 = mock.Mock()
        net3 = mock.Mock()
        net4 = mock.Mock()
        net1.id = 'net1'
        net2.id = 'net2'
        net3.id = 'net3'
        net4.id = 'net4'
        existing_ports = [
            {
                'port_id': 'port1',
                'net_id': 'net1',
                'fixed_ips': [{'subnet_id': 'subnet1', 'ip_address': 'ip1'}]
            },
            {
                'port_id': 'port2',
                'net_id': 'net1',
                'fixed_ips': [{'subnet_id': 'subnet1',
                               'ip_address': 'ip-random2'}]
            },
            {
                'port_id': 'port3',
                'net_id': 'net2',
                'fixed_ips': [{'subnet_id': 'subnet2', 'ip_address': 'ip3'}]
            },
        ]
        deleted_networks = [
            {'fixed-ip': 'ip1', 'network': 'net1', 'port': None},
            {'fixed-ip': None, 'network': 'net1', 'port': None},
            {'fixed-ip': None, 'network': None, 'port': 'port3'}
        ]
        created_networks = [
            {'fixed-ip': 'ip2', 'network': 'net1', 'port': None},
            {'fixed-ip': None, 'network': 'net2', 'port': None},
            {'fixed-ip': None, 'network': None, 'port': 'port4'}
        ]
        novaclient.server_get.return_value = server_obj
        novaclient.server_interface_list.return_value = existing_ports
        neutronclient.network_get.side_effect = [net1, net1,
                                                 net1, net2]

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = novaclient
        profile._neutronclient = neutronclient
        profile._update_network(obj, created_networks, deleted_networks)
        calls = [mock.call('port1', server_obj),
                 mock.call('port3', server_obj),
                 mock.call('port2', server_obj)]
        novaclient.server_interface_delete.assert_has_calls(calls)
        calls = [
            mock.call(
                server_obj, net_id='net1', fixed_ips=[{'ip_address': 'ip2'}]),
            mock.call(server_obj, net_id='net2'),
            mock.call(server_obj, port_id='port4'),
        ]
        novaclient.server_interface_create.assert_has_calls(calls)

    @mock.patch.object(server.ServerProfile, '_update_image')
    def test_do_update_image_succeeded(self, mock_update_image):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()
        new_spec = copy.deepcopy(self.spec)
        new_spec['properties']['image'] = 'FAKE_IMAGE_NEW'
        new_profile = server.ServerProfile('t', new_spec)

        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)
        mock_update_image.assert_called_with(obj, 'FAKE_IMAGE',
                                             'FAKE_IMAGE_NEW',
                                             'adminpass')

    @mock.patch.object(server.ServerProfile, '_update_image')
    def test_do_update_image_with_passwd(self, mock_update_image):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()
        new_spec = copy.deepcopy(self.spec)
        new_spec['properties']['image'] = 'FAKE_IMAGE_NEW'
        new_spec['properties']['adminPass'] = 'adminpass2'
        new_profile = server.ServerProfile('t', new_spec)
        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)
        mock_update_image.assert_called_with(obj, 'FAKE_IMAGE',
                                             'FAKE_IMAGE_NEW',
                                             'adminpass2')

        del new_spec['properties']['adminPass']
        new_profile = server.ServerProfile('t', new_spec)
        self.assertEqual(new_profile.properties['adminPass'], None)
        res = profile.do_update(obj, new_profile)
        self.assertTrue(res)
        mock_update_image.assert_called_with(obj, 'FAKE_IMAGE',
                                             'FAKE_IMAGE_NEW',
                                             'adminpass')

    @mock.patch.object(server.ServerProfile, '_update_image')
    def test_do_update_image_failed(self, mock_update_image):
        ex = exception.InternalError(code=404,
                                     message='FAKE_IMAGE_NEW is not found')
        mock_update_image.side_effect = ex
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()
        new_spec = copy.deepcopy(self.spec)
        new_spec['properties']['image'] = 'FAKE_IMAGE_NEW'
        new_profile = server.ServerProfile('t', new_spec)

        res = profile.do_update(obj, new_profile)
        self.assertFalse(res)
        mock_update_image.assert_called_with(obj, 'FAKE_IMAGE',
                                             'FAKE_IMAGE_NEW',
                                             'adminpass')

    def test_update_image(self):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        mock_old_image = mock.Mock()
        mock_old_image.id = '123'
        mock_new_image = mock.Mock()
        mock_new_image.id = '456'
        novaclient = mock.Mock()
        novaclient.image_find.side_effect = [mock_old_image,
                                             mock_new_image]

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = novaclient
        profile._update_image(obj, 'old_image', 'new_image', 'adminpass')
        novaclient.image_find.has_calls(
            [mock.call('old_image'), mock.call('new_image')])
        novaclient.server_rebuild.assert_called_once_with('FAKE_ID',
                                                          '456',
                                                          'FAKE_SERVER_NAME',
                                                          'adminpass')

    def test_update_image_old_image_is_none(self):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        novaclient = mock.Mock()
        mock_server = mock.Mock()
        mock_server.image = {
            'id': '123',
            'link': {
                'href': 'http://openstack.example.com/openstack/images/123',
                'rel': 'bookmark'
            }
        }
        novaclient.server_get.return_value = mock_server
        mock_image = mock.Mock()
        mock_image.id = '456'
        novaclient.image_find.return_value = mock_image

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = novaclient
        profile._update_image(obj, None, 'new_image', 'adminpass')
        novaclient.image_find.assert_called_once_with('new_image')
        novaclient.server_get.assert_called_once_with('FAKE_ID')
        novaclient.server_rebuild.assert_called_once_with('FAKE_ID',
                                                          '456',
                                                          'FAKE_SERVER_NAME',
                                                          'adminpass')

    def test_update_image_new_image_is_none(self):
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        novaclient = mock.Mock()
        mock_image = mock.Mock()
        mock_image.id = '123'
        novaclient.image_find.return_value = mock_image

        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = novaclient
        ex = self.assertRaises(exception.ResourceUpdateFailure,
                               profile._update_image,
                               obj, 'old_image', None,
                               'adminpass')
        msg = _("Failed in updating FAKE_ID.")
        self.assertEqual(msg, six.text_type(ex))
        novaclient.image_find.assert_called_once_with('old_image')

    def test_do_update_no_physical_id(self):
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()

        test_server = mock.Mock()
        test_server.physical_id = None

        new_profile = mock.Mock()

        # Test path where server doesn't already exist
        res = profile.do_update(test_server, new_profile)
        self.assertTrue(res)

    def test_do_check(self):
        # Not implemented in server profile yet.
        profile = server.ServerProfile('t', self.spec)
        res = profile.do_check(mock.Mock())
        self.assertTrue(res)

    def test_do_get_details(self):
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        # Test normal path
        nova_server = mock.Mock()
        nova_server.to_dict.return_value = {
            'OS-DCF:diskConfig': 'MANUAL',
            'OS-EXT-AZ:availability_zone': 'nova',
            'OS-EXT-STS:power_state': 1,
            'OS-EXT-STS:task_state': None,
            'OS-EXT-STS:vm_state': 'active',
            'OS-SRV-USG:launched_at': 'TIMESTAMP1',
            'OS-SRV-USG:terminated_at': None,
            'accessIPv4': 'FAKE_IPV4',
            'accessIPv6': 'FAKE_IPV6',
            'addresses': {
                'private': [{
                    'OS-EXT-IPS-MAC:mac_addr': 'fa:16:3e:5e:00:81',
                    'version': 4,
                    'addr': '10.0.0.3',
                    'OS-EXT-IPS:type': 'fixed'
                }]
            },
            'config_drive': True,
            'created': 'CREATED_TIMESTAMP',
            'flavor': {
                'id': '1',
                'links': [{
                    'href': 'http://url_flavor',
                    'rel': 'bookmark'
                }]
            },
            'hostId': 'FAKE_HOST_ID',
            'id': 'FAKE_ID',
            'image': {
                'id': 'FAKE_IMAGE',
                'links': [{
                    'href': 'http://url_image',
                    'rel': 'bookmark'
                }],
            },
            'key_name': 'FAKE_KEY',
            'links': [{
                'href': 'http://url1',
                'rel': 'self'
            }, {
                'href': 'http://url2',
                'rel': 'bookmark'
            }],
            'metadata': {},
            'name': 'FAKE_NAME',
            'os-extended-volumes:volumes_attached': [],
            'progress': 0,
            'security_groups': [{'name': 'default'}],
            'status': 'FAKE_STATUS',
            'tenant_id': 'FAKE_TENANT',
            'updated': 'UPDATE_TIMESTAMP',
            'user_id': 'FAKE_USER_ID',
        }
        nc.server_get.return_value = nova_server
        res = profile.do_get_details(obj)
        expected = {
            'OS-DCF:diskConfig': 'MANUAL',
            'OS-EXT-AZ:availability_zone': 'nova',
            'OS-EXT-STS:power_state': 1,
            'OS-EXT-STS:task_state': '-',
            'OS-EXT-STS:vm_state': 'active',
            'OS-SRV-USG:launched_at': 'TIMESTAMP1',
            'OS-SRV-USG:terminated_at': '-',
            'accessIPv4': 'FAKE_IPV4',
            'accessIPv6': 'FAKE_IPV6',
            'config_drive': True,
            'created': 'CREATED_TIMESTAMP',
            'flavor': '1',
            'hostId': 'FAKE_HOST_ID',
            'id': 'FAKE_ID',
            'image': 'FAKE_IMAGE',
            'key_name': 'FAKE_KEY',
            'metadata': {},
            'name': 'FAKE_NAME',
            'os-extended-volumes:volumes_attached': [],
            'private network': '10.0.0.3',
            'progress': 0,
            'security_groups': 'default',
            'updated': 'UPDATE_TIMESTAMP',
            'status': 'FAKE_STATUS',
            'updated': 'UPDATE_TIMESTAMP',
        }
        self.assertEqual(expected, res)
        nc.server_get.assert_called_once_with('FAKE_ID')

    def test_do_get_details_with_no_network_or_sg(self):
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        # Test normal path
        nova_server = mock.Mock()
        nova_server.to_dict.return_value = {
            'addresses': {},
            'flavor': {
                'id': 'FAKE_FLAVOR',
            },
            'id': 'FAKE_ID',
            'image': {
                'id': 'FAKE_IMAGE',
            },
            'security_groups': [],
        }
        nc.server_get.return_value = nova_server
        res = profile.do_get_details(obj)
        expected = {
            'flavor': 'FAKE_FLAVOR',
            'id': 'FAKE_ID',
            'image': 'FAKE_IMAGE',
            'security_groups': '',
        }
        self.assertEqual(expected, res)
        nc.server_get.assert_called_once_with('FAKE_ID')

    def test_do_get_details_with_more_network_or_sg(self):
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        # Test normal path
        nova_server = mock.Mock()
        nova_server.to_dict.return_value = {
            'addresses': {
                'private': [{
                    'version': 4,
                    'addr': '10.0.0.3',
                }, {
                    'version': 4,
                    'addr': '192.168.43.3'
                }],
                'public': [{
                    'version': 4,
                    'addr': '172.16.5.3',
                }]
            },
            'flavor': {
                'id': 'FAKE_FLAVOR',
            },
            'id': 'FAKE_ID',
            'image': {
                'id': 'FAKE_IMAGE',
            },
            'security_groups': [{
                'name': 'default',
            }, {
                'name': 'webserver',
            }],
        }
        nc.server_get.return_value = nova_server
        res = profile.do_get_details(obj)
        expected = {
            'flavor': 'FAKE_FLAVOR',
            'id': 'FAKE_ID',
            'image': 'FAKE_IMAGE',
            'private network': ['10.0.0.3', '192.168.43.3'],
            'public network': '172.16.5.3',
            'security_groups': ['default', 'webserver'],
        }
        self.assertEqual(expected, res)
        nc.server_get.assert_called_once_with('FAKE_ID')

    def test_do_get_details_no_physical_id(self):
        # Test path for server not created
        profile = server.ServerProfile('t', self.spec)
        obj = mock.Mock()
        obj.physical_id = ''
        self.assertEqual({}, profile.do_get_details(obj))

        obj.physical_id = None
        self.assertEqual({}, profile.do_get_details(obj))

    def test_do_get_details_server_not_found(self):
        # Test path for server not created
        nc = mock.Mock()
        err = exception.InternalError(code=404,
                                      message='No Server found for ID')
        nc.server_get.side_effect = err
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        res = profile.do_get_details(obj)
        expected = {
            'Error': {
                'message': 'No Server found for ID',
                'code': 404
            }
        }
        self.assertEqual(expected, res)
        nc.server_get.assert_called_once_with('FAKE_ID')

    def test_do_join_successful(self):
        # Test normal path
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        nc.server_metadata_get.return_value = {'FOO': 'BAR'}
        nc.server_metadata_update.return_value = 'Boom'

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        cluster_id = "FAKE_CLUSTER_ID"

        res = profile.do_join(obj, cluster_id)
        self.assertEqual('Boom', res)
        nc.server_metadata_get.assert_called_once_with(server_id='FAKE_ID')
        nc.server_metadata_update.assert_called_once_with(
            FOO='BAR', cluster='FAKE_CLUSTER_ID')

    def test_do_join_server_not_created(self):
        # Test path where server not specified
        profile = server.ServerProfile('t', self.spec)
        obj = mock.Mock()
        obj.physical_id = None

        res = profile.do_join(obj, 'FAKE_CLUSTER_ID')
        self.assertEqual({}, res)

    def test_do_leave_successful(self):
        # Test normal path
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        nc.server_metadata_get.return_value = {'FOO': 'BAR', 'cluster': 'CLS'}
        nc.server_metadata_update.return_value = 'Boom'

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'
        obj.cluster_id = 'FAKE_CLUSTER_ID'

        res = profile.do_leave(obj)
        self.assertEqual('Boom', res)
        nc.server_metadata_get.assert_called_once_with(server_id='FAKE_ID')
        nc.server_metadata_update.assert_called_once_with(FOO='BAR')

    def test_do_leave_no_physical_id(self):
        # Test path where server not specified
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = mock.Mock()
        obj = mock.Mock()
        obj.physical_id = None
        self.assertIsNone(profile.do_leave(obj))

    def test_do_leave_not_in_cluster(self):
        # Test path where node is not in cluster
        nc = mock.Mock()
        profile = server.ServerProfile('t', self.spec)
        profile._novaclient = nc
        nc.server_metadata_get.return_value = {'FOO': 'BAR'}
        nc.server_metadata_update.return_value = 'Boom'

        obj = mock.Mock()
        obj.physical_id = 'FAKE_ID'

        res = profile.do_leave(obj)
        self.assertEqual('Boom', res)
        nc.server_metadata_get.assert_called_once_with(server_id='FAKE_ID')
        nc.server_metadata_update.assert_called_once_with(FOO='BAR')
