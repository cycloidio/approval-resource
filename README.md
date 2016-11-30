# approval-resource

The approval resource copies the pool-resource but is backed by dynamodb on AWS.
There is no "pool" of random lock to acquire like the pool-resource does, but the resource creates on the fly a lock with a lockname until this one is released.

This resource adds a feature to let an external user approve or reject a step. For example, if you want a user validation between the job `test1` and `test2`, then you should use this resource.

## IAM configuration

To work with dynamodb, you will need a dedicated IAM user with this policy :

```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ConcourseApproval",
            "Effect": "Allow",
            "Action": [
                "dynamodb:*"
            ],
            "Resource": "arn:aws:dynamodb:eu-west-1:<ACCOUNT-NUMBER>:table/concourse-approval*"
        },
        {
            "Sid": "ListAllTables",
            "Effect": "Allow",
            "Action": "dynamodb:ListTables",
            "Resource": "*"
        }
    ]
}
```

## Source Configuration

* `AWS_ACCESS_KEY_ID`: *Required.* The access key used to query dynamodb.

* `AWS_SECRET_ACCESS_KEY`: *Required.* The secret key used to query dynamodb.

* `AWS_DEFAULT_REGION`: *Required.* The region where is located dynamodb.

* `pool`: *Required.* This parameter sets the pool which the lock will belong too. It's useful to avoid lockname collisions.

* `wait_lock`: *Optional.* This parameter controls the time to wait between two checks of the `approved` field.

* `debug`: *Optional.* This parameter will increase the verbosity of logs output.

## Behavior

### `check`: Check for changes to the pool.

The check command will query the dynamodb table and retrieve all locks of a pool with a timestamp greater than the last version checked.


### `in`: Fetch an acquired lock or wait until it's approved.

Outputs 2 files:

* `metadata`: Contains the content of whatever was in your lock file. This is
  useful for environment configuration settings.

* `name`: Contains the name of lock that was acquired.

If the lock is waiting for an approval, the get will wait that the field `approved` goes to `True` or `False`.
If the `approved` field is `False`, it will exit with a `1` status and then, fail the job.

#### Parameters

* `lock_name`: *Optional.* This field is optional but goes with the `need_approval` field. It specifies which lock to fetch from the dynamodb.
  
* `need_approval`: *Optional.* If set, the `get` will wait indefinitely for a change on the `approved` field.


### `out`: Claim or release a lock.

Performs one of the following actions to change the state of the pool.

#### Parameters

* `action`: *Required.* This field has two possible values : `claim` and `release`.
  The `claim` will create/acquire a lock and the `release` will release the lock

* `lock_name`: *Required.* This field contains the name of the lock to acquire/release

* `need_approval`: *Optional.* If set, the `put` will add a field to tell the `get` that the .

## Example Concourse Configuration

The following example pipeline models: acquiring, passing through, and releasing
a lock based on the example git repository structure:

```
resource_types:
- name: approval
  type: docker-image
  source:
    # use tag `latest` for stable release
    repository: cycloid/approval-resource
    tag: test

resources:
- name: approval
  type: approval
  source:
    #debug: true
    AWS_ACCESS_KEY_ID: {{ec2_access_key}}
    AWS_SECRET_ACCESS_KEY: {{ec2_secret_key}}
    AWS_DEFAULT_REGION: eu-west-1
    pool: cycloid-approval
    
jobs:
  - name: test1
    plan:
      - get: resource-src
        trigger: true
        passed: [push-test-image]
      - put: approval
        params:
          lock_name: test1
          action: claim
          need_approval: true

  - name: test2
    plan:
      - get: approval
        trigger: true
        passed: [test1]
        params:
          lock_name: test1
          need_approval: true
      - get: resource-src
        trigger: true
        passed: [test1]
      - put: approval
        params:
          lock_name: test1
          action: release

```

## CLI

To manage the lock, there's a little CLI script to interact with dynamodb.
You can of course write your own tool.

### List
```
# python3 cli.py list
approved    claimed    description    id                                    lockname    need_approval    pipeline           pool              team    timestamp
----------  ---------  -------------  ------------------------------------  ----------  ---------------  -----------------  ----------------  ------  --------------------------------
            True                      f47f5864-a1b4-4bc0-8899-784ae3b768a4  test1       True             approval-resource  cycloid-approval  main    2016-11-30 13:55:05.079579+00:00
```

### Approve
```
# python3 cli.py approve --id f47f5864-a1b4-4bc0-8899-784ae3b768a4
INFO: The lock f47f5864-a1b4-4bc0-8899-784ae3b768a4 has been approved
```

### Reject
```
# python3 cli.py reject --id f47f5864-a1b4-4bc0-8899-784ae3b768a4
INFO: The lock f47f5864-a1b4-4bc0-8899-784ae3b768a4 has been rejected
```
