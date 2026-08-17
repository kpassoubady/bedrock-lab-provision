#!/usr/bin/env python3
"""
Provision STUDENT sandboxes for the Bedrock AgentCore course.

This script is intended for the external vendor / IT team that provisions the
AWS environment before the course.  It creates one isolated sandbox per student
or team, each with its own IAM user, execution role, S3 bucket, AgentCore
Gateway, AgentCore Memory, and CloudWatch log group.

The script follows the naming conventions and policy documents from
provisioning-student.md exactly.

Resources created PER TEAM
---------------------------
  - IAM user                 LabAgent-TeamXX
  - IAM execution role       LabAgent-TeamXX-ExecutionRole
  - S3 bucket                labagent-teamXX-<account-id>-<random>
  - AgentCore Gateway        LabAgent-TeamXX-Gateway
  - AgentCore Memory         LabAgent-TeamXX-Memory
  - CloudWatch log group     /aws/bedrock/agentcore/teamXX
  - Access keys              one pair per IAM user
  - Console login profile    browser sign-in for CloudWatch trace inspection
                             (disable with --no-console-access)

Output
------
  - env/credentials.csv                 master credential sheet (all teams)
  - env/team-XX.env                     per-team environment variables
  - env/team-XX-credentials.csv         per-team AWS credentials
  - env/student-manifest.json           resource manifest for teardown

Usage
-----
  # Dry run — print what would be created (no AWS calls)
  python3 provision-students.py --teams 5 --dry-run

  # Live run for 10 teams
  python3 provision-students.py --teams 10 --prefix LabAgent --region us-east-1

  # Provision a single team (useful for late additions)
  python3 provision-students.py --teams 1 --start-team 11 --prefix LabAgent

Prerequisites
-------------
  - Python 3.10+
  - boto3 installed (pip3 install boto3)
  - AWS credentials with permissions listed in
    provisioning-instructor.md section 2:
      IAMFullAccess, AmazonS3FullAccess, CloudWatchFullAccess,
      AmazonBedrockFullAccess, plus the AgentCore inline policy
  - Bedrock model access enabled in the target region

Reference
---------
  provisioning-student.md     — full student sandbox specification
  provisioning-instructor.md  — instructor provisioning specification
"""

from __future__ import annotations

import argparse
import csv
import json
import secrets
import string
import sys
import textwrap
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy boto3 import so --dry-run works without it
# ---------------------------------------------------------------------------
boto3 = None
ClientError = None


def _require_boto3():
    global boto3, ClientError
    try:
        import boto3 as _boto3
        from botocore.exceptions import ClientError as _ClientError

        boto3 = _boto3
        ClientError = _ClientError
    except ImportError:
        print("ERROR: boto3 is required. Install it: pip3 install boto3")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration — matches provisioning-student.md exactly
# ---------------------------------------------------------------------------
DEFAULT_REGION = "us-east-1"
DEFAULT_TEAMS = 10
DEFAULT_PREFIX = "LabAgent"

# Per-team IAM user name: LabAgent-TeamXX
# Per-team execution role: LabAgent-TeamXX-ExecutionRole
EXECUTION_ROLE_SUFFIX = "ExecutionRole"
SANDBOX_POLICY_NAME = "AgentCoreSandboxPolicy"
EXECUTION_POLICY_NAME = "AgentCoreExecutionPolicy"

# Console login — students need browser sign-in to inspect CloudWatch GenAI
# Observability traces in lab Checkpoint 5.  Password symbols use a subset of
# the characters IAM permits so the generated password satisfies the account
# password policy.
CONSOLE_PASSWORD_LENGTH = 16
CONSOLE_PASSWORD_SYMBOLS = "!@#$%*-_=+"

# Output directory
OUTPUT_DIR = "env"
STUDENT_MANIFEST_FILE = "student-manifest.json"


# ---------------------------------------------------------------------------
# Naming helpers — must match provisioning-student.md section 1
# ---------------------------------------------------------------------------
def _team_tag(team_num: int) -> str:
    """Return 'TeamXX' with zero-padded two-digit team number."""
    return f"Team{team_num:02d}"


def _user_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX"""
    return f"{prefix}-{_team_tag(team_num)}"


def _execution_role_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-ExecutionRole"""
    return f"{prefix}-{_team_tag(team_num)}-{EXECUTION_ROLE_SUFFIX}"


