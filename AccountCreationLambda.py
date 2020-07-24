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

'''
AWS Organizations Create Account
This module creates a new account using Organizations
'''

__version__ = '1.0'
__author__ = 'Mahesh Bisl'
__email__ = 'bisl.mahesh@gmail.com'


def create_account(event):
    client = boto3.client('organizations')
    AccountName = os.environ['AccountName']
    AccountEmail = os.environ['AccountEmail']
    accountrole = 'AWSCloudFormationStackSetExecutionRole'

    print("Creating new account: " + AccountName + " (" + AccountEmail + ")")
    try:
        account_status = client.create_account(
            Email=AccountEmail,
            AccountName=AccountName,
            RoleName=accountrole
        )

        while(account_status['CreateAccountStatus']['State'] is 'IN_PROGRESS'):
            print("Account Creation status: {}".format(
                account_status['CreateAccountStatus']['State']))
            time.sleep(10)
            account_status = client.describe_create_account_status(
                CreateAccountRequestId=account_status['CreateAccountStatus']['Id'])

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
        sys.exit(1)

    return(accountrole, account_status['AccountId'])


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
    lambda_client = boto3.client('lambda')
    function_name = os.environ['AWS_LAMBDA_FUNCTION_NAME']
    print('Deleting resources and rolling back the stack.')
    lambda_client.delete_function(FunctionName=function_name)
    # requests.put(event['ResponseURL'], data=json.dumps(responseBody))


def main(event, context):
    print(event)

    if (event['RequestType'] == 'Create'):
        (accountrole, account_id) = create_account(event)
        print("Created acount:{}\n".format(account_id))
        respond_cloudformation(
            event,
            "SUCCESS",
            {
                "Message": "Resource update successful!",
                "AccountID": account_id,
                "AccountRole": accountrole
            }
        )

    elif(event['RequestType'] == 'Update'):
        print("No use of updating this")
        respond_cloudformation(
            event, "SUCCESS", {"Message": "Resource update successful!"})

    elif(event['RequestType'] == 'Delete'):
        try:
            delete_respond_cloudformation(
                event, "SUCCESS", "Delete Request Initiated. Deleting Lambda Function.")
        except:
            print("Couldnt initiate delete response.")
