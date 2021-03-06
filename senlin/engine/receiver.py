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

from oslo_utils import timeutils

from senlin.common import exception
from senlin.common import utils
from senlin.db import api as db_api


class Receiver(object):
    """Create a Receiver which is used to trigger a cluster action."""

    def __init__(self, rtype, cluster_id, action, **kwargs):

        self.id = kwargs.get('id', None)
        self.name = kwargs.get('name', None)
        self.type = rtype
        self.user = kwargs.get('user', '')
        self.project = kwargs.get('project', '')
        self.domain = kwargs.get('domain', '')

        self.created_time = kwargs.get('created_time', None)
        self.updated_time = kwargs.get('updated_time', None)
        self.deleted_time = kwargs.get('deleted_time', None)

        self.cluster_id = cluster_id
        self.action = action
        self.actor = kwargs.get('actor', {})
        self.params = kwargs.get('params', {})
        self.channel = kwargs.get('channel', {})

    def store(self, context):
        """Store the receiver in database and return its ID.

        :param context: Context for DB operations.
        """
        self.created_time = timeutils.utcnow()
        values = {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'user': self.user,
            'project': self.project,
            'domain': self.domain,
            'created_time': self.created_time,
            'updated_time': self.updated_time,
            'deleted_time': self.deleted_time,
            'cluster_id': self.cluster_id,
            'actor': self.actor,
            'action': self.action,
            'params': self.params,
            'channel': self.channel,
        }

        # TODO(Qiming): Add support to update
        receiver = db_api.receiver_create(context, values)
        self.id = receiver.id

        return self.id

    @classmethod
    def create(cls, context, rtype, cluster, action, **kwargs):
        cdata = dict()
        if context.is_admin:
            # use object owner if request is from admin
            cred = db_api.cred_get(context, cluster.user, cluster.project)
            trust_id = cred['cred']['openstack']['trust']
            cdata['trust_id'] = [trust_id]
        else:
            # otherwise, use context user
            cdata['trust_id'] = [context.trusts]

        kwargs['actor'] = cdata
        kwargs['user'] = context.user
        kwargs['project'] = context.project
        kwargs['domain'] = context.domain

        obj = cls(rtype, cluster.id, action, **kwargs)
        obj.initialize_channel()
        obj.store(context)

        return obj

    @classmethod
    def _from_db_record(cls, record):
        """Construct a receiver object from database record.

        :param record: a DB receiver object that will receive all fields.
        """
        kwargs = {
            'id': record.id,
            'name': record.name,
            'user': record.user,
            'project': record.project,
            'domain': record.domain,
            'created_time': record.created_time,
            'updated_time': record.updated_time,
            'deleted_time': record.deleted_time,
            'actor': record.actor,
            'params': record.params,
            'channel': record.channel,
        }

        return cls(record.type, record.cluster_id, record.action, **kwargs)

    @classmethod
    def load(cls, context, receiver_id=None, receiver_obj=None,
             show_deleted=False, project_safe=True):
        """Retrieve a receiver from database.

        :param context: the context for db operations.
        :param receiver_id: the unique ID of the receiver to retrieve.
        :param receiver_obj: the DB object of a receiver to retrieve.
        :param show_deleted: boolean indicating whether deleted objects
                             should be returned or not. Default is False.
        :param project_safe: Optional parameter specifying whether only
                             receiver belong to the context.project will be
                             loaded.
        """
        if receiver_obj is None:
            receiver = db_api.receiver_get(context, receiver_id,
                                           show_deleted=show_deleted,
                                           project_safe=project_safe)
            if receiver is None:
                raise exception.ReceiverNotFound(receiver=receiver_id)

        return cls._from_db_record(receiver)

    @classmethod
    def load_all(cls, context, limit=None, marker=None, sort_keys=None,
                 sort_dir=None, filters=None, show_deleted=False,
                 project_safe=True):
        """Retrieve all receivers from database."""

        records = db_api.receiver_get_all(context, show_deleted=show_deleted,
                                          limit=limit, marker=marker,
                                          sort_keys=sort_keys,
                                          sort_dir=sort_dir,
                                          filters=filters,
                                          project_safe=project_safe)

        for record in records:
            receiver = cls._from_db_record(record)
            yield receiver

    def to_dict(self):
        info = {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'user': self.user,
            'project': self.project,
            'domain': self.domain,
            'created_time': utils.format_time(self.created_time),
            'updated_time': utils.format_time(self.updated_time),
            'deleted_time': utils.format_time(self.deleted_time),
            'cluster_id': self.cluster_id,
            'actor': self.actor,
            'action': self.action,
            'params': self.params,
            'channel': self.channel,
        }
        return info

    def initialize_channel(self):
        return


class Webhook(Receiver):
    """Webhook flavor of receivers."""

    def initialize_channel(self):
        # key = receiver.encrypt_credential()
        # url, token = receiver.generate_url(key)

        channel = {}
        self.channel = channel
        return channel