def _bucket_name(prefix: str, team_num: int, account_id: str) -> str:
    """labagent-teamXX-<account-id>-<random>"""
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix.lower()}-{_team_tag(team_num).lower()}-{account_id}-{suffix}"


def _gateway_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-Gateway"""
    return f"{prefix}-{_team_tag(team_num)}-Gateway"


def _memory_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-Memory"""
    return f"{prefix}-{_team_tag(team_num)}-Memory"


def _log_group_name(team_num: int) -> str:
    """/aws/bedrock/agentcore/teamXX"""
    return f"/aws/bedrock/agentcore/{_team_tag(team_num).lower()}"


def _agent_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX (used in .env as AGENT_NAME)"""
    return f"{prefix}-{_team_tag(team_num)}"


def _generate_console_password() -> str:
    """
    Generate a strong console password that satisfies a typical IAM account
    password policy (upper, lower, digit, and symbol present).
    """
    alphabet = string.ascii_letters + string.digits + CONSOLE_PASSWORD_SYMBOLS
    while True:
        pw = "".join(
            secrets.choice(alphabet) for _ in range(CONSOLE_PASSWORD_LENGTH)
        )
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in CONSOLE_PASSWORD_SYMBOLS for c in pw)
        ):
            return pw


def _signin_url(account_id: str) -> str:
    """Account-specific AWS Console sign-in URL for IAM users."""
    return f"https://{account_id}.signin.aws.amazon.com/console"


# ---------------------------------------------------------------------------
# IAM policy documents — Python dicts matching provisioning-student.md
# ---------------------------------------------------------------------------
def execution_trust_policy(region: str, account_id: str) -> dict:
    """
    Trust policy allowing AgentCore Runtime to assume the execution role.

    Matches provisioning-student.md section 3. The AgentCore Runtime service
    principal is bedrock-agentcore.amazonaws.com, not bedrock.amazonaws.com —
    the latter cannot assume this role, so CreateAgentRuntime/UpdateAgentRuntime
    would fail even with the right IAM actions granted elsewhere.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                        )
                    },
                },
            }
        ],
    }


