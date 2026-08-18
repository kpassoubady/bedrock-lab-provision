#!/usr/bin/env python3
"""
Provision the INSTRUCTOR demo sandbox for the Bedrock AgentCore course.

This script is intended for the external vendor / IT team that provisions the
AWS environment before the instructor takes over.  It creates the instructor's
own working demo resources — separate from student sandboxes — and serves as
the known-good reference during class.

Resources created
------------------
  - S3 bucket                bedrock-instructor-demo-<account-id>-<random>
  - IAM execution role       LabAgent-Instructor-ExecutionRole
  - AgentCore Gateway        InstructorDemoGateway
  - AgentCore Memory         InstructorDemoMemory
  - CloudWatch log group     /aws/bedrock/agentcore/instructor-demo
  - Placeholder agent        uploaded to the S3 bucket

Output
------
  - env/instructor.env       environment variables for the instructor
  - env/instructor-manifest.json   resource manifest for teardown

Usage
-----
  # Dry run — print what would be created (no AWS calls)
  python3 provision-instructor.py --dry-run

  # Live run
  python3 provision-instructor.py --region us-east-1

Prerequisites
-------------
  - Python 3.10+
  - boto3 installed (pip3 install boto3)
  - AWS credentials configured with permissions listed in
    provisioning-instructor.md section 2:
      IAMFullAccess, AmazonS3FullAccess, CloudWatchFullAccess,
      AmazonBedrockFullAccess, plus the AgentCore inline policy
  - Bedrock model access enabled in the target region

Reference
---------
  provisioning-instructor.md  — full provisioning specification
  provisioning-student.md     — student sandbox specification
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import textwrap
import time
import uuid
import zipfile
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
# Configuration — matches provisioning-instructor.md
# ---------------------------------------------------------------------------
DEFAULT_REGION = "us-east-1"

# IAM
INSTRUCTOR_EXECUTION_ROLE_NAME = "LabAgent-Instructor-ExecutionRole"
INSTRUCTOR_EXECUTION_POLICY_NAME = "InstructorExecutionPolicy"

# S3
INSTRUCTOR_BUCKET_PREFIX = "bedrock-instructor-demo"

# AgentCore
INSTRUCTOR_GATEWAY_NAME = "InstructorDemoGateway"
INSTRUCTOR_MEMORY_NAME = "InstructorDemoMemory"

# CloudWatch
INSTRUCTOR_LOG_GROUP = "/aws/bedrock/agentcore/instructor-demo"

# Output directory
OUTPUT_DIR = "env"
INSTRUCTOR_ENV_FILE = "instructor.env"
INSTRUCTOR_MANIFEST_FILE = "instructor-manifest.json"


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------
def _bucket_name(account_id: str, region: str) -> str:
    """Generate a unique S3 bucket name with account ID and random suffix."""
    suffix = uuid.uuid4().hex[:8]
    return f"{INSTRUCTOR_BUCKET_PREFIX}-{account_id}-{suffix}"


def _execution_role_arn(account_id: str) -> str:
    return f"arn:aws:iam::{account_id}:role/{INSTRUCTOR_EXECUTION_ROLE_NAME}"


# ---------------------------------------------------------------------------
# IAM policy documents (Python dicts — exact JSON from provisioning spec)
# ---------------------------------------------------------------------------
def execution_trust_policy(region: str, account_id: str) -> dict:
    """
    Trust policy that allows AgentCore Runtime to assume this role.

    Matches provisioning-instructor.md section 3.
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
    bucket_arn: str,
    region: str,
    account_id: str,
    gateway_arn: str,
    memory_arn: str,
) -> dict:
    """
    Inline policy for the instructor execution role.

    The instructor's demo agent needs to:
      - Invoke the Bedrock model
      - Call the pre-created Gateway
      - Read/write Memory
      - Write logs to CloudWatch
      - Read the agent artifact from S3
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                # Broad on purpose: this is a short-lived demo sandbox with
                # minimal call volume, and per-model/per-action scoping here
                # has repeatedly broken (wrong action name, wrong ARN, missing
                # bearer-token action) and cost days of debugging for no real
                # cost-control benefit. Trade narrow scoping for reliability.
                "Effect": "Allow",
                "Action": ["bedrock:*", "bedrock-agentcore:*"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:{INSTRUCTOR_LOG_GROUP}:*",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Provisioner class
# ---------------------------------------------------------------------------
class InstructorProvisioner:
    """
    Creates all instructor demo sandbox resources.

    On dry_run=True, prints what would happen without making AWS calls.
    """

    def __init__(self, region: str, dry_run: bool = False):
        self.region = region
        self.dry_run = dry_run

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

            self.agentcore = boto3.client(
                "bedrock-agentcore-control", region_name=region
            )

        self._resources: dict = {}

    # ------------------------------------------------------------------
    # S3
    # ------------------------------------------------------------------
    def create_bucket(self) -> str:
        """Create the instructor demo S3 bucket with encryption and block-public-access."""
        bucket = _bucket_name(self.account_id, self.region)

        if self.dry_run:
            print(f"  [DRY RUN] Would create or reuse S3 bucket: {bucket}")
            self._resources["bucket"] = bucket
            return bucket

        manifest_path = Path(OUTPUT_DIR) / INSTRUCTOR_MANIFEST_FILE
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            existing_bucket = manifest.get("bucket", "")
            if (
                manifest.get("region") == self.region
                and f"-{self.account_id}-" in existing_bucket
            ):
                try:
                    self.s3.head_bucket(Bucket=existing_bucket)
                    self._resources["bucket"] = existing_bucket
                    print(f"  S3 bucket already exists: {existing_bucket} (reusing)")
                    return existing_bucket
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                        raise

        if self.region == "us-east-1":
            self.s3.create_bucket(Bucket=bucket)
        else:
            self.s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": self.region},
            )

        # SSE-S3 encryption
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

        # Block all public access
        self.s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

        self._resources["bucket"] = bucket
        print(f"  Created S3 bucket: {bucket}")
        return bucket

    def upload_placeholder_agent(self, bucket: str) -> str:
        """
        Zip a minimal agent.py and upload it as the direct-deployment
        artifact so deploy_agent.py (bedrock-companion) works immediately.

        The instructor replaces this with the real demo agent before class.
        """
        key = "instructor-demo/v1/deployment_package.zip"
        agent_source = textwrap.dedent("""\
        def handler(event, context):
            return {"message": "Instructor demo agent is running."}
        """)

        if self.dry_run:
            print(f"  [DRY RUN] Would upload s3://{bucket}/{key}")
            return key

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("agent.py", agent_source)

        self.s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())
        print(f"  Uploaded placeholder agent: s3://{bucket}/{key}")
        return key

    # ------------------------------------------------------------------
    # IAM — execution role
    # ------------------------------------------------------------------
    def create_execution_role(self) -> str:
        """Create the IAM execution role with trust policy for AgentCore."""
        role_arn = _execution_role_arn(self.account_id)

        if self.dry_run:
            print(f"  [DRY RUN] Would create execution role: {role_arn}")
            self._resources["execution_role_name"] = INSTRUCTOR_EXECUTION_ROLE_NAME
            self._resources["execution_role_arn"] = role_arn
            return role_arn

        try:
            resp = self.iam.create_role(
                RoleName=INSTRUCTOR_EXECUTION_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(
                    execution_trust_policy(self.region, self.account_id)
                ),
                Description="Execution role for instructor demo - Bedrock AgentCore course",
            )
            role_arn = resp["Role"]["Arn"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                print(
                    f"  Execution role already exists: {role_arn} (reusing)"
                )
            else:
                raise

        self.iam.update_assume_role_policy(
            RoleName=INSTRUCTOR_EXECUTION_ROLE_NAME,
            PolicyDocument=json.dumps(
                execution_trust_policy(self.region, self.account_id)
            ),
        )
        self._resources["execution_role_name"] = INSTRUCTOR_EXECUTION_ROLE_NAME
        self._resources["execution_role_arn"] = role_arn
        print(f"  Execution role ready: {role_arn}")
        return role_arn

    def attach_execution_policy(
        self,
        bucket_arn: str,
        gateway_arn: str,
        memory_arn: str,
    ) -> None:
        """Attach the inline permissions policy to the execution role."""
        if self.dry_run:
            print(
                "  [DRY RUN] Would attach inline policy: "
                f"{INSTRUCTOR_EXECUTION_POLICY_NAME}"
            )
            return

        policy_doc = execution_permissions_policy(
            bucket_arn=bucket_arn,
            region=self.region,
            account_id=self.account_id,
            gateway_arn=gateway_arn,
            memory_arn=memory_arn,
        )
        self.iam.put_role_policy(
            RoleName=INSTRUCTOR_EXECUTION_ROLE_NAME,
            PolicyName=INSTRUCTOR_EXECUTION_POLICY_NAME,
            PolicyDocument=json.dumps(policy_doc),
        )
        print(f"  Attached inline policy: {INSTRUCTOR_EXECUTION_POLICY_NAME}")

    # ------------------------------------------------------------------
    # CloudWatch Logs
    # ------------------------------------------------------------------
    def create_log_group(self) -> str:
        """Create the CloudWatch log group for instructor demo traces."""
        if self.dry_run:
            print(f"  [DRY RUN] Would create log group: {INSTRUCTOR_LOG_GROUP}")
            self._resources["log_group"] = INSTRUCTOR_LOG_GROUP
            return INSTRUCTOR_LOG_GROUP

        try:
            self.logs.create_log_group(logGroupName=INSTRUCTOR_LOG_GROUP)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
                print(f"  Log group already exists: {INSTRUCTOR_LOG_GROUP}")
            else:
                raise

        # 7-day retention — covers class plus review window
        self.logs.put_retention_policy(
            logGroupName=INSTRUCTOR_LOG_GROUP, retentionInDays=7
        )

        self._resources["log_group"] = INSTRUCTOR_LOG_GROUP
        print(f"  Log group ready: {INSTRUCTOR_LOG_GROUP} (retention: 7 days)")
        return INSTRUCTOR_LOG_GROUP

    # ------------------------------------------------------------------
    # AgentCore Gateway
    # ------------------------------------------------------------------
    def _find_gateway(self) -> dict | None:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_gateways(**request)
            for item in response.get("items", []):
                if item.get("name") == INSTRUCTOR_GATEWAY_NAME:
                    return self.agentcore.get_gateway(
                        gatewayIdentifier=item["gatewayId"]
                    )
            next_token = response.get("nextToken")
            if not next_token:
                return None

    def create_gateway(self, role_arn: str) -> tuple[str, str]:
        gateway_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":gateway/{INSTRUCTOR_GATEWAY_NAME}"
        )

        if self.dry_run:
            print(
                f"  [DRY RUN] Would create or reuse AgentCore Gateway: {INSTRUCTOR_GATEWAY_NAME}"
            )
            self._resources["gateway_name"] = INSTRUCTOR_GATEWAY_NAME
            self._resources["gateway_arn"] = gateway_arn
            return INSTRUCTOR_GATEWAY_NAME, gateway_arn

        gateway = self._find_gateway()
        if gateway:
            print(f"  Gateway already exists: {INSTRUCTOR_GATEWAY_NAME} (reusing)")
        else:
            try:
                gateway = self.agentcore.create_gateway(
                    name=INSTRUCTOR_GATEWAY_NAME,
                    description="Instructor demo Gateway for AgentCore course",
                    roleArn=role_arn,
                    protocolType="MCP",
                    authorizerType="AWS_IAM",
                )
                print(f"  Created Gateway: {INSTRUCTOR_GATEWAY_NAME}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConflictException":
                    raise
                gateway = self._find_gateway()
                if not gateway:
                    raise
                print(f"  Gateway already exists: {INSTRUCTOR_GATEWAY_NAME} (reusing)")

        actual_arn = gateway["gatewayArn"]
        gateway_id = gateway["gatewayId"]
        self._resources["gateway_name"] = INSTRUCTOR_GATEWAY_NAME
        self._resources["gateway_arn"] = actual_arn
        self._resources["gateway_id"] = gateway_id
        self._resources["gateway_url"] = gateway.get("gatewayUrl", "")
        return INSTRUCTOR_GATEWAY_NAME, actual_arn

    # ------------------------------------------------------------------
    # AgentCore Memory
    # ------------------------------------------------------------------
    def _find_memory(self) -> dict | None:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_memories(**request)
            for item in response.get("memories", []):
                memory = self.agentcore.get_memory(memoryId=item["id"])["memory"]
                if memory.get("name") == INSTRUCTOR_MEMORY_NAME:
                    return memory
            next_token = response.get("nextToken")
            if not next_token:
                return None

    def create_memory(self) -> tuple[str, str]:
        memory_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":memory/{INSTRUCTOR_MEMORY_NAME}"
        )

        if self.dry_run:
            print(
                f"  [DRY RUN] Would create or reuse AgentCore Memory: {INSTRUCTOR_MEMORY_NAME}"
            )
            self._resources["memory_name"] = INSTRUCTOR_MEMORY_NAME
            self._resources["memory_arn"] = memory_arn
            return INSTRUCTOR_MEMORY_NAME, memory_arn

        memory = self._find_memory()
        if memory:
            print(f"  Memory already exists: {INSTRUCTOR_MEMORY_NAME} (reusing)")
        else:
            try:
                response = self.agentcore.create_memory(
                    name=INSTRUCTOR_MEMORY_NAME,
                    description="Instructor demo short-term memory for AgentCore course",
                    eventExpiryDuration=30,
                )
                memory = response["memory"]
                print(f"  Created Memory: {INSTRUCTOR_MEMORY_NAME}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConflictException":
                    raise
                memory = self._find_memory()
                if not memory:
                    raise
                print(f"  Memory already exists: {INSTRUCTOR_MEMORY_NAME} (reusing)")

        actual_arn = memory["arn"]
        self._resources["memory_name"] = INSTRUCTOR_MEMORY_NAME
        self._resources["memory_arn"] = actual_arn
        self._resources["memory_id"] = memory["id"]
        return INSTRUCTOR_MEMORY_NAME, actual_arn

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def write_outputs(
        self,
        bucket: str,
        s3_key: str,
        execution_role_arn: str,
        gateway_name: str,
        gateway_arn: str,
        memory_name: str,
        memory_arn: str,
        log_group: str,
    ) -> None:
        """Write instructor.env and instructor-manifest.json."""
        if self.dry_run:
            print("\n  [DRY RUN] Would write instructor environment and manifest files")
            return

        out_dir = Path(OUTPUT_DIR)
        out_dir.mkdir(exist_ok=True)

        # --- instructor.env ---
        env_content = textwrap.dedent(f"""\
        # Instructor Demo Sandbox — Bedrock AgentCore Course
        # Generated by provision-instructor.py
        export AWS_DEFAULT_REGION={self.region}
        export EXECUTION_ROLE_ARN={execution_role_arn}
        export S3_BUCKET={bucket}
        export S3_KEY={s3_key}
        export GATEWAY_NAME={gateway_name}
        export GATEWAY_ID={self._resources.get('gateway_id', '')}
        export GATEWAY_ARN={gateway_arn}
        export GATEWAY_URL={self._resources.get('gateway_url', '')}
        export MEMORY_NAME={memory_name}
        export MEMORY_ID={self._resources.get('memory_id', '')}
        export MEMORY_ARN={memory_arn}
        export LOG_GROUP={log_group}
        """)
        (out_dir / INSTRUCTOR_ENV_FILE).write_text(env_content)
        print(f"\n  Wrote: {out_dir / INSTRUCTOR_ENV_FILE}")

        # --- instructor-manifest.json (for teardown) ---
        manifest = {
            "bucket": bucket,
            "s3_key": s3_key,
            "execution_role_name": INSTRUCTOR_EXECUTION_ROLE_NAME,
            "execution_policy_name": INSTRUCTOR_EXECUTION_POLICY_NAME,
            "gateway_name": gateway_name,
            "gateway_id": self._resources.get("gateway_id", ""),
            "gateway_arn": gateway_arn,
            "gateway_url": self._resources.get("gateway_url", ""),
            "memory_name": memory_name,
            "memory_id": self._resources.get("memory_id", ""),
            "memory_arn": memory_arn,
            "log_group": log_group,
            "region": self.region,
        }
        manifest_path = out_dir / INSTRUCTOR_MANIFEST_FILE
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"  Wrote: {manifest_path}")


# ---------------------------------------------------------------------------
# Verification instructions
# ---------------------------------------------------------------------------
VERIFICATION_INSTRUCTIONS = """
======================================================================
  VALIDATION — Instructor Demo Sandbox
