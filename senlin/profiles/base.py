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

import copy
import datetime

from oslo_log import log as logging

from senlin.common import exception
from senlin.common import schema
from senlin.db import api as db_api
from senlin.drivers.openstack import keystone_v3 as keystoneclient
from senlin.engine import environment

LOG = logging.getLogger(__name__)


class Profile(object):
    '''Base class for profiles.'''

    def __new__(cls, type_name, name, **kwargs):
        '''Create a new profile of the appropriate class.'''

        if cls != Profile:
            ProfileClass = cls
        else:
            ProfileClass = environment.global_env().get_profile(type_name)

        return super(Profile, cls).__new__(ProfileClass)

    def __init__(self, type_name, name, **kwargs):
        '''Initialize the profile with given parameters and a JSON object.

        :param type_name: a string containing valid profile type name;
        :param name: a string that specifies the name for the profile.
        '''

        self.id = kwargs.get('id', None)
        self.name = name
        self.type = type_name

        self.spec = kwargs.get('spec', None)
        self.spec_data = schema.Spec(self.spec_schema, self.spec)

        self.permission = kwargs.get('permission', '')
        self.metadata = kwargs.get('metadata', {})
        self.created_time = kwargs.get('created_time', None)
        self.updated_time = kwargs.get('updated_time', None)
        self.deleted_time = kwargs.get('deleted_time', None)

        if not self.id:
            # new object needs a context dict
            self.context = self._init_context()
        else:
            self.context = kwargs.get('context')

    @classmethod
    def from_db_record(cls, record):
        '''Construct a profile object from database record.

        :param record: a DB Profle object that contains all required fields.
        '''
        kwargs = {
            'id': record.id,
            'context': record.context,
            'spec': record.spec,
            'permission': record.permission,
            'metadata': record.meta_data,
            'created_time': record.created_time,
            'updated_time': record.updated_time,
            'deleted_time': record.deleted_time,
        }

        return cls(record.type, record.name, **kwargs)

    @classmethod
    def load(cls, ctx, profile_id=None, profile=None):
        '''Retrieve a profile object from database.'''
        if profile is None:
            profile = db_api.profile_get(ctx, profile_id)
            if profile is None:
                raise exception.ProfileNotFound(profile=profile_id)

        return cls.from_db_record(profile)

    @classmethod
    def load_all(cls, ctx, limit=None, sort_keys=None, marker=None,
                 sort_dir=None, filters=None, show_deleted=False):
        '''Retrieve all profiles from database.'''

        records = db_api.profile_get_all(ctx, limit=limit, marker=marker,
                                         sort_keys=sort_keys,
                                         sort_dir=sort_dir,
                                         filters=filters,
                                         show_deleted=show_deleted)

        for record in records:
            yield cls.from_db_record(record)

    @classmethod
    def delete(cls, ctx, profile_id):
        db_api.profile_delete(ctx, profile_id)

    def store(self, ctx):
        '''Store the profile into database and return its ID.'''
        timestamp = datetime.datetime.utcnow()

        values = {
            'name': self.name,
            'type': self.type,
            'context': self.context,
            'spec': self.spec,
            'permission': self.permission,
            'meta_data': self.metadata,
        }

        if self.id:
            self.updated_time = timestamp
            values['updated_time'] = timestamp
            db_api.profile_update(ctx, self.id, values)
        else:
            self.created_time = timestamp
            values['created_time'] = timestamp
            profile = db_api.profile_create(ctx, values)
            self.id = profile.id

        return self.id

    @classmethod
    def create_object(cls, ctx, obj):
        profile = cls.load(ctx, obj.profile_id)
        return profile.do_create(obj)

    @classmethod
    def delete_object(cls, ctx, obj):
        profile = cls.load(ctx, obj.profile_id)
        return profile.do_delete(obj)

    @classmethod
    def update_object(cls, ctx, obj, new_profile_id):
        profile = cls.load(ctx, obj.profile_id)
        new_profile = cls.load(ctx, new_profile_id)
        return profile.do_update(obj, new_profile)

    @classmethod
    def get_details(cls, ctx, obj):
        profile = cls.load(ctx, obj.profile_id)
        return profile.do_get_details(obj)

    @classmethod
    def join_cluster(cls, ctx, obj, cluster_id):
        profile = cls.load(ctx, obj.profile_id)
        return profile.do_join(obj, cluster_id)

    @classmethod
    def leave_cluster(cls, ctx, obj):
        profile = cls.load(ctx, obj.profile_id)
        return profile.do_leave(obj)

    def validate(self):
        '''Validate the schema and the data provided.'''
        self.spec_data.validate()

    def _init_context(self):
        cred = keystoneclient.get_service_credentials()
        cntx = {
            'auth_url': cred['auth_url'],
            'user_name': cred['user_name'],
            'user_domain_name': cred['user_domain_name'],
            'password': cred['password'],
        }

        if self.CONTEXT in self.spec_data:
            profile_context = self.spec_data[self.CONTEXT]
            if profile_context:
                # TODO(Anyone): need to check the contents in self.CONTEXT
                cntx.update(profile_context)
        return cntx

    def _get_connection_params(self, ctx, obj):
        cred = db_api.cred_get(ctx, obj.user, obj.project)
        if cred is None:
            # TODO(Anyone): this exception type makes no sense to end user,
            # need to translate it at a higher layer
            raise exception.TrustNotFound(trustor=obj.user)

        trust_id = cred.cred['openstack']['trust']

        params = copy.deepcopy(self.context)
        params['project_id'] = obj.project
        params['trusts'] = trust_id

        return params

    def do_create(self, obj):
        '''For subclass to override.'''

        return NotImplemented

    def do_delete(self, obj):
        '''For subclass to override.'''

        return NotImplemented

    def do_update(self, obj, new_profile):
        '''For subclass to override.'''

        return NotImplemented

    def do_check(self, obj):
        '''For subclass to override.'''
        return NotImplemented

    def do_get_details(self, obj):
        '''For subclass to override.'''
        return NotImplemented

    def do_join(self, obj, cluster_id):
        '''For subclass to override.'''
        return NotImplemented

    def do_leave(self, obj):
        '''For subclass to override.'''
        return NotImplemented

    def to_dict(self):
        def _fmt_time(value):
            return value and value.isoformat()

        pb_dict = {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'permission': self.permission,
            'spec': self.spec,
            'metadata': self.metadata,
            'created_time': _fmt_time(self.created_time),
            'updated_time': _fmt_time(self.updated_time),
            'deleted_time': _fmt_time(self.deleted_time),
        }
        return pb_dict

    @classmethod
    def from_dict(cls, **kwargs):
        return cls(kwargs)
