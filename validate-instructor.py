#!/usr/bin/env python3
"""Validate resources created by provision-instructor.py."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    print("ERROR: boto3 is required. Install it: pip3 install -r requirements.txt")
    sys.exit(2)


DEFAULT_MANIFEST = "env/instructor-manifest.json"
PLACEHOLDER_AGENT_KEY = "instructor-demo/v1/agent.py"


class ValidationError(Exception):
    pass


class InstructorValidator:
    def __init__(
        self,
        manifest: dict,
        region: str,
        model_id: str | None,
        wait_seconds: int,
    ):
        self.manifest = manifest
        self.region = region
        self.model_id = model_id
        self.wait_seconds = wait_seconds
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.sts = boto3.client("sts", region_name=region)
        self.s3 = boto3.client("s3", region_name=region)
        self.iam = boto3.client("iam", region_name=region)
        self.logs = boto3.client("logs", region_name=region)
        self.agentcore = boto3.client("bedrock-agentcore-control", region_name=region)

    def check(self, name: str, operation: Callable[[], str]) -> None:
        try:
            detail = operation()
        except ClientError as error:
            aws_error = error.response.get("Error", {})
            code = aws_error.get("Code", "Unknown")
            message = aws_error.get("Message", str(error))
            print(f"[FAIL] {name}: {code}: {message}")
            self.failed += 1
        except (BotoCoreError, KeyError, TypeError, ValidationError) as error:
            print(f"[FAIL] {name}: {error}")
            self.failed += 1
        else:
            print(f"[PASS] {name}: {detail}")
            self.passed += 1

    def skip(self, name: str, reason: str) -> None:
        print(f"[SKIP] {name}: {reason}")
        self.skipped += 1

    def validate_identity(self) -> str:
        identity = self.sts.get_caller_identity()
        expected_account = self.manifest["gateway_arn"].split(":")[4]
        if identity["Account"] != expected_account:
            raise ValidationError(
                f"current account {identity['Account']} does not match manifest account {expected_account}"
            )
        return f"{identity['Arn']} (account {identity['Account']})"

    def validate_s3(self) -> str:
        bucket = self.manifest["bucket"]
        self.s3.head_bucket(Bucket=bucket)
        encryption = self.s3.get_bucket_encryption(Bucket=bucket)
        rules = encryption["ServerSideEncryptionConfiguration"]["Rules"]
        algorithms = {
            rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
            for rule in rules
        }
        if not algorithms.intersection({"AES256", "aws:kms"}):
            raise ValidationError("bucket default encryption is not enabled")
        public_access = self.s3.get_public_access_block(Bucket=bucket)[
            "PublicAccessBlockConfiguration"
        ]
        if not all(public_access.values()):
            raise ValidationError("not all S3 public-access blocks are enabled")
        key = self.manifest.get("s3_key", PLACEHOLDER_AGENT_KEY)
        artifact = self.s3.head_object(Bucket=bucket, Key=key)
        return (
            f"{bucket}; encrypted; public access blocked; "
            f"artifact present ({artifact['ContentLength']} bytes)"
        )

    def validate_iam(self) -> str:
        role_name = self.manifest["execution_role_name"]
        role = self.iam.get_role(RoleName=role_name)["Role"]
        expected_arn = (
            f"arn:aws:iam::{self.sts.get_caller_identity()['Account']}:role/{role_name}"
        )
        if role["Arn"] != expected_arn:
            raise ValidationError(f"role ARN is {role['Arn']}, expected {expected_arn}")
        self.iam.get_role_policy(
            RoleName=role_name,
            PolicyName=self.manifest["execution_policy_name"],
        )
        return f"{role['Arn']}; inline policy {self.manifest['execution_policy_name']} present"

    def validate_gateway(self) -> str:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            gateway = self.agentcore.get_gateway(
                gatewayIdentifier=self.manifest["gateway_id"]
            )
            if gateway["status"] not in {"CREATING", "UPDATING"}:
                break
            if time.monotonic() >= deadline:
                raise ValidationError(
                    f"status remained {gateway['status']} after {self.wait_seconds} seconds"
                )
            time.sleep(5)
        if gateway["gatewayArn"] != self.manifest["gateway_arn"]:
            raise ValidationError("Gateway ARN does not match the manifest")
        if gateway["status"] != "READY":
            reasons = "; ".join(gateway.get("statusReasons", [])) or "no status reason returned"
            raise ValidationError(f"status is {gateway['status']}: {reasons}")
        return f"{gateway['name']} ({gateway['gatewayId']}) is READY"

    def validate_memory(self) -> str:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            memory = self.agentcore.get_memory(
                memoryId=self.manifest["memory_id"]
            )["memory"]
            if memory["status"] not in {"CREATING", "UPDATING"}:
                break
            if time.monotonic() >= deadline:
                raise ValidationError(
                    f"status remained {memory['status']} after {self.wait_seconds} seconds"
                )
            time.sleep(5)
        if memory["arn"] != self.manifest["memory_arn"]:
            raise ValidationError("Memory ARN does not match the manifest")
        if memory["status"] != "ACTIVE":
            raise ValidationError(f"status is {memory['status']}")
        return f"{memory['name']} ({memory['id']}) is ACTIVE"

    def validate_logs(self) -> str:
        log_group = self.manifest["log_group"]
        response = self.logs.describe_log_groups(logGroupNamePrefix=log_group)
        matches = [
            item for item in response.get("logGroups", [])
            if item["logGroupName"] == log_group
        ]
        if not matches:
            raise ValidationError(f"log group {log_group} was not found")
        retention = matches[0].get("retentionInDays")
        if retention != 7:
            raise ValidationError(f"retention is {retention!r} days, expected 7")
        return f"{log_group}; retention is 7 days"

    def validate_model(self) -> str:
        runtime = boto3.client("bedrock-runtime", region_name=self.region)
        response = runtime.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0},
        )
        stop_reason = response.get("stopReason", "unknown")
        return f"{self.model_id} responded (stop reason: {stop_reason})"

    def run(self) -> int:
        print("=" * 70)
        print("  Bedrock AgentCore — Instructor Demo Validation")
        print("=" * 70)
        print(f"  Region:   {self.region}")
        print(f"  Gateway:  {self.manifest.get('gateway_id', '<missing>')}")
        print(f"  Memory:   {self.manifest.get('memory_id', '<missing>')}")
        print()

        self.check("AWS identity", self.validate_identity)
        self.check("S3 bucket and artifact", self.validate_s3)
        self.check("IAM execution role", self.validate_iam)
        self.check("AgentCore Gateway", self.validate_gateway)
        self.check("AgentCore Memory", self.validate_memory)
        self.check("CloudWatch log group", self.validate_logs)
        if self.model_id:
            self.check("Bedrock model invocation", self.validate_model)
        else:
            self.skip("Bedrock model invocation", "pass --model-id to enable")

        print()
        print("=" * 70)
        print(
            f"  Result: {self.passed} passed, {self.failed} failed, "
            f"{self.skipped} skipped"
        )
        print("=" * 70)
        return 1 if self.failed else 0


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValidationError(
            f"manifest not found: {path}; run provision-instructor.py first"
        )
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid JSON in {path}: {error}")

    required = {
        "bucket",
        "execution_role_name",
        "execution_policy_name",
        "gateway_id",
        "gateway_arn",
        "memory_id",
        "memory_arn",
        "log_group",
        "region",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValidationError(f"manifest is missing: {', '.join(missing)}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the instructor resources created by provision-instructor.py"
    )
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help=f"provisioning manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--region",
        help="AWS region override (default: region recorded in the manifest)",
    )
    parser.add_argument(
        "--model-id",
        help="optionally invoke this Bedrock model as the current AWS identity",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="maximum wait for asynchronous resources (default: 120)",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(Path(args.manifest))
    except ValidationError as error:
        print(f"ERROR: {error}")
        return 2

    region = args.region or manifest["region"]
    if args.region and args.region != manifest["region"]:
        print(
            f"ERROR: region override {args.region} does not match manifest region "
            f"{manifest['region']}"
        )
        return 2
    if args.wait_seconds < 0:
        print("ERROR: --wait-seconds cannot be negative")
        return 2

    return InstructorValidator(
        manifest,
        region,
        args.model_id,
        args.wait_seconds,
    ).run()


if __name__ == "__main__":
    sys.exit(main())
