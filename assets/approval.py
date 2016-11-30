#!/usr/bin/env python3

from datetime import (datetime, timezone)
from flywheel import Model, Field, Engine
from decimal import Decimal
import simplejson as json
import logging as log
import os
import sys
import tempfile
import time
import uuid


class Approval(Model):
    __metadata__ = {
        '_name': 'concourse-approval',
        'throughput': {
            'read': 1,
            'write': 1,
        }
    }
    id = Field(type=str, range_key=True)
    lockname = Field()
    pool = Field(hash_key=True)
    timestamp = Field(type=datetime, index='ts-index')
    claimed = Field(type=bool)
    need_approval = Field(type=bool, default=False)
    approved = Field(type=bool, nullable=True)
    team = Field()
    pipeline = Field()
    description = Field(type=str, nullable=True)


class ApprovalResource:
    """Approval resource implementation."""

    def __init__(self, command_name, json_data, command_argument):
        self.command_name = command_name
        self.command_argument = command_argument
        # Namespace the approval lock
        self.data = json.loads(json_data)
        self.wait_lock = 10
        self.pool = ''
        self.engine = Engine()

        # allow debug logging to console for tests
        if os.getenv('RESOURCE_DEBUG', False) or self.data.get('source', {}).get('debug', False):
            log.basicConfig(level=log.DEBUG)
        else:
            logfile = tempfile.NamedTemporaryFile(delete=False, prefix='log')
            log.basicConfig(level=log.DEBUG, filename=logfile.name)
        stderr = log.StreamHandler()
        stderr.setLevel(log.INFO)
        log.getLogger().addHandler(stderr)

        log.debug('command: %s', command_name)
        log.debug('input: %s', self.data)
        log.debug('args: %s', command_argument)
        log.debug('environment: %s', os.environ)

    def check_cmd(self, source, version):
        """
        Check for new version(s)
        This function will look on Dynamodb if there's a lock with the given prefix
        and will return the last timestamp associated
        """

        log.debug('version: %s', version)

        if not version:
            version = {"timestamp": '0'}
        log.debug('source: %s', source)
        log.debug('version: %s', version)
        approval_locks = self.engine.query(Approval)\
            .filter(
                Approval.timestamp >= datetime.fromtimestamp(Decimal(version.get('timestamp'))),
                pool=self.pool) \
            .index('ts-index') \
            .all()
        versions_list = []
        for lock in approval_locks:
            versions_list.append({"timestamp": "{timestamp}".format(timestamp=Decimal(lock.timestamp.timestamp()))})
        if not approval_locks:
            versions_list.append(version)
        log.debug(versions_list)
        return versions_list

    def in_cmd(self, target_dir, source, version, params):
        log.debug('source: %s', source)
        log.debug('version: %s', version)

        if not version:
            version = {"timestamp": 0}

        if params:
            need_approval = params.get('need_approval', False)

        # Does the get should wait for an approval or not ?
        if 'lock_name' in params and 'need_approval' in params:
            approval_lock = self.engine.query(Approval) \
                .filter(
                    pool=self.pool,
                    lockname=params.get('lock_name')) \
                .index('ts-index') \
                .first(desc=True)

            # We want to wait until the approve is done
            if approval_lock.need_approval:
                while approval_lock.approved is None:
                    log.info("The lock %s is waiting for an approval" % params['lock_name'])
                    refresh_approval = self.query_lock(params['lock_name'])
                    if refresh_approval.approved:
                        approval_lock.approved = True
                    if refresh_approval.approved is False:
                        log.info("The lock hasn't been approved, exiting")
                        approval_lock.claimed = False
                        approval_lock.approved = None
                        self.engine.save(approval_lock, overwrite=True)
                        exit(1)
                    else:
                        time.sleep(self.wait_lock)
        else:
            approval_lock = self.engine.query(Approval)\
                .filter(
                    Approval.timestamp >= datetime.fromtimestamp(Decimal(version.get('timestamp'))),
                    pool=self.pool)\
                .index('ts-index')\
                .first(desc=True)

        metadata = []
        if approval_lock:
            for key in approval_lock.keys_():
                value = getattr(approval_lock, key)
                if type(value) is datetime:
                    value = str(Decimal(value.timestamp()))
                if type(value) is bool:
                    value = str(value)
                metadata.append(
                    {
                        'name': key,
                        'value': value
                    }
                )

            name_path = os.path.join(target_dir, 'name')
            with open(name_path, 'w') as name:
                name.write(getattr(approval_lock, 'lockname'))

            metadata_path = os.path.join(target_dir, 'metadata')
            with open(metadata_path, 'w') as metadata_file:
                json.dump(metadata, metadata_file)
        else:
            log.info("No lock have been found")
            exit(0)

        return {
            'version': {"timestamp": "{timestamp}".format(timestamp=Decimal(getattr(approval_lock, 'timestamp').timestamp()))},
            'metadata': metadata,
        }

    def query_lock(self, lock_name):
        return self.engine.query(Approval) \
            .filter(
                lockname=lock_name,
                pool=self.pool) \
            .index('ts-index') \
            .first(desc=True)

    def out_cmd(self, target_dir, source, params):
        metadata = []

        if 'lock_name' not in params:
            log.error('You must set a lock_name on params')
        if 'action' not in params:
            log.error('You must set an action on params')

        approval_lock = self.query_lock(params['lock_name'])
        need_approval = params.get('need_approval', False)

        if 'claim' in params['action']:
            if approval_lock:
                # If the lock is claimed in database
                if approval_lock.claimed:
                    # We want to wait until the lock is not claimed
                    while approval_lock.claimed:
                        log.debug("The lock %s is already claimed" % params['lock_name'])
                        refresh_approval = self.query_lock(params['lock_name'])
                        if not refresh_approval:
                            log.info("The lock does not exist")
                            exit(1)
                        if not refresh_approval.claimed:
                            approval_lock.claimed = False
                        else:
                            time.sleep(self.wait_lock)

                if need_approval:
                    approval_lock.need_approval = True
                approval_lock.claimed = True
                approval_lock.timestamp = datetime.utcnow()
                self.engine.save(approval_lock, overwrite=True)
            else:
                approval_lock = Approval(
                    id=uuid.uuid4().urn[9:],
                    lockname=params['lock_name'],
                    pool=self.pool,
                    timestamp=datetime.utcnow(),
                    claimed=True,
                    team=os.getenv('BUILD_TEAM_NAME', "team"),
                    pipeline=os.getenv('BUILD_PIPELINE_NAME', "pipeline"),
                    description=params.get('description', None)
                )
                if need_approval:
                    approval_lock.need_approval = True
                self.engine.save(approval_lock, overwrite=True)
                log.debug('Writing the lock')
                log.debug(approval_lock)
        elif 'release' in params['action']:
            if approval_lock:
                approval_lock.claimed = False
                approval_lock.approved = None
                approval_lock.timestamp = datetime.utcnow()
                self.engine.save(approval_lock, overwrite=True)
            else:
                log.info("The lock does not exist")
                exit(1)
        else:
            log.error('Please use an available action')
            exit(1)

        metadata = []
        for key in approval_lock.keys_():
            value = getattr(approval_lock, key)
            if type(value) is datetime:
                value = str(Decimal(value.timestamp()))
            if type(value) is bool:
                value = str(value)

            metadata.append(
                {
                    'name': key,
                    'value': value
                }
            )

        name_path = os.path.join(target_dir, 'name')
        with open(name_path, 'w') as name:
            name.write(approval_lock.lockname)

        metadata_path = os.path.join(target_dir, 'metadata')
        with open(metadata_path, 'w') as metadata_file:
            json.dump(metadata, metadata_file)

        return {
            'version': {"timestamp": "{timestamp}".format(timestamp=Decimal(approval_lock.timestamp.timestamp()))},
            'metadata': metadata,
        }

    def run(self):
        """Parse input/arguments, perform requested command return output."""
        # combine source and params
        source = self.data.get('source', {})
        params = self.data.get('params', {})
        version = self.data.get('version', {})

        os.environ['AWS_ACCESS_KEY_ID'] = source.get('AWS_ACCESS_KEY_ID', '')
        os.environ['AWS_SECRET_ACCESS_KEY'] = source.get('AWS_SECRET_ACCESS_KEY', '')
        os.environ['AWS_DEFAULT_REGION'] = source.get('AWS_DEFAULT_REGION', "eu-west-1")
        self.wait_lock = source.get('wait_lock', 10)

        if 'pool' not in source:
            log.error("pool must exist in the source configuration")
            exit(1)
        else:
            self.pool = source.get('pool')

        self.engine.connect_to_region(os.environ.get('AWS_DEFAULT_REGION', 'eu-west-1'))
        # Register our model with the engine so it can create the Dynamo table
        self.engine.register(Approval)
        # Create the dynamo table for our registered model
        self.engine.create_schema()

        if self.command_name == 'check':
            response = self.check_cmd(source, version)
        elif self.command_name == 'in':
            response = self.in_cmd(self.command_argument[0], source, version, params)
        else:
            response = self.out_cmd(self.command_argument[0], source, params)

        return json.dumps(response)

print(ApprovalResource(os.path.basename(__file__), sys.stdin.read(), sys.argv[1:]).run())
