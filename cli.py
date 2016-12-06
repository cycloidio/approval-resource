#!/usr/bin/env python

from datetime import datetime
from flywheel import Model, Field, Engine
from tabulate import tabulate
import sys, argparse, logging


class Approval(Model):
    __metadata__ = {
        '_name': 'concourse-approval',
        '_ordering': 'ts-index',
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


class CLI():
    def __init__(self, args):
        self.engine = Engine()
        self.engine.connect_to_region('eu-west-1')

        # Register our model with the engine so it can create the Dynamo table
        self.engine.register(Approval)

        # Create the dynamo table for our registered model
        self.engine.create_schema()
        self.args = args
        # Setup logging
        if args.verbose:
            loglevel = logging.DEBUG
        else:
            loglevel = logging.INFO
        logging.basicConfig(format="%(levelname)s: %(message)s", level=loglevel)

    def main(self):
        if self.args.action == 'list':
            self.list()
        elif self.args.action == 'approve':
            self.approve()
        elif self.args.action == 'reject':
            self.reject()
        else:
            logging.error('Please use a correct argument')
            exit(1)

    def approve(self):
        if 'id' not in self.args:
            logging.error('Please give an id')
            exit(1)
        approval_lock = self.engine.scan(Approval).filter(id=self.args.id).first()
        approval_lock.approved = True
        approval_lock.timestamp = datetime.utcnow()
        self.engine.save(approval_lock, overwrite=True)
        logging.info('The lock %s has been approved' % self.args.id)

    def reject(self):
        if 'id' not in self.args:
            logging.error('Please give an id')
            exit(1)
        approval_lock = self.engine.scan(Approval).filter(id=self.args.id).first()
        approval_lock.approved = False
        approval_lock.timestamp = datetime.utcnow()
        self.engine.save(approval_lock, overwrite=True)
        logging.info('The lock %s has been rejected' % self.args.id)

    def list(self):
        approval_locks = self.engine.scan(Approval).filter(claimed=True).all()
        table = []

        if approval_locks:
            headers = sorted(approval_locks[0].keys_())
        else:
            headers = None
        if approval_locks:
            for item in approval_locks:
                row = []
                for key in sorted(item.keys_()):
                    row.append(getattr(item, key))
                table.append(row)

            print(tabulate(table, headers))
        else:
            print('There is no waiting approval')

# Standard boilerplate to call the main() function to begin
# the program.
if __name__ == '__main__':
  parser = argparse.ArgumentParser(description="Approval CLI")
  parser.add_argument('action', type=str)
  parser.add_argument("--id")

  parser.add_argument(
                      "-v",
                      "--verbose",
                      help="increase output verbosity",
                      action="store_true")
  args = parser.parse_args()

  cli = CLI(args)
  cli.main()
