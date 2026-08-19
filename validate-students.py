#!/usr/bin/env python3
"""Validate resources created by provision-students.py."""

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


DEFAULT_MANIFEST = "env/student-manifest.json"
DEFAULT_MODEL_ID = "openai.gpt-oss-20b-1:0"
EXECUTION_POLICY_NAME = "AgentCoreExecutionPolicy"
SANDBOX_POLICY_NAME = "AgentCoreSandboxPolicy"


class ValidationError(Exception):
    pass


class StudentValidator:
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
            print(f"    [FAIL] {name}: {code}: {message}")
            self.failed += 1
        except (BotoCoreError, KeyError, TypeError, ValidationError) as error:
            print(f"    [FAIL] {name}: {error}")
            self.failed += 1
        else:
            if detail is None:
                self.skip(name, "insufficient permissions to verify; resource state unknown")
            else:
                print(f"    [PASS] {name}: {detail}")
                self.passed += 1

    def skip(self, name: str, reason: str) -> None:
        print(f"    [SKIP] {name}: {reason}")
        self.skipped += 1

    def validate_identity(self) -> str:
        identity = self.sts.get_caller_identity()
        return f"{identity['Arn']} (account {identity['Account']})"

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

    def validate_s3(self, team: dict) -> str:
        bucket = team["bucket"]
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
        key = team.get("s3_key") or (
            f"{team['user_name']}/v1/deployment_package.zip"
        )
        artifact = self.s3.head_object(Bucket=bucket, Key=key)
        return (
            f"{bucket}; encrypted; public access blocked; "
            f"artifact {key} present ({artifact['ContentLength']} bytes)"
        )

    def validate_execution_role(self, team: dict) -> str:
        role_name = team["execution_role_name"]
        role = self.iam.get_role(RoleName=role_name)["Role"]
        expected_arn = (
            f"arn:aws:iam::{self.sts.get_caller_identity()['Account']}:role/{role_name}"
        )
        if role["Arn"] != expected_arn:
            raise ValidationError(f"role ARN is {role['Arn']}, expected {expected_arn}")
        self.iam.get_role_policy(RoleName=role_name, PolicyName=EXECUTION_POLICY_NAME)
        return f"{role['Arn']}; inline policy {EXECUTION_POLICY_NAME} present"

    def validate_gateway(self, team: dict) -> str:
        gateway_name = team["gateway_name"]
        deadline = time.monotonic() + self.wait_seconds
        while True:
            gateway = self._find_gateway(gateway_name)
            if not gateway:
                raise ValidationError(f"gateway {gateway_name} was not found")
            if gateway["status"] not in {"CREATING", "UPDATING"}:
                break
            if time.monotonic() >= deadline:
                raise ValidationError(
                    f"status remained {gateway['status']} after {self.wait_seconds} seconds"
                )
            print(f"    Waiting for Gateway to leave {gateway['status']} (5s)...")
            time.sleep(5)
        if gateway["status"] != "READY":
            reasons = "; ".join(gateway.get("statusReasons", [])) or "no status reason returned"
            raise ValidationError(f"status is {gateway['status']}: {reasons}")
        return f"{gateway['name']} ({gateway['gatewayId']}) is READY"

    def validate_memory(self, team: dict) -> str:
        memory_name = team["memory_name"]
        deadline = time.monotonic() + self.wait_seconds
        while True:
            memory = self._find_memory(memory_name)
            if not memory:
                raise ValidationError(f"memory {memory_name} was not found")
            if memory["status"] not in {"CREATING", "UPDATING"}:
                break
            if time.monotonic() >= deadline:
                raise ValidationError(
                    f"status remained {memory['status']} after {self.wait_seconds} seconds"
                )
            print(f"    Waiting for Memory to leave {memory['status']} (5s)...")
            time.sleep(5)
        if memory["status"] != "ACTIVE":
            raise ValidationError(f"status is {memory['status']}")
        return f"{memory['name']} ({memory['id']}) is ACTIVE"

    def validate_logs(self, team: dict) -> str:
        log_group = team["log_group"]
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

    def validate_sandbox_user(self, team: dict) -> str:
        user_name = team["user_name"]
        self.iam.get_user(UserName=user_name)
        self.iam.get_user_policy(UserName=user_name, PolicyName=SANDBOX_POLICY_NAME)
        keys = self.iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]
        active = [k for k in keys if k["Status"] == "Active"]
        if len(active) != 1:
            raise ValidationError(
                f"expected exactly 1 active access key, found {len(active)}"
            )
        return (
            f"{user_name}; inline policy {SANDBOX_POLICY_NAME} present; "
            f"1 active access key ({active[0]['AccessKeyId']})"
        )

    def validate_console_login(self, team: dict) -> str | None:
        user_name = team["user_name"]
        try:
            self.iam.get_login_profile(UserName=user_name)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "AccessDenied":
                return None
            raise
        return f"{user_name}; console login profile present"

    def validate_model(self) -> str:
        runtime = boto3.client("bedrock-runtime", region_name=self.region)
        response = runtime.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0},
        )
        stop_reason = response.get("stopReason", "unknown")
        return f"{self.model_id} responded (stop reason: {stop_reason})"

    def run(self, teams: list[dict]) -> int:
        print("=" * 70)
        print("  Bedrock AgentCore — Student Sandbox Validation")
        print("=" * 70)
        print(f"  Region:   {self.region}")
        print(f"  Teams:    {', '.join(str(t['team_num']) for t in teams)}")
        print()

        self.check("AWS identity", self.validate_identity)

        for team in teams:
            print(f"\n--- Team{team['team_num']:02d} ---")
            self.check("S3 bucket and artifact", lambda t=team: self.validate_s3(t))
            self.check(
                "IAM execution role",
                lambda t=team: self.validate_execution_role(t),
            )
            self.check("AgentCore Gateway", lambda t=team: self.validate_gateway(t))
            self.check("AgentCore Memory", lambda t=team: self.validate_memory(t))
            self.check("CloudWatch log group", lambda t=team: self.validate_logs(t))
            self.check(
                "IAM sandbox user", lambda t=team: self.validate_sandbox_user(t)
            )
            self.check(
                "Console login profile",
                lambda t=team: self.validate_console_login(t),
            )

        print()
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
            f"manifest not found: {path}; run provision-students.py first"
        )
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid JSON in {path}: {error}")

    required = {"region", "prefix", "teams"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValidationError(f"manifest is missing: {', '.join(missing)}")

    required_team_fields = {
        "team_num",
        "user_name",
        "execution_role_name",
        "bucket",
        "gateway_name",
        "memory_name",
        "log_group",
    }
    for team in manifest["teams"]:
        missing_fields = sorted(required_team_fields - team.keys())
        if missing_fields:
            raise ValidationError(
                f"team {team.get('team_num', '?')} is missing: {', '.join(missing_fields)}"
            )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the student resources created by provision-students.py"
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
        "--team",
        type=int,
        action="append",
        dest="teams",
        help="Validate only this team number (repeatable). Default: all teams in the manifest.",
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help=f"Bedrock model to invoke as the current AWS identity (default: {DEFAULT_MODEL_ID}); pass '' to skip",
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

    teams = manifest["teams"]
    if args.teams:
        wanted = set(args.teams)
        teams = [t for t in teams if t["team_num"] in wanted]
        missing = wanted - {t["team_num"] for t in teams}
        if missing:
            print(f"ERROR: team(s) not found in manifest: {sorted(missing)}")
            return 2

    return StudentValidator(
        manifest,
        region,
        args.model_id,
        args.wait_seconds,
    ).run(teams)


if __name__ == "__main__":
    sys.exit(main())