======================================================================

Run the automated validator with the same AWS credentials used here:

  python3 validate-instructor.py \\
    --manifest env/instructor-manifest.json --region {region}

To include a live Bedrock model invocation:

  python3 validate-instructor.py \\
    --manifest env/instructor-manifest.json --region {region} \\
    --model-id <model-id>

The validator checks the AWS account, S3 security and agent artifact,
IAM execution role and policy, Gateway READY status, Memory ACTIVE
status, CloudWatch log group retention, and optional model access. It
returns exit code 1 if any resource check fails.

After deploying the real demo agent from the bedrock-companion repo,
also verify Runtime invocation, Memory continuity across two requests,
and trace events in CloudWatch. Export trace JSON and screenshots as
fallback classroom evidence.

Gateway ID: {gateway_id}
Log group:  {log_group}

======================================================================
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Provision instructor demo sandbox for Bedrock AgentCore course",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Reference documents:
          provisioning-instructor.md  — full provisioning specification
          provisioning-student.md     — student sandbox specification
        """),
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making AWS calls",
    )
    args = parser.parse_args()

    if not args.dry_run:
        _require_boto3()

    p = InstructorProvisioner(region=args.region, dry_run=args.dry_run)

    print("=" * 60)
    print("  Bedrock AgentCore — Instructor Demo Provisioning")
    print("=" * 60)
    print(f"  Region:    {args.region}")
    print(f"  Account:   {p.account_id}")
    print(f"  Dry run:   {args.dry_run}")
    print()

    # ---- Step 1: S3 bucket ----
    print("[1/6] Creating S3 bucket...")
    bucket = p.create_bucket()
    bucket_arn = f"arn:aws:s3:::{bucket}"

    # ---- Step 2: CloudWatch log group ----
    print("\n[2/6] Creating CloudWatch log group...")
    log_group = p.create_log_group()

    # ---- Step 3: IAM execution role ----
    print("\n[3/6] Creating IAM execution role...")
    execution_role_arn = p.create_execution_role()

    if not args.dry_run:
        print("  Waiting for IAM propagation (10s)...")
        time.sleep(10)

    # ---- Step 4: AgentCore Gateway ----
    print("\n[4/6] Creating AgentCore Gateway...")
    gateway_name, gateway_arn = p.create_gateway(execution_role_arn)

    # ---- Step 5: AgentCore Memory ----
    print("\n[5/6] Creating AgentCore Memory...")
    memory_name, memory_arn = p.create_memory()
    p.attach_execution_policy(
        bucket_arn=bucket_arn,
        gateway_arn=gateway_arn,
        memory_arn=memory_arn,
    )

    # ---- Step 6: Upload placeholder agent ----
    print("\n[6/6] Uploading placeholder agent artifact...")
    s3_key = p.upload_placeholder_agent(bucket)

    # ---- Output ----
    p.write_outputs(
        bucket=bucket,
        s3_key=s3_key,
        execution_role_arn=execution_role_arn,
        gateway_name=gateway_name,
        gateway_arn=gateway_arn,
        memory_name=memory_name,
        memory_arn=memory_arn,
        log_group=log_group,
    )

    print("\n" + "=" * 60)
    print("  Instructor demo provisioning complete.")
    print("=" * 60)
    print(
        VERIFICATION_INSTRUCTIONS.format(
            region=args.region,
            gateway_id=p._resources.get("gateway_id", "<gateway-id>"),
            log_group=log_group,
        )
    )


if __name__ == "__main__":
    main()
