#!/usr/bin/env python

from __future__ import print_function
import boto3
import botocore
import time
import sys
import argparse
import os
import urllib
import json
from botocore.vendored import requests

'''AWS Organizations Create Account and Provision Resources via CloudFormation

This module creates a new account using Organizations, then calls CloudFormation to deploy baseline resources within that account via a local tempalte file.

'''

__version__ = '1.0'
__author__ = 'Mahesh Bisl'
__email__ = 'm.bisl@unsw.edu.au'


def get_client(service):
    client = boto3.client(service)
    return client


def create_account(event, root_id):
    client = get_client('organizations')
    accountname = os.environ['accountname']
    accountemail = os.environ['accountemail']
    accountrole = 'OrganizationAccountAccessRole'
    account_id = 'None'

    try:
        print("Trying to create the account with {}".format(accountemail))
        create_account_response = client.create_account(Email=accountemail, AccountName=accountname,
                                                        RoleName=accountrole)
        # while(create_account_response['CreateAccountStatus']['State'] is 'IN_PROGRESS'):
        #     print(create_account_response['CreateAccountStatus']['State'])
        time.sleep(120)
        account_status = client.describe_create_account_status(
            CreateAccountRequestId=create_account_response['CreateAccountStatus']['Id'])
        print("Account Creation status: {}".format(
            account_status['CreateAccountStatus']['State']))
        if(account_status['CreateAccountStatus']['State'] == 'FAILED'):
            print("Account Creation Failed. Reason : {}".format(
                account_status['CreateAccountStatus']['FailureReason']))
            delete_respond_cloudformation(
                event, "FAILED", account_status['CreateAccountStatus']['FailureReason'])
            sys.exit(1)

    except botocore.exceptions.ClientError as e:
        print("In the except module. Error : {}".format(e))
        delete_respond_cloudformation(
            event, "FAILED", "Account Creation Failed. Deleting Lambda Function." + e + ".")

    time.sleep(10)
    create_account_status_response = client.describe_create_account_status(
        CreateAccountRequestId=create_account_response.get('CreateAccountStatus').get('Id'))
    account_id = create_account_status_response.get(
        'CreateAccountStatus').get('AccountId')
    while(account_id is None):
        create_account_status_response = client.describe_create_account_status(
            CreateAccountRequestId=create_account_response.get('CreateAccountStatus').get('Id'))
        account_id = create_account_status_response.get(
            'CreateAccountStatus').get('AccountId')
    # move_response = client.move_account(AccountId=account_id,SourceParentId=root_id,DestinationParentId=organization_unit_id)
    return(create_account_response, account_id)


def get_template():
    sourcebucket = os.environ['sourcebucket']
    baselinetemplate = os.environ['baselinetemplate']
    s3 = boto3.resource('s3')
    try:
        obj = s3.Object(sourcebucket, baselinetemplate)
        return obj.get()['Body'].read().decode('utf-8')
    except botocore.exceptions.ClientError as e:
        print("Error accessing the source bucket. Error : {}".format(e))
        return e


def deploy_resources(credentials, account_id):
    stackname = os.environ['stackname']
    stackregion = os.environ['stackregion']
    template = os.environ['baselinetemplate']
    datestamp = time.strftime("%d/%m/%Y")
    client = boto3.client('cloudformation',
                          aws_access_key_id=credentials['AccessKeyId'],
                          aws_secret_access_key=credentials['SecretAccessKey'],
                          aws_session_token=credentials['SessionToken'],
                          region_name=stackregion)
    print("Creating stack " + stackname + " in " + account_id)
    creating_stack = True
    try:
        while creating_stack is True:
            try:
                creating_stack = False
                variables = [
                    'BusinessUnit',
                    'Environment',
                    'PresentationSubnetACidr',
                    'PresentationSubnetBCidr',
                    'PresentationSubnetCCidr',
                    'ApplicationSubnetACidr',
                    'ApplicationSubnetBCidr',
                    'ApplicationSubnetCCidr',
                    'DataSubnetACidr',
                    'DataSubnetBCidr',
                    'DataSubnetCCidr'
                ]

                parameters = []
                for v in variables:
                    parameters.append(
                        {
                            'ParameterKey': v,
                            'ParameterValue': os.environ(v)
                        }
                    )

                create_stack_response = client.create_stack(
                    StackName=stackname,
                    TemplateBody=template,
                    Parameters=parameters,
                    NotificationARNs=[],
                    Capabilities=[
                        'CAPABILITY_NAMED_IAM',
                    ],
                    OnFailure='ROLLBACK',
                    Tags=[
                        {
                            'Key': 'ManagedResource',
                            'Value': 'True'
                        },
                        {
                            'Key': 'DeployDate',
                            'Value': datestamp
                        }
                    ]
                )
            except botocore.exceptions.ClientError as e:
                creating_stack = True
                print(e)
                print("Retrying...")
                time.sleep(10)

        stack_building = True
        print("Stack creation in process...")
        print(create_stack_response)
        while stack_building is True:
            event_list = client.describe_stack_events(
                StackName=stackname).get("StackEvents")
            stack_event = event_list[0]

            if (stack_event.get('ResourceType') == 'AWS::CloudFormation::Stack' and
                    stack_event.get('ResourceStatus') == 'CREATE_COMPLETE'):
                stack_building = False
                print("Stack construction complete.")
            elif (stack_event.get('ResourceType') == 'AWS::CloudFormation::Stack' and
                  stack_event.get('ResourceStatus') == 'ROLLBACK_COMPLETE'):
                stack_building = False
                print("Stack construction failed.")
                # sys.exit(1)
            else:
                print(stack_event)
                print("Stack building . . .")
                time.sleep(10)
        stack = client.describe_stacks(StackName=stackname)
        return stack
    except botocore.exceptions.ClientError as e:
        print("Error deploying stack.There might be an error either accessing the Source bucket or accessing the baseline template from the source bucket.Error : {}".format(e))
        return e


