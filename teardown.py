#!/usr/bin/env python3
"""
Tear down all resources created by provision-instructor.py and provision-students.py.

Reads the manifest files from env/ and cleans up every resource type:
  - S3 buckets (all objects and versions, then the bucket)
  - IAM sandbox users (console login profile, access keys, inline policies, then the user)
  - IAM execution roles (inline policies, then the role)
  - AgentCore Gateways
  - AgentCore Memory resources
  - CloudWatch log groups

Supports both instructor and student manifests.  Auto-detects the manifest
type from its structure.

Usage
-----
  # Tear down instructor demo
  python3 teardown.py --manifest env/instructor-manifest.json

  # Tear down student sandboxes
  python3 teardown.py --manifest env/student-manifest.json

  # Tear down both (if both manifests exist)
  python3 teardown.py --all

  # Dry run — print what would be deleted (no AWS calls)
  python3 teardown.py --manifest env/student-manifest.json --dry-run

Prerequisites
-------------
  - Python 3.10+
  - boto3 installed (pip3 install boto3)
  - AWS credentials with the same permissions used for provisioning:
      IAMFullAccess, AmazonS3FullAccess, CloudWatchFullAccess,
      AmazonBedrockFullAccess, plus the AgentCore inline policy
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy boto3 import so --dry-run works without it
# ---------------------------------------------------------------------------
boto3 = None
ClientError = None
GATEWAY_TARGET_DELETE_TIMEOUT_SECONDS = 300
GATEWAY_TARGET_DELETE_POLL_SECONDS = 5


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
# Manifest helpers
# ---------------------------------------------------------------------------

def _is_instructor_manifest(manifest: dict) -> bool:
    """Return True if the manifest is from provision-instructor.py."""
    return "teams" not in manifest and "execution_role_name" in manifest


def _is_student_manifest(manifest: dict) -> bool:
    """Return True if the manifest is from provision-students.py."""
    return "teams" in manifest


# ---------------------------------------------------------------------------
# Teardown class
# ---------------------------------------------------------------------------

class Teardown:
    """
    Tears down all resources tracked in a provisioning manifest.

    Handles both instructor and student manifest formats automatically.
    """

    def __init__(self, region: str, dry_run: bool = False):
        self.region = region
        self.dry_run = dry_run

        if dry_run:
            self.iam = None
            self.s3 = None
            self.logs = None
            self.agentcore = None
            self.lambda_ = None
        else:
            self.iam = boto3.client("iam", region_name=region)
            self.s3 = boto3.client("s3", region_name=region)
            self.logs = boto3.client("logs", region_name=region)
            self.lambda_ = boto3.client("lambda", region_name=region)

            self.agentcore = boto3.client(
                "bedrock-agentcore-control", region_name=region
            )

        self._errors: list[str] = []

    # ------------------------------------------------------------------
    # S3
    # ------------------------------------------------------------------
    def delete_bucket(self, bucket: str) -> None:
        """Empty and delete an S3 bucket (handles versioned objects)."""
        if self.dry_run:
            print(f"  [DRY RUN] Would empty and delete S3 bucket: {bucket}")
            return

        try:
            # Delete all object versions (including delete markers)
            paginator = self.s3.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
                if versions:
                    self.s3.delete_objects(
                        Bucket=bucket,
                        Delete={
                            "Objects": [
                                {"Key": v["Key"], "VersionId": v["VersionId"]}
                                for v in versions
                            ]
                        },
                    )
                    print(f"    Deleted {len(versions)} object versions")

            self.s3.delete_bucket(Bucket=bucket)
            print(f"    Deleted S3 bucket: {bucket}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "NoSuchBucket":
                print(f"    Bucket already deleted: {bucket}")
            else:
                msg = f"    ERROR deleting bucket {bucket}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # IAM — users
    # ------------------------------------------------------------------
    def delete_user(self, user_name: str) -> None:
        """Delete access keys, inline policies, and the IAM user."""
        if self.dry_run:
            print(f"  [DRY RUN] Would delete IAM user: {user_name}")
            return

        try:
            # Delete console login profile (must go before the user itself)
            try:
                self.iam.delete_login_profile(UserName=user_name)
                print(f"    Deleted console login profile")
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    raise

            # Delete access keys
            keys = self.iam.list_access_keys(UserName=user_name).get(
                "AccessKeyMetadata", []
            )
            for k in keys:
                self.iam.delete_access_key(
                    UserName=user_name, AccessKeyId=k["AccessKeyId"]
                )
                print(f"    Deleted access key: {k['AccessKeyId']}")

            # Delete inline policies
            policies = self.iam.list_user_policies(UserName=user_name).get(
                "PolicyNames", []
            )
            for p in policies:
                self.iam.delete_user_policy(UserName=user_name, PolicyName=p)
                print(f"    Deleted inline policy: {p}")

            # Delete attached managed policies (if any were added manually)
            attached = self.iam.list_attached_user_policies(UserName=user_name).get(
                "AttachedPolicies", []
            )
            for pol in attached:
                self.iam.detach_user_policy(
                    UserName=user_name, PolicyArn=pol["PolicyArn"]
                )
                print(f"    Detached managed policy: {pol['PolicyName']}")

            # Delete the user
            self.iam.delete_user(UserName=user_name)
            print(f"    Deleted IAM user: {user_name}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "NoSuchEntity":
                print(f"    User already deleted: {user_name}")
            else:
                msg = f"    ERROR deleting user {user_name}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # IAM — roles
    # ------------------------------------------------------------------
    def delete_role(self, role_name: str) -> None:
        """Delete all inline policies and the IAM role."""
        if self.dry_run:
            print(f"  [DRY RUN] Would delete IAM role: {role_name}")
            return

        try:
            # Delete inline policies
            policies = self.iam.list_role_policies(RoleName=role_name).get(
                "PolicyNames", []
            )
            for p in policies:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)
                print(f"    Deleted inline policy: {p}")

            # Detach managed policies
            attached = self.iam.list_attached_role_policies(RoleName=role_name).get(
                "AttachedPolicies", []
            )
            for pol in attached:
                self.iam.detach_role_policy(
                    RoleName=role_name, PolicyArn=pol["PolicyArn"]
                )
                print(f"    Detached managed policy: {pol['PolicyName']}")

            # Delete the role
            self.iam.delete_role(RoleName=role_name)
            print(f"    Deleted IAM role: {role_name}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "NoSuchEntity":
                print(f"    Role already deleted: {role_name}")
            else:
                msg = f"    ERROR deleting role {role_name}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # Lambda
    # ------------------------------------------------------------------
    def delete_lambda(self, function_name: str) -> None:
        """Delete a Lambda function."""
        if self.dry_run:
            print(f"  [DRY RUN] Would delete Lambda: {function_name}")
            return

        try:
            self.lambda_.delete_function(FunctionName=function_name)
            print(f"    Deleted Lambda: {function_name}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                print(f"    Lambda already deleted: {function_name}")
            else:
                msg = f"    ERROR deleting Lambda {function_name}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # CloudWatch Logs
    # ------------------------------------------------------------------
    def delete_log_group(self, log_group: str) -> None:
        """Delete a CloudWatch log group."""
        if self.dry_run:
            print(f"  [DRY RUN] Would delete log group: {log_group}")
            return

        try:
            self.logs.delete_log_group(logGroupName=log_group)
            print(f"    Deleted log group: {log_group}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                print(f"    Log group already deleted: {log_group}")
            else:
                msg = f"    ERROR deleting log group {log_group}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # AgentCore Gateway
    # ------------------------------------------------------------------
    def _gateway_id_for_name(self, gateway_name: str) -> str:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_gateways(**request)
            for item in response.get("items", []):
                if item.get("name") == gateway_name:
                    return item["gatewayId"]
            next_token = response.get("nextToken")
            if not next_token:
                return ""

    def _gateway_targets(self, gateway_identifier: str) -> list[dict]:
        targets = []
        next_token = None
        while True:
            request = {"gatewayIdentifier": gateway_identifier, "maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_gateway_targets(**request)
            targets.extend(response.get("items", []))
            next_token = response.get("nextToken")
            if not next_token:
                return targets

    def delete_gateway_targets(self, gateway_identifier: str) -> bool:
        """Delete every target on a Gateway. DeleteGateway fails while any target remains."""
        if self.dry_run:
            print(f"  [DRY RUN] Would delete Gateway targets on: {gateway_identifier}")
            return True
        if not gateway_identifier:
            return True

        try:
            for item in self._gateway_targets(gateway_identifier):
                target_name = item.get("name", item["targetId"])
                if item.get("status") == "DELETING":
                    print(f"    Gateway target already deleting: {target_name}")
                    continue
                self.agentcore.delete_gateway_target(
                    gatewayIdentifier=gateway_identifier,
                    targetId=item["targetId"],
                )
                print(f"    Deleting Gateway target: {target_name}")

            deadline = time.monotonic() + GATEWAY_TARGET_DELETE_TIMEOUT_SECONDS
            while self._gateway_targets(gateway_identifier):
                if time.monotonic() >= deadline:
                    msg = (
                        f"    ERROR deleting Gateway targets on {gateway_identifier}: "
                        f"targets remained after "
                        f"{GATEWAY_TARGET_DELETE_TIMEOUT_SECONDS} seconds"
                    )
                    print(msg)
                    self._errors.append(msg)
                    return False
                print(
                    "    Waiting for Gateway target deletion "
                    f"({GATEWAY_TARGET_DELETE_POLL_SECONDS}s)..."
                )
                time.sleep(GATEWAY_TARGET_DELETE_POLL_SECONDS)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "NotFoundException"):
                return True
            msg = f"    ERROR deleting Gateway targets on {gateway_identifier}: {e}"
            print(msg)
            self._errors.append(msg)
            return False

    def delete_gateway(self, gateway_identifier: str, gateway_name: str = "") -> None:
        """Delete an AgentCore Gateway (and any targets still attached to it)."""
        display_name = gateway_name or gateway_identifier
        if self.dry_run:
            print(f"  [DRY RUN] Would delete Gateway: {display_name}")
            return

        identifier = gateway_identifier or self._gateway_id_for_name(gateway_name)
        if not identifier:
            print(f"    Gateway already deleted: {display_name}")
            return

        if not self.delete_gateway_targets(identifier):
            return

        try:
            self.agentcore.delete_gateway(gatewayIdentifier=identifier)
            print(f"    Deleted Gateway: {display_name}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "NotFoundException"):
                print(f"    Gateway already deleted: {display_name}")
            else:
                msg = f"    ERROR deleting Gateway {display_name}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # AgentCore Memory
    # ------------------------------------------------------------------
    def _memory_id_for_name(self, memory_name: str) -> str:
        next_token = None
        while True:
            request = {"maxResults": 100}
            if next_token:
                request["nextToken"] = next_token
            response = self.agentcore.list_memories(**request)
            for item in response.get("memories", []):
                memory = self.agentcore.get_memory(memoryId=item["id"])["memory"]
                if memory.get("name") == memory_name:
                    return memory["id"]
            next_token = response.get("nextToken")
            if not next_token:
                return ""

    def delete_memory(self, memory_id: str, memory_name: str = "") -> None:
        """Delete an AgentCore Memory resource."""
        display_name = memory_name or memory_id
        if self.dry_run:
            print(f"  [DRY RUN] Would delete Memory: {display_name}")
            return

        identifier = memory_id or self._memory_id_for_name(memory_name)
        if not identifier:
            print(f"    Memory already deleted: {display_name}")
            return

        try:
            self.agentcore.delete_memory(memoryId=identifier)
            print(f"    Deleted Memory: {display_name}")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "NotFoundException"):
                print(f"    Memory already deleted: {display_name}")
            else:
                msg = f"    ERROR deleting Memory {display_name}: {e}"
                print(msg)
                self._errors.append(msg)

    # ------------------------------------------------------------------
    # Orchestration — run teardown from a manifest
    # ------------------------------------------------------------------
    def teardown_instructor(self, manifest: dict) -> None:
        """Tear down all instructor demo resources."""
        print("\n--- Instructor Demo ---")

        # Order matters: delete dependencies before the resources they depend on.
        # Delete the Gateway before its shared IAM execution role.

        # 1. AgentCore Gateway
        gateway = manifest.get("gateway_name", "")
        if gateway:
            print(f"\n  [Gateway] {gateway}")
            self.delete_gateway(manifest.get("gateway_id", ""), gateway)

        # 2. AgentCore Memory
        memory = manifest.get("memory_name", "")
        if memory:
            print(f"\n  [Memory] {memory}")
            self.delete_memory(manifest.get("memory_id", ""), memory)

        # 3. CloudWatch log group
        log_group = manifest.get("log_group", "")
        if log_group:
            print(f"\n  [Log group] {log_group}")
            self.delete_log_group(log_group)

        # 4. IAM execution role (delete policy first, then role)
        role_name = manifest.get("execution_role_name", "")
        if role_name:
            print(f"\n  [IAM role] {role_name}")
            self.delete_role(role_name)

        # 5. S3 bucket (empty first, then delete)
        bucket = manifest.get("bucket", "")
        if bucket:
            print(f"\n  [S3 bucket] {bucket}")
            self.delete_bucket(bucket)

    def teardown_student_team(self, team: dict) -> None:
        """Tear down all resources for one student team."""
        tag = f"Team{team['team_num']:02d}"
        print(f"\n--- {tag} ---")

        # 1. IAM user (keys, policies, then user)
        user = team.get("user_name", "")
        if user:
            print(f"\n  [IAM user] {user}")
            self.delete_user(user)

        # 2. AgentCore Gateway (deletes its targets first)
        gateway = team.get("gateway_name", "")
        if gateway:
            print(f"\n  [Gateway] {gateway}")
            self.delete_gateway(team.get("gateway_id", ""), gateway)

        # 2b. Order-status Lambda backing the Gateway target, and its role
        lambda_name = team.get("order_status_lambda_name", "")
        if lambda_name:
            print(f"\n  [Lambda] {lambda_name}")
            self.delete_lambda(lambda_name)

        lambda_role = team.get("order_status_lambda_role_name", "")
        if lambda_role:
            print(f"\n  [IAM role] {lambda_role}")
            self.delete_role(lambda_role)

        # 3. AgentCore Memory
        memory = team.get("memory_name", "")
        if memory:
            print(f"\n  [Memory] {memory}")
            self.delete_memory(team.get("memory_id", ""), memory)

        # 4. CloudWatch log group
        log_group = team.get("log_group", "")
        if log_group:
            print(f"\n  [Log group] {log_group}")
            self.delete_log_group(log_group)

        # 5. IAM execution role
        role = team.get("execution_role_name", "")
        if role:
            print(f"\n  [IAM role] {role}")
            self.delete_role(role)

        # 6. S3 bucket (empty first, then delete)
        bucket = team.get("bucket", "")
        if bucket:
            print(f"\n  [S3 bucket] {bucket}")
            self.delete_bucket(bucket)

    def run(self, manifest: dict) -> None:
        """Run teardown for the given manifest (auto-detects type)."""
        if _is_instructor_manifest(manifest):
            self.teardown_instructor(manifest)
        elif _is_student_manifest(manifest):
            teams = manifest.get("teams", [])
            print(f"\nTearing down {len(teams)} student team(s)...")
            for team in teams:
                self.teardown_student_team(team)
        else:
            print("ERROR: Unrecognized manifest format.")
            sys.exit(1)

        if self._errors:
            print(f"\nCompleted with {len(self._errors)} error(s):")
            for err in self._errors:
                print(f"  - {err}")
        else:
            print("\nAll resources deleted successfully.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Tear down resources created by provision-instructor.py or provision-students.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python3 teardown.py --manifest env/instructor-manifest.json
          python3 teardown.py --manifest env/student-manifest.json
          python3 teardown.py --all
          python3 teardown.py --all --dry-run
        """),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--manifest",
        help="Path to a manifest JSON file (instructor-manifest.json or student-manifest.json)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Tear down both instructor and student resources (looks for both manifests in env/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making AWS calls",
    )
    args = parser.parse_args()

    if not args.dry_run:
        _require_boto3()

    # Determine which manifests to process
    manifests_to_process: list[Path] = []

    if args.all:
        instructor_path = Path("env/instructor-manifest.json")
        student_path = Path("env/student-manifest.json")

        if instructor_path.exists():
            manifests_to_process.append(instructor_path)
        else:
            print(f"  Skipping instructor manifest (not found): {instructor_path}")

        if student_path.exists():
            manifests_to_process.append(student_path)
        else:
            print(f"  Skipping student manifest (not found): {student_path}")

        if not manifests_to_process:
            print("ERROR: No manifest files found in env/.")
            print("Expected: env/instructor-manifest.json or env/student-manifest.json")
            sys.exit(1)
    else:
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"ERROR: Manifest not found: {manifest_path}")
            print("Run provision-instructor.py or provision-students.py first.")
            sys.exit(1)
        manifests_to_process.append(manifest_path)

    # Process each manifest
    for manifest_path in manifests_to_process:
        manifest = json.loads(manifest_path.read_text())

        print("=" * 60)
        print(f"  Bedrock AgentCore — Teardown")
        print("=" * 60)
        print(f"  Manifest:  {manifest_path}")
        print(f"  Region:    {manifest.get('region', 'unknown')}")
        print(f"  Dry run:   {args.dry_run}")

        if _is_instructor_manifest(manifest):
            print(f"  Type:      Instructor demo")
        elif _is_student_manifest(manifest):
            teams = manifest.get("teams", [])
            print(f"  Type:      Student sandboxes ({len(teams)} team(s))")
        else:
            print(f"  Type:      Unknown")
            sys.exit(1)

        td = Teardown(region=manifest.get("region", "us-east-1"), dry_run=args.dry_run)
        td.run(manifest)

        print("\n" + "=" * 60)
        print("  Teardown complete for this manifest.")
        print("=" * 60)

    print("\nDone.")


if __name__ == "__main__":
    main()
