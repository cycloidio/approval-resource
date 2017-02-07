#!/usr/bin/env python3

from datetime import (datetime, timezone, timedelta)
from flywheel import Model, Field, Engine
from flywheel.fields.types import DateTimeType
from decimal import Decimal
import simplejson as json
import logging as log
import os
import sys
import tempfile
import time
import uuid


class Approval(Model):
    """
        We define the dynamodb model of the Approval locks
    """

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
    timestamp = Field(data_type=DateTimeType(naive=True))
    claimed = Field(type=bool)
    need_approval = Field(type=bool, default=False)
    approved = Field(type=bool, nullable=True)
    team = Field()
    pipeline = Field()
    description = Field(type=str, nullable=True)


class ApprovalResource:
    """
        Approval resource implementation.
        This python script is the target of symbolic links and is used for check, in and out.
        These three commands are defined as methods on this class and common parameters live in the constructor.
        To enable the debug output, the resource source configuration must have the debug parameter.

    """

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
        This function will look on Dynamodb if there's a lock within the pool
        and will return the last timestamps associated.
        :param source: is an arbitrary JSON object which specifies the location of the resource,
        including any credentials. This is passed verbatim from the pipeline configuration.
        :param version: is a JSON object with string fields, used to uniquely identify an instance of the resource.
        :return: a dict with the version fetched
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
            .all()
        versions_list = []
        for lock in approval_locks:
            versions_list.append({"timestamp": "{timestamp}".format(timestamp=Decimal(lock.timestamp.timestamp()))})
        if not approval_locks:
            versions_list.append(version)
        log.debug(versions_list)
        return versions_list

    def in_cmd(self, target_dir, source, version, params):
        """
        This function will fetch a lock in dynamodb an write it in the target directory.
        If parameters lock_name and need_approval are passed, then the function will wait for a change
        on the dynamodb lock item.
        :param target_dir: a temporary directory which will be exposed as an output
        :param source: is the same value as passed to check
        :param version: is the same type of value passed to check, and specifies the version to fetch.
        :param params: is an arbitrary JSON object passed along verbatim from params on a get.
        :return: a dict with the version fetched and the metadata of the lock
        """
        log.debug('source: %s', source)
        log.debug('version: %s', version)

        if not version:
            version = {"timestamp": 0}

        # Does the get should wait for an approval or not ?
        if 'lock_name' in params and 'need_approval' in params:
            log.debug('Looking for the lock %s in the pool %s' % (params.get('lock_name'), self.pool))
            approval_lock = self.query_lock(params.get('lock_name'))
            if approval_lock:
                # We want to wait until the approve is done
                while approval_lock.approved is None and approval_lock.need_approval:
                    # Query the lock item in the loop
                    refresh_approval = self.query_lock(lock_name=params['lock_name'])

                    # If the lock has timed out, then we override the refresh_approval to simulate a reject
                    if 'timeout' in params:
                        if approval_lock.timestamp + timedelta(minutes=params['timeout']) <= datetime.now():
                            refresh_approval.approved = False
                        countdown = (approval_lock.timestamp.replace(microsecond=0) +
                                     timedelta(minutes=params['timeout'])) - datetime.now().replace(microsecond=0)
                        timedelta(minutes=params['timeout']).total_seconds() / self.wait_lock
                        if countdown.days >= 0:
                            log.info("The lock %s is waiting for an approval. There is %s left" %
                                     (params['lock_name'], str(countdown)))
                    else:
                        log.info("The lock %s is waiting for an approval" % params['lock_name'])
                    # If hasn't been approved or rejected, waiting a bit more
                    if refresh_approval.approved is None:
                        time.sleep(self.wait_lock)
                        continue

                    # Is it approved ?
                    if refresh_approval.approved:
                        approval_lock.approved = True
                    else:
                        approval_lock.approved = False
                # If the lock has been rejected we should fail the job and release the lock
                if not approval_lock.approved:
                    log.info("The lock hasn't been approved, exiting")
                    approval_lock.claimed = False
                    approval_lock.approved = None
                    self.engine.save(approval_lock, overwrite=True)
                    exit(1)
        elif 'lock_name' in params:
            approval_lock = self.query_lock(params.get('lock_name'))
        else:
            # There is no approval, we have just a normal lock. Let's fetch the lock
            approval_lock = self.engine.query(Approval)\
                .filter(
                    Approval.timestamp >= datetime.fromtimestamp(Decimal(version.get('timestamp'))),
                    pool=self.pool)\
                .all()
            if approval_lock:
                approval_lock = approval_lock[0]

        metadata = []

        if not approval_lock:
            log.info("No lock have been found")
            exit(0)
        for key in approval_lock.keys_():
            value = getattr(approval_lock, key)
            if type(value) is datetime:
                value = str(Decimal(value.timestamp()))
            elif type(value) is bool:
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

        return {
            'version': {"timestamp": "{timestamp}".format(
                timestamp=Decimal(getattr(approval_lock, 'timestamp').timestamp()))},
            'metadata': metadata,
        }

    def _do_claim(self, params):
        """
        This method handle the claiming of a lock. If the lock is already claimed, it wait until the lock is
        available. Else, it create the lock.
        :param params: the params passed as parameters of the resource
        :return: the approval_lock item in dynamodb
        """
        approval_lock = self.query_lock(lock_name=params['lock_name'])
        need_approval = params.get('need_approval', False)
        override_approval = params.get('override_approval', False)

        if approval_lock:
            # To override the previous approval, we need to reject the previous one
            # Then the get will see it was rejected, will release the lock and fail the job
            if override_approval:
                approval_lock.approved = False
                approval_lock.timestamp = datetime.now()
                self.engine.save(approval_lock, overwrite=True)
                log.info("Rejecting the previous approval")
                # Let the get fail before acquiring the new lock
                time.sleep(self.wait_lock + 5)

            # We want to wait until the lock is not claimed
            while approval_lock.claimed:
                log.info("The lock %s is already claimed" % params['lock_name'])
                refresh_approval = self.query_lock(lock_name=params['lock_name'])
                if not refresh_approval:
                    log.info("The lock does not exist")
                    exit(1)
                if not refresh_approval.claimed:
                    approval_lock.claimed = False
                else:
                    time.sleep(self.wait_lock)

        else:
            approval_lock = Approval(
                id=uuid.uuid4().urn[9:],
                lockname=params['lock_name'],
                pool=self.pool,
                claimed=True,
                team=os.getenv('BUILD_TEAM_NAME', "team"),
                pipeline=os.getenv('BUILD_PIPELINE_NAME', "pipeline"),
                description=params.get('description', None)
            )

        if need_approval:
            approval_lock.need_approval = True
        approval_lock.claimed = True
        approval_lock.approved = None
        approval_lock.timestamp = datetime.now()
        self.engine.save(approval_lock, overwrite=True)
        log.info("Claiming the lock %s" % params['lock_name'])

        return approval_lock

    def _do_release(self, params):
        """
        This method handle the release of a claimed lock

        :param params: the params passed as parameters of the resource
        :return: the approval_lock item in dynamodb
        """
        approval_lock = self.query_lock(lock_name=params['lock_name'])

        if not approval_lock:
            log.info("The lock does not exist")
            exit(1)

        approval_lock.claimed = False
        approval_lock.approved = None
        approval_lock.timestamp = datetime.now()
        self.engine.save(approval_lock, overwrite=True)
        log.info("Releasing the lock %s" % params['lock_name'])

        return approval_lock

    def query_lock(self, lock_name):
        """
        This method is used to query the lock in the approval loop to check if there is a change on it
        :param lock_name: The name of the lock to fetch
        :return: the dynamodb item
        """
        approval_lock = self.engine.query(Approval) \
            .filter(
                lockname=lock_name,
                pool=self.pool) \
            .all()
        if approval_lock:
            approval_lock = approval_lock[0]
        return approval_lock

    def out_cmd(self, target_dir, source, params):
        """
        This method is responsible to acquire or release a lock. If the lock doesn't exist yet, then the method
        create it automatically.
        If a lock is already acquired, the method will wait indefinitely until being able to acquire it.
        :param target_dir:
        :param source: is the same value as passed to check.
        :param params: is an arbitrary JSON object passed along verbatim from params on a put.
        :return: a dict with the version fetched and the metadata of the lock
        """
        metadata = []

        if 'lock_name' not in params:
            log.error('You must set a lock_name on params')
        if 'action' not in params:
            log.error('You must set an action on params')

        if 'claim' in params['action']:
            approval_lock = self._do_claim(params=params)
        elif 'release' in params['action']:
            approval_lock = self._do_release(params=params)
        else:
            log.error('Please use an available action')
            exit(1)

        metadata = []
        for key in approval_lock.keys_():
            value = getattr(approval_lock, key)
            if type(value) is datetime:
                value = str(Decimal(value.timestamp()))
            elif type(value) is bool:
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
            'version': {"timestamp": "{timestamp}".format(
                timestamp=Decimal(approval_lock.timestamp.timestamp()))},
            'metadata': metadata,
        }

    def run(self):
        """Parse input/arguments, perform requested command return output."""
        # Extract informations from the json
        source = self.data.get('source', {})
        params = self.data.get('params', {})
        version = self.data.get('version', {})

        # To use AWS with efficiency, we are pushing the AWS credentials into environment
        os.environ['AWS_ACCESS_KEY_ID'] = source.get('AWS_ACCESS_KEY_ID', '')
        os.environ['AWS_SECRET_ACCESS_KEY'] = source.get('AWS_SECRET_ACCESS_KEY', '')
        os.environ['AWS_DEFAULT_REGION'] = source.get('AWS_DEFAULT_REGION', "eu-west-1")
        self.wait_lock = source.get('wait_lock', 10)

        # Ensure we are receiving the required parameters on the configuration
        if 'pool' not in source:
            log.error("pool must exist in the source configuration")
            exit(1)
        else:
            self.pool = source.get('pool')

        # Configure the connection to Dynamodb
        self.engine.connect_to_region(os.environ.get('AWS_DEFAULT_REGION', 'eu-west-1'))
        # Register our model with the engine so it can create the Dynamo table
        self.engine.register(Approval)
        # Create the dynamo table for our registered model
        self.engine.create_schema()

        # Define which operation to perform
        if self.command_name == 'check':
            response = self.check_cmd(source, version)
        elif self.command_name == 'in':
            response = self.in_cmd(self.command_argument[0], source, version, params)
        else:
            response = self.out_cmd(self.command_argument[0], source, params)

        return json.dumps(response)

if __name__ == "__main__":
    print(ApprovalResource(command_name=os.path.basename(__file__),
                           json_data=sys.stdin.read(),
                           command_argument=sys.argv[1:]).run())
