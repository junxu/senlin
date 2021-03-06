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

from oslo_context import context as oslo_context
from oslo_log import log as logging
from oslo_utils import timeutils

from senlin.common import context
from senlin.common import exception
from senlin.common.i18n import _
from senlin.common import schema
from senlin.common import utils
from senlin.db import api as db_api
from senlin.engine import environment

LOG = logging.getLogger(__name__)


class Profile(object):
    '''Base class for profiles.'''

    KEYS = (
        TYPE, VERSION, PROPERTIES,
    ) = (
        'type', 'version', 'properties',
    )

    spec_schema = {
        TYPE: schema.String(
            _('Name of the profile type.'),
            required=True,
        ),
        VERSION: schema.String(
            _('Version number of the profile type.'),
            required=True,
        ),
        PROPERTIES: schema.Map(
            _('Properties for the profile.'),
            required=True,
        )
    }

    properties_schema = {}

    def __new__(cls, name, spec, **kwargs):
        """Create a new profile of the appropriate class.

        :param name: The name for the profile.
        :param spec: A dictionary containing the spec for the profile.
        :param kwargs: Keyword arguments for profile creation.
        :returns: An instance of a specific sub-class of Profile.
        """
        type_name, version = schema.get_spec_version(spec)

        if cls != Profile:
            ProfileClass = cls
        else:
            ProfileClass = environment.global_env().get_profile(type_name)

        return super(Profile, cls).__new__(ProfileClass)

    def __init__(self, name, spec, **kwargs):
        """Initialize a profile instance.

        :param name: A string that specifies the name for the profile.
        :param spec: A dictionary containing the detailed profile spec.
        :param kwargs: Keyword arguments for initializing the profile.
        :returns: An instance of a specific sub-class of Profile.
        """

        type_name, version = schema.get_spec_version(spec)

        self.name = name
        self.spec = spec

        self.id = kwargs.get('id', None)
        self.type = kwargs.get('type', '%s-%s' % (type_name, version))

        self.user = kwargs.get('user')
        self.project = kwargs.get('project')
        self.domain = kwargs.get('domain')

        self.permission = kwargs.get('permission', '')
        self.metadata = kwargs.get('metadata', {})

        self.created_time = kwargs.get('created_time', None)
        self.updated_time = kwargs.get('updated_time', None)
        self.deleted_time = kwargs.get('deleted_time', None)

        self.spec_data = schema.Spec(self.spec_schema, self.spec)
        self.properties = schema.Spec(self.properties_schema,
                                      self.spec.get(self.PROPERTIES, {}))

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
            'type': record.type,
            'context': record.context,
            'user': record.user,
            'project': record.project,
            'domain': record.domain,
            'permission': record.permission,
            'metadata': record.meta_data,
            'created_time': record.created_time,
            'updated_time': record.updated_time,
            'deleted_time': record.deleted_time,
        }

        return cls(record.name, record.spec, **kwargs)

    @classmethod
    def load(cls, ctx, profile_id=None, profile=None, project_safe=True):
        '''Retrieve a profile object from database.'''
        if profile is None:
            profile = db_api.profile_get(ctx, profile_id,
                                         project_safe=project_safe)
            if profile is None:
                raise exception.ProfileNotFound(profile=profile_id)

        return cls.from_db_record(profile)

    @classmethod
    def load_all(cls, ctx, limit=None, sort_keys=None, marker=None,
                 sort_dir=None, filters=None, show_deleted=False,
                 project_safe=True):
        '''Retrieve all profiles from database.'''

        records = db_api.profile_get_all(ctx, limit=limit, marker=marker,
                                         sort_keys=sort_keys,
                                         sort_dir=sort_dir,
                                         filters=filters,
                                         show_deleted=show_deleted,
                                         project_safe=project_safe)

        for record in records:
            yield cls.from_db_record(record)

    @classmethod
    def delete(cls, ctx, profile_id):
        db_api.profile_delete(ctx, profile_id)

    def store(self, ctx):
        '''Store the profile into database and return its ID.'''
        timestamp = timeutils.utcnow()

        values = {
            'name': self.name,
            'type': self.type,
            'context': self.context,
            'spec': self.spec,
            'user': self.user,
            'project': self.project,
            'domain': self.domain,
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
    def update_object(cls, ctx, obj, new_profile_id=None, **params):
        profile = cls.load(ctx, obj.profile_id)
        new_profile = None
        if new_profile_id:
            new_profile = cls.load(ctx, new_profile_id)
        return profile.do_update(obj, new_profile, **params)

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
        # general validation
        self.spec_data.validate()
        self.properties.validate()

        # TODO(Anyone): need to check the contents in self.CONTEXT

    @classmethod
    def get_schema(cls):
        return dict((name, dict(schema))
                    for name, schema in cls.properties_schema.items())

    def _init_context(self):
        profile_context = {}
        if self.CONTEXT in self.spec_data:
            profile_context = self.spec_data[self.CONTEXT] or {}

        ctx_dict = context.get_service_context(**profile_context)

        ctx_dict.pop('project_name')
        ctx_dict.pop('project_domain_name')

        return ctx_dict

    def _build_conn_params(self, user, project):
        """Build connection params for specific user and project.

        :param user: The ID of the user for which a trust will be used.
        :param project: The ID of the project for which a trust will be used.
        :returns: A dict containing the required parameters for connection
                  creation.
        """
        cred = db_api.cred_get(oslo_context.get_current(), user, project)
        if cred is None:
            raise exception.TrustNotFound(trustor=user)

        trust_id = cred.cred['openstack']['trust']

        # This is supposed to be trust-based authentication
        params = copy.deepcopy(self.context)
        params['trust_id'] = trust_id

        return params

    def do_create(self, obj):
        '''For subclass to override.'''

        return NotImplemented

    def do_delete(self, obj):
        '''For subclass to override.'''

        return NotImplemented

    def do_update(self, obj, new_profile, **params):
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
        pb_dict = {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'user': self.user,
            'project': self.project,
            'domain': self.domain,
            'permission': self.permission,
            'spec': self.spec,
            'metadata': self.metadata,
            'created_time': utils.format_time(self.created_time),
            'updated_time': utils.format_time(self.updated_time),
            'deleted_time': utils.format_time(self.deleted_time),
        }
        return pb_dict

    @classmethod
    def from_dict(cls, **kwargs):
        type_name = kwargs.pop('type')
        name = kwargs.pop('name')
        return cls(type_name, name, kwargs)
