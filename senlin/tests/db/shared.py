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

import json
import uuid

from senlin.db.sqlalchemy import api as db_api
from senlin.engine import parser

sample_profile_type = '''
  name: my_test_profile_type
  type: os.heat.stack
  spec:
    template:
      get_file: template_file
    files:
      fname: contents
'''

UUIDs = (UUID1, UUID2, UUID3) = sorted([str(uuid.uuid4())
                                        for x in range(3)])


def create_profile(context, profile_type, **kwargs):
    data = parser.parse_profile(sample_profile_type)
    values = {
        'name': 'test_profile_name',
        'type': profile_type,
        'spec': {
            'template': {
                'heat_template_version': '2013-05-23',
                'resources': {
                    'myrandom': 'OS::Heat::RandomString',
                }
            },
            'files': {'input_file': 'template_file'}
        },
        'permission': 'xxxyyy',
    }
    data.update(values)
    return db_api.profile_create(context, values)


def create_cluster(ctx, profile, **kwargs):
    values = {
        'name': 'db_test_cluster_name',
        'profile_id': profile.id,
        'user': ctx.user,
        'project': ctx.tenant_id,
        'domain': 'unknown',
        'parent': None,
        'next_index': 0,
        'timeout': '60',
        'status': 'INIT',
        'status_reason': 'Just Initialized'
    }
    values.update(kwargs)
    if 'tenant_id' in kwargs:
        values.update({'project': kwargs.get('tenant_id')})
    return db_api.cluster_create(ctx, values)


def create_node(ctx, cluster, profile, **kwargs):
    values = {
        'name': 'test_node_name',
        'physical_id': UUID1,
        'cluster_id': cluster.id,
        'profile_id': profile.id,
        'index': 0,
        'role': None,
        'created_time': None,
        'updated_time': None,
        'deleted_time': None,
        'status': 'ACTIVE',
        'status_reason': 'create complete',
        'tags': json.loads('{"foo": "123"}'),
        'data': json.loads('{"key1": "value1"}'),
    }
    values.update(kwargs)
    return db_api.node_create(ctx, values)