def execution_permissions_policy(
    region: str,
    account_id: str,
    gateway_arn: str,
    memory_arn: str,
    log_group: str,
) -> dict:
    """
    Inline policy for the execution role — what the agent can do.

    Matches provisioning-student.md section 3.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": f"arn:aws:bedrock:{region}::foundation-model/*",
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": gateway_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                "Resource": memory_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:{log_group}:*",
            },
        ],
    }


def sandbox_permissions_policy(
    region: str,
    account_id: str,
    execution_role_arn: str,
    bucket_arn: str,
    gateway_arn: str,
    memory_arn: str,
    log_group: str,
) -> dict:
    """
    Scoped inline policy for the student IAM user.

    Students invoke models and their own Gateway/Memory, and — via
    deploy_agent.py (Lab 05) — create and update their own Runtime.
    They cannot touch any other team's resources.

    Matches provisioning-student.md section 2.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": f"arn:aws:bedrock:{region}::foundation-model/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateAgentRuntime",
                    "bedrock-agentcore:UpdateAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime",
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/*",
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": gateway_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                ],
                "Resource": memory_arn,
            },
            {
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": execution_role_arn,
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                ],
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:GetLogRecord",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:{log_group}:*",
            },
            {
                # logs:StopQuery / DescribeQueries do not support resource-level
                # scoping; the GenAI Observability trace view calls them.
                "Effect": "Allow",
                "Action": [
                    "logs:StopQuery",
                    "logs:DescribeQueries",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:GetMetricData",
                    "cloudwatch:ListMetrics",
                    "cloudwatch:GetMetricStatistics",
                ],
                "Resource": "*",
            },
            {
                # X-Ray read actions back the CloudWatch GenAI Observability
                # trace view (Checkpoint 5).  X-Ray Get/BatchGet APIs do not
                # support resource-level permissions, so Resource must be "*".
                "Effect": "Allow",
                "Action": [
                    "xray:GetTraceSummaries",
                    "xray:BatchGetTraces",
                    "xray:GetTraceGraph",
                    "xray:GetServiceGraph",
                    "xray:GetGroups",
                    "xray:GetGroup",
                    "xray:GetInsightSummaries",
                ],
                "Resource": "*",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Provisioner class
# ---------------------------------------------------------------------------
class StudentProvisioner:
    """
    Creates per-team student sandboxes.

    On dry_run=True, prints what would happen without making AWS calls.
    """

    def __init__(
        self,
        region: str,
        prefix: str,
        dry_run: bool = False,
        console_access: bool = True,
        force_password_reset: bool = False,
    ):
        self.region = region
        self.prefix = prefix
        self.dry_run = dry_run
        self.console_access = console_access
        self.force_password_reset = force_password_reset

        if dry_run:
            self.iam = None
            self.s3 = None
            self.logs = None
            self.agentcore = None
            self.sts = None
            self.account_id = "123456789012"
        else:
            self.iam = boto3.client("iam", region_name=region)
            self.s3 = boto3.client("s3", region_name=region)
            self.logs = boto3.client("logs", region_name=region)
            self.sts = boto3.client("sts", region_name=region)
            self.account_id = self.sts.get_caller_identity()["Account"]

            try:
                self.agentcore = boto3.client(
                    "bedrock-agentcore-control", region_name=region
                )
            except Exception:
                self.agentcore = None

        self._all_teams: list[dict] = []

    # ------------------------------------------------------------------
    # S3 — one bucket per team
    # ------------------------------------------------------------------
    def _create_bucket(self, team_num: int) -> str:
        bucket = _bucket_name(self.prefix, team_num, self.account_id)

        if self.dry_run:
            print(f"    [DRY RUN] Would create S3 bucket: {bucket}")
            return bucket

        if self.region == "us-east-1":
            self.s3.create_bucket(Bucket=bucket)
        else:
            self.s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": self.region},
            )

        self.s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            },
        )
        self.s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        print(f"    Created S3 bucket: {bucket}")
        return bucket

    def _upload_placeholder_agent(self, bucket: str, agent_name: str) -> None:
        key = f"{agent_name}/v1/agent.py"
        content = textwrap.dedent("""\
        def handler(event, context):
            return {"message": "Agent is running."}
        """)

        if self.dry_run:
            print(f"    [DRY RUN] Would upload s3://{bucket}/{key}")
            return

        self.s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
        print(f"    Uploaded placeholder: s3://{bucket}/{key}")

    # ------------------------------------------------------------------
    # CloudWatch Logs — one log group per team
    # ------------------------------------------------------------------
    def _create_log_group(self, team_num: int) -> str:
        log_group = _log_group_name(team_num)

        if self.dry_run:
            print(f"    [DRY RUN] Would create log group: {log_group}")
            return log_group

        try:
            self.logs.create_log_group(logGroupName=log_group)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
                print(f"    Log group already exists: {log_group}")
            else:
                raise

        self.logs.put_retention_policy(
            logGroupName=log_group, retentionInDays=7
        )
        print(f"    Created log group: {log_group} (retention: 7 days)")
        return log_group

    # ------------------------------------------------------------------
    # AgentCore Gateway — one per team
    # ------------------------------------------------------------------
    def _find_gateway(self, gateway_name: str) -> dict | None:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_gateways(**request)
            for item in response.get("items", []):
                if item.get("name") == gateway_name:
                    return self.agentcore.get_gateway(
                        gatewayIdentifier=item["gatewayId"]
                    )
            next_token = response.get("nextToken")
            if not next_token:
                return None

    def _create_gateway(self, team_num: int, role_arn: str) -> tuple[str, str]:
        gateway_name = _gateway_name(self.prefix, team_num)
        gateway_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":gateway/{gateway_name}"
        )

        if self.dry_run:
            print(f"    [DRY RUN] Would create Gateway: {gateway_name}")
            return gateway_name, gateway_arn

        if self.agentcore is None:
            print(
                f"    WARNING: SDK client unavailable — create Gateway manually:\n"
                f"      Name: {gateway_name}\n"
                f"      Protocol: MCP or HTTP (match lab agent)\n"
                f"      Target: read-only endpoint returning structured JSON\n"
                f"      Auth: IAM SigV4"
            )
            return gateway_name, gateway_arn

        gateway = self._find_gateway(gateway_name)
        if gateway:
            print(f"    Gateway already exists: {gateway_name} (reusing)")
        else:
            try:
                gateway = self.agentcore.create_gateway(
                    name=gateway_name,
                    description=f"Student Gateway for {_team_tag(team_num)} - Bedrock AgentCore course",
                    roleArn=role_arn,
                    protocolType="MCP",
                    authorizerType="AWS_IAM",
                )
                print(f"    Created Gateway: {gateway_name}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConflictException":
                    raise
                gateway = self._find_gateway(gateway_name)
                if not gateway:
                    raise
                print(f"    Gateway already exists: {gateway_name} (reusing)")

        return gateway_name, gateway["gatewayArn"]

    # ------------------------------------------------------------------
    # AgentCore Memory — one per team
    # ------------------------------------------------------------------
    def _find_memory(self, memory_name: str) -> dict | None:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_memories(**request)
            for item in response.get("memories", []):
                memory = self.agentcore.get_memory(memoryId=item["id"])["memory"]
                if memory.get("name") == memory_name:
                    return memory
            next_token = response.get("nextToken")
            if not next_token:
                return None

    def _create_memory(self, team_num: int) -> tuple[str, str]:
        memory_name = _memory_name(self.prefix, team_num)
        memory_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":memory/{memory_name}"
        )

        if self.dry_run:
            print(f"    [DRY RUN] Would create Memory: {memory_name}")
            return memory_name, memory_arn

        if self.agentcore is None:
            print(
                f"    WARNING: SDK client unavailable — create Memory manually:\n"
                f"      Name: {memory_name}\n"
                f"      Type: Short-term (session-scoped)"
            )
            return memory_name, memory_arn

        memory = self._find_memory(memory_name)
        if memory:
            print(f"    Memory already exists: {memory_name} (reusing)")
        else:
            try:
                resp = self.agentcore.create_memory(
                    name=memory_name,
                    description=f"Student Memory for {_team_tag(team_num)} - Bedrock AgentCore course",
                    eventExpiryDuration=30,
                )
                memory = resp["memory"]
                print(f"    Created Memory: {memory_name}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConflictException":
                    raise
                memory = self._find_memory(memory_name)
                if not memory:
                    raise
                print(f"    Memory already exists: {memory_name} (reusing)")

        return memory_name, memory["arn"]

    # ------------------------------------------------------------------
    # IAM — execution role per team
    # ------------------------------------------------------------------
    def _create_execution_role(self, team_num: int) -> str:
        role_name = _execution_role_name(self.prefix, team_num)
        role_arn = f"arn:aws:iam::{self.account_id}:role/{role_name}"

        if self.dry_run:
            print(f"    [DRY RUN] Would create execution role: {role_arn}")
            return role_arn

        try:
            resp = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(
                    execution_trust_policy(self.region, self.account_id)
                ),
                Description=f"Execution role for {_team_tag(team_num)} - Bedrock AgentCore course",
            )
            role_arn = resp["Role"]["Arn"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                print(f"    Execution role already exists: {role_arn} (reusing)")
            else:
                raise
        else:
            print(f"    Created execution role: {role_arn}")

        # Reapply on every run (not just first creation) so a role created
        # under an older, incorrect trust policy gets corrected in place.
        self.iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(
                execution_trust_policy(self.region, self.account_id)
            ),
        )
        return role_arn

    def _attach_execution_policy(
        self,
        team_num: int,
        gateway_arn: str,
        memory_arn: str,
        log_group: str,
    ) -> None:
        role_name = _execution_role_name(self.prefix, team_num)

        if self.dry_run:
            print(
                f"    [DRY RUN] Would attach execution policy: {EXECUTION_POLICY_NAME}"
            )
            return

        policy_doc = execution_permissions_policy(
            region=self.region,
            account_id=self.account_id,
            gateway_arn=gateway_arn,
            memory_arn=memory_arn,
            log_group=log_group,
        )
        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName=EXECUTION_POLICY_NAME,
            PolicyDocument=json.dumps(policy_doc),
        )
        print(f"    Attached execution policy: {EXECUTION_POLICY_NAME}")

    # ------------------------------------------------------------------
    # IAM — sandbox user per team
    # ------------------------------------------------------------------
    def _create_sandbox_user(
        self,
        team_num: int,
        execution_role_arn: str,
        bucket_arn: str,
        gateway_arn: str,
        memory_arn: str,
        log_group: str,
    ) -> dict:
        """
        Create one IAM user with scoped permissions.

        Returns dict with keys: team_num, user_name, access_key, secret_key.
        """
        user_name = _user_name(self.prefix, team_num)

        if self.dry_run:
            print(f"    [DRY RUN] Would create user: {user_name}")
            if self.console_access:
                print(
                    f"    [DRY RUN] Would create console login for: {user_name}"
                )
            return {
                "team_num": team_num,
                "user_name": user_name,
                "access_key": "AKIA_DRY_RUN",
                "secret_key": "dry-run-secret",
                "console_password": "dry-run-console-password" if self.console_access else "",
                "signin_url": _signin_url(self.account_id) if self.console_access else "",
            }

        # Create user (idempotent)
        try:
            self.iam.create_user(UserName=user_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                print(f"    User already exists: {user_name} (reusing)")
            else:
                raise

        # Attach scoped inline policy
        policy_doc = sandbox_permissions_policy(
            region=self.region,
            account_id=self.account_id,
            execution_role_arn=execution_role_arn,
            bucket_arn=bucket_arn,
            gateway_arn=gateway_arn,
            memory_arn=memory_arn,
            log_group=log_group,
        )
        self.iam.put_user_policy(
            UserName=user_name,
            PolicyName=SANDBOX_POLICY_NAME,
            PolicyDocument=json.dumps(policy_doc),
        )

        # Generate access keys
        keys = self.iam.create_access_key(UserName=user_name)["AccessKey"]

        # Console login profile (browser sign-in for CloudWatch trace view)
        console_password, signin_url = self._create_console_login(user_name)

        print(f"    Created sandbox user: {user_name}")
        return {
            "team_num": team_num,
            "user_name": user_name,
            "access_key": keys["AccessKeyId"],
            "secret_key": keys["SecretAccessKey"],
            "console_password": console_password,
            "signin_url": signin_url,
        }

    def _create_console_login(self, user_name: str) -> tuple[str, str]:
        """
        Create (or reset) a console login profile for the IAM user.

        Students need browser sign-in to inspect CloudWatch GenAI Observability
        traces in lab Checkpoint 5.  Returns (password, signin_url); both are
        empty strings when console access is disabled.

        On rerun, the existing password cannot be read back, so the profile is
        reset to a freshly generated password to keep the output sheets valid.
        """
        if not self.console_access:
            return "", ""

        signin_url = _signin_url(self.account_id)
        password = _generate_console_password()

        try:
            self.iam.create_login_profile(
                UserName=user_name,
                Password=password,
                PasswordResetRequired=self.force_password_reset,
            )
            print(f"    Created console login: {user_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                self.iam.update_login_profile(
                    UserName=user_name,
                    Password=password,
                    PasswordResetRequired=self.force_password_reset,
                )
                print(f"    Reset console login password: {user_name}")
            else:
                raise

        return password, signin_url

    # ------------------------------------------------------------------
    # Provision one team (all resources)
    # ------------------------------------------------------------------
    def provision_team(self, team_num: int) -> dict:
        """
        Provision all resources for one team.

        Returns a dict with all team details for output generation.
        """
        tag = _team_tag(team_num)
        print(f"\n--- {tag} ---")

        # 1. S3 bucket
        print(f"  [1/7] S3 bucket...")
        bucket = self._create_bucket(team_num)
        bucket_arn = f"arn:aws:s3:::{bucket}"

        # 2. CloudWatch log group
        print(f"  [2/7] CloudWatch log group...")
        log_group = self._create_log_group(team_num)

        # 3. IAM execution role (Gateway creation needs its ARN)
        print(f"  [3/7] IAM execution role...")
        execution_role_arn = self._create_execution_role(team_num)

        if not self.dry_run:
            time.sleep(2)  # brief IAM propagation wait

        # 4. AgentCore Gateway
        print(f"  [4/7] AgentCore Gateway...")
        gateway_name, gateway_arn = self._create_gateway(team_num, execution_role_arn)

        # 5. AgentCore Memory
        print(f"  [5/7] AgentCore Memory...")
        memory_name, memory_arn = self._create_memory(team_num)

        self._attach_execution_policy(
            team_num=team_num,
            gateway_arn=gateway_arn,
            memory_arn=memory_arn,
            log_group=log_group,
        )

        # 6. IAM sandbox user
        print(f"  [6/7] IAM sandbox user...")
        team_info = self._create_sandbox_user(
            team_num=team_num,
            execution_role_arn=execution_role_arn,
            bucket_arn=bucket_arn,
            gateway_arn=gateway_arn,
            memory_arn=memory_arn,
            log_group=log_group,
        )

        # 7. Placeholder agent artifact
        print(f"  [7/7] Placeholder agent artifact...")
        agent_name = _agent_name(self.prefix, team_num)
        self._upload_placeholder_agent(bucket, agent_name)

        # Enrich team_info with resource details
        team_info.update({
            "execution_role_arn": execution_role_arn,
            "execution_role_name": _execution_role_name(self.prefix, team_num),
            "bucket": bucket,
            "bucket_arn": bucket_arn,
            "gateway_name": gateway_name,
            "gateway_arn": gateway_arn,
            "memory_name": memory_name,
            "memory_arn": memory_arn,
            "log_group": log_group,
            "agent_name": agent_name,
        })

        self._all_teams.append(team_info)
        return team_info

    # ------------------------------------------------------------------
    # Output — .env files, CSVs, manifest
    # ------------------------------------------------------------------
    def write_outputs(self) -> None:
        """Write per-team .env files, credential CSVs, and teardown manifest."""
        out_dir = Path(OUTPUT_DIR)
        out_dir.mkdir(exist_ok=True)

        # --- Per-team .env file ---
        for t in self._all_teams:
            env_path = out_dir / f"team-{t['team_num']:02d}.env"
            content = textwrap.dedent(f"""\
            # {_team_tag(t['team_num'])} — Bedrock AgentCore Lab
            # Generated by provision-students.py
            export AWS_ACCESS_KEY_ID={t['access_key']}
            export AWS_SECRET_ACCESS_KEY={t['secret_key']}
            export AWS_DEFAULT_REGION={self.region}
            export EXECUTION_ROLE_ARN={t['execution_role_arn']}
            export S3_BUCKET={t['bucket']}
            export GATEWAY_NAME={t['gateway_name']}
            export MEMORY_NAME={t['memory_name']}
            export AGENT_NAME={t['agent_name']}
            """)
            if t.get("console_password"):
                content += textwrap.dedent(f"""\
                #
                # AWS Console sign-in — needed for CloudWatch trace inspection
                # (lab Checkpoint 5). These are browser credentials, not CLI env.
                #   URL:      {t['signin_url']}
                #   Username: {t['user_name']}
                #   Password: {t['console_password']}
                """)
            env_path.write_text(content)

        # --- Per-team credentials CSV ---
        for t in self._all_teams:
            csv_path = out_dir / f"team-{t['team_num']:02d}-credentials.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Access key ID",
                    "Secret access key",
                    "Console sign-in URL",
                    "Console username",
                    "Console password",
                ])
                writer.writerow([
                    t["access_key"],
                    t["secret_key"],
                    t.get("signin_url", ""),
                    t["user_name"],
                    t.get("console_password", ""),
                ])

        # --- Master CSV ---
        master_path = out_dir / "credentials.csv"
        with open(master_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Team",
                "User Name",
                "Access Key ID",
                "Secret Access Key",
                "Console Sign-in URL",
                "Console Password",
                "Execution Role ARN",
                "S3 Bucket",
                "Gateway Name",
                "Memory Name",
                "Agent Name",
            ])
            for t in self._all_teams:
                writer.writerow([
                    t["team_num"],
                    t["user_name"],
                    t["access_key"],
                    t["secret_key"],
                    t.get("signin_url", ""),
                    t.get("console_password", ""),
                    t["execution_role_arn"],
                    t["bucket"],
                    t["gateway_name"],
                    t["memory_name"],
                    t["agent_name"],
                ])

        # --- Teardown manifest ---
        manifest = {
            "region": self.region,
            "prefix": self.prefix,
            "teams": [
                {
                    "team_num": t["team_num"],
                    "user_name": t["user_name"],
                    "execution_role_name": t["execution_role_name"],
                    "bucket": t["bucket"],
                    "gateway_name": t["gateway_name"],
                    "memory_name": t["memory_name"],
                    "log_group": t["log_group"],
                }
                for t in self._all_teams
            ],
        }
        manifest_path = out_dir / STUDENT_MANIFEST_FILE
        manifest_path.write_text(json.dumps(manifest, indent=2))

        print(f"\nOutput written to {out_dir.resolve()}/")
        print(f"  credentials.csv              — all teams (keep secure)")
        print(f"  team-XX.env                   — per-team environment vars")
        print(f"  team-XX-credentials.csv       — per-team AWS credentials")
        print(f"  {STUDENT_MANIFEST_FILE}  — resource manifest for teardown")


# ---------------------------------------------------------------------------
# Distribution & verification instructions
# ---------------------------------------------------------------------------
DISTRIBUTION_INSTRUCTIONS = """
======================================================================
  DISTRIBUTION — Student Credentials
======================================================================

Each team receives one .env file from the env/ directory:

  env/team-01.env
  env/team-02.env
  ...

Contents of each .env file:
  AWS_ACCESS_KEY_ID       Sandbox IAM user access key
  AWS_SECRET_ACCESS_KEY   Sandbox IAM user secret key
  AWS_DEFAULT_REGION      Course region ({region})
  EXECUTION_ROLE_ARN      Role AgentCore Runtime assumes
  S3_BUCKET               Bucket for agent code artifacts
  GATEWAY_NAME            Pre-created AgentCore Gateway
  MEMORY_NAME             Pre-created AgentCore Memory
  AGENT_NAME              Agent name for Runtime deployment

  Console sign-in details (URL, username, password) are appended as comments
  at the end of each .env file, and also in team-XX-credentials.csv. Students
  use them to inspect CloudWatch GenAI Observability traces in lab
  Checkpoint 5 — a browser step the CLI access keys cannot perform. If you
  provisioned with --no-console-access, these are omitted.

SECURITY:
  - Do NOT email credentials in plain text.
  - Use a one-time secret service or the company's internal credential vault.
  - Distribute one .env file per team before class.

======================================================================
  VERIFICATION — Per Team
======================================================================

After provisioning, verify each sandbox.  These checks match
provisioning-student.md section 11.

For each team, using their credentials:

1.  Confirm the correct IAM user:
      AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \\
        aws sts get-caller-identity --region {region}

2.  Invoke the Bedrock model:
      aws bedrock invoke-model --model-id <model-id> \\
        --body '{{"prompt":"test"}}' --region {region} output.json

3.  Call the Gateway target and confirm the expected synthetic response:
      aws bedrock-agentcore invoke-agent-gateway \\
        --gateway-name LabAgent-{prefix}-TeamXX-Gateway --region {region}

4.  Upload a test artifact to the S3 bucket and confirm access.

5.  Deploy a test agent to Runtime using the course deployment script
    (from the bedrock-companion repo) and confirm the execution role works.

6.  Query the CloudWatch log group and confirm logs and traces appear:
      aws logs get-log-events \\
        --log-group-name /aws/bedrock/agentcore/teamXX --region {region}

======================================================================
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Provision student sandboxes for Bedrock AgentCore course",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Reference documents:
          provisioning-student.md     — full student sandbox specification
          provisioning-instructor.md  — instructor provisioning specification
        """),
    )
    parser.add_argument(
        "--teams",
        type=int,
        default=DEFAULT_TEAMS,
        help=f"Number of student teams to provision (default: {DEFAULT_TEAMS})",
    )
    parser.add_argument(
        "--start-team",
        type=int,
        default=1,
        help="Starting team number (default: 1). Use for late additions.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Resource name prefix (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--no-console-access",
        action="store_true",
        help=(
            "Skip creating IAM console login profiles. Students then cannot "
            "sign in to inspect CloudWatch traces (lab Checkpoint 5)."
        ),
    )
    parser.add_argument(
        "--force-password-reset",
        action="store_true",
        help=(
            "Require students to change the console password on first sign-in "
            "(default: off, for a smoother short classroom session)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making AWS calls",
    )
    args = parser.parse_args()

    if not args.dry_run:
        _require_boto3()

    p = StudentProvisioner(
        region=args.region,
        prefix=args.prefix,
        dry_run=args.dry_run,
        console_access=not args.no_console_access,
        force_password_reset=args.force_password_reset,
    )

    print("=" * 60)
    print("  Bedrock AgentCore — Student Sandbox Provisioning")
    print("=" * 60)
    print(f"  Teams:     {args.teams}  (starting at team {args.start_team})")
    print(f"  Prefix:    {args.prefix}")
    print(f"  Region:    {args.region}")
    print(f"  Account:   {p.account_id}")
    print(f"  Console:   {'enabled' if p.console_access else 'disabled'}")
    print(f"  Dry run:   {args.dry_run}")
    print()

    end_team = args.start_team + args.teams
    for team_num in range(args.start_team, end_team):
        p.provision_team(team_num)

    p.write_outputs()

    print("\n" + "=" * 60)
    print("  Student sandbox provisioning complete.")
    print("=" * 60)
    print(
        DISTRIBUTION_INSTRUCTIONS.format(
            region=args.region,
            prefix=args.prefix,
        )
    )


if __name__ == "__main__":
    main()