def assume_role(account_id):
    account_role = 'OrganizationAccountAccessRole'
    sts_client = boto3.client('sts')
    role_arn = 'arn:aws:iam::' + account_id + ':role/' + account_role
    assuming_role = True
    while assuming_role is True:
        try:
            assuming_role = False
            assumedRoleObject = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName="NewAccountRole"
            )
        except botocore.exceptions.ClientError as e:
            assuming_role = True
            print(e)
            print("Retrying...")
            time.sleep(60)

    # From the response that contains the assumed role, get the temporary
    # credentials that can be used to make subsequent API calls
    return assumedRoleObject['Credentials']


def get_ou_name_id(root_id):

    organization_unit_name = os.environ['organizationunitname']

    ou_client = get_client('organizations')
    list_of_OU_ids = []
    list_of_OU_names = []
    ou_name_to_id = {}

    list_of_OUs_response = ou_client.list_organizational_units_for_parent(
        ParentId=root_id)

    for i in list_of_OUs_response['OrganizationalUnits']:
        list_of_OU_ids.append(i['Id'])
        list_of_OU_names.append(i['Name'])

    if(organization_unit_name not in list_of_OU_names):
        print("The provided Organization Unit Name doesnt exist. Creating an OU named: {}".format(
            organization_unit_name))
        try:
            ou_creation_response = ou_client.create_organizational_unit(
                ParentId=root_id, Name=organization_unit_name)
            for k, v in ou_creation_response.items():
                for k1, v1 in v.items():
                    if(k1 == 'Name'):
                        organization_unit_name = v1
                    if(k1 == 'Id'):
                        organization_unit_id = v1
        except botocore.exceptions.ClientError as e:
            print("Error in creating the OU: {}".format(e))
            respond_cloudformation({}, "FAILED", {
                                   "Message": "Could not list out AWS Organization OUs. Account creation Aborted."})

    else:
        for i in range(len(list_of_OU_names)):
            ou_name_to_id[list_of_OU_names[i]] = list_of_OU_ids[i]
        organization_unit_id = ou_name_to_id[organization_unit_name]

    return(organization_unit_name, organization_unit_id)


def respond_cloudformation(event, status, data=None):
    responseBody = {
        'Status': status,
        'Reason': 'See the details in CloudWatch Log Stream',
        'PhysicalResourceId': event['ServiceToken'],
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data
    }

    print('Response = ' + json.dumps(responseBody))
    print(event)
    requests.put(event['ResponseURL'], data=json.dumps(responseBody))


def delete_respond_cloudformation(event, status, message):
    responseBody = {
        'Status': status,
        'Reason': message,
        'PhysicalResourceId': event['ServiceToken'],
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId']
    }

    requests.put(event['ResponseURL'], data=json.dumps(responseBody))
    lambda_client = get_client('lambda')
    function_name = os.environ['AWS_LAMBDA_FUNCTION_NAME']
    print('Deleting resources and rolling back the stack.')
    lambda_client.delete_function(FunctionName=function_name)
    # requests.put(event['ResponseURL'], data=json.dumps(responseBody))


def main(event, context):
    print(event)
    client = get_client('organizations')
    accountname = os.environ['accountname']
    accountemail = os.environ['accountemail']
    organization_unit_name = os.environ['organizationunitname']

    if (event['RequestType'] == 'Create'):
        top_level_account = event['ServiceToken'].split(':')[4]
        org_client = get_client('organizations')

        try:
            list_roots_response = org_client.list_roots()
            # print(list_roots_response)
            root_id = list_roots_response['Roots'][0]['Id']
        except:
            root_id = "Error"

        if root_id is not "Error":
            print("Creating new account: " +
                  accountname + " (" + accountemail + ")")

            (create_account_response, account_id) = create_account(event, root_id)
            # print(create_account_response)
            print("Created acount:{}\n".format(account_id))

            credentials = assume_role(account_id)

            ec2_client = get_client('ec2')
            template = get_template()
            stack = deploy_resources(credentials, account_id)
            print(stack)

            print("Resources deployment for account " +
                  account_id + " (" + accountemail + ") complete !!")

            root_id = client.list_roots().get('Roots')[0].get('Id')
            # print(root_id)
            # print('Outside try block - {}'.format(organization_unit_name))

            if(organization_unit_name != 'None'):
                try:
                    (organization_unit_name,
                     organization_unit_id) = get_ou_name_id(root_id)
                    move_response = org_client.move_account(
                        AccountId=account_id, SourceParentId=root_id, DestinationParentId=organization_unit_id)

                except Exception as ex:
                    template = "An exception of type {0} occurred. Arguments:\n{1!r} "
                    message = template.format(type(ex).__name__, ex.args)
                    print(message)
        else:
            print("Cannot access the AWS Organization ROOT. Contact the master account Administrator for more details.")
            # sys.exit(1)
            delete_respond_cloudformation(
                event, "FAILED", "Cannot access the AWS Organization ROOT. Contact the master account Administrator for more details.Deleting Lambda Function.")

    if(event['RequestType'] == 'Update'):
        print("Template in Update Status")
        respond_cloudformation(
            event, "SUCCESS", {"Message": "Resource update successful!"})

    elif(event['RequestType'] == 'Delete'):
        try:
            delete_respond_cloudformation(
                event, "SUCCESS", "Delete Request Initiated. Deleting Lambda Function.")
        except:
            print("Couldnt initiate delete response.")
