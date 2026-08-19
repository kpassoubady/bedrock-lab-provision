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
  - AgentCore Memory         LabAgent_TeamXX_Memory
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
import io
import json
import secrets
import string
import subprocess
import sys
import tempfile
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
# Configuration — matches provisioning-student.md exactly
# ---------------------------------------------------------------------------
DEFAULT_REGION = "us-east-1"
DEFAULT_TEAMS = 10
DEFAULT_PREFIX = "LabAgent"
GATEWAY_READY_TIMEOUT_SECONDS = 300
GATEWAY_POLL_INTERVAL_SECONDS = 5
GATEWAY_TARGET_PERMISSION_RETRIES = 12
GATEWAY_TARGET_PERMISSION_RETRY_SECONDS = 5

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

# The governed agent deployed to each team's Runtime for Lab 05 Checkpoints
# 2-4 (bedrock-companion's deploy_agent.py / check_gateway.py / verify_memory.py).
# Kept in sync by hand with day1/labs/05-operating-the-environment/{start,solution}/agent.py
# in bedrock-companion — embedded here (rather than read from that repo) so this
# script has no cross-repo file dependency at provisioning time.
AGENT_SOURCE = '''\
"""
Lab 05 - Governed agent for the Salesforce case + retail order-status scenario.

Deployed to AgentCore Runtime as a direct-code Python ZIP (entryPoint agent.py).
Implements the Runtime HTTP contract directly (no bedrock-agentcore SDK):
  GET  /ping          health check
  POST /invocations   {"prompt": ..., "actorId": ..., "memorySessionId": ...}

On each invocation the agent:
  1. Loads prior conversation turns for this actorId/memorySessionId from
     AgentCore Memory, so a second, different Runtime session can recall
     what an earlier session was told.
  2. Asks the model to answer the prompt, offering the read-only Gateway
     order-status tool.
  3. Calls the Gateway over MCP (SigV4-signed with the Runtime's own
     execution role) whenever the model asks for the tool.
  4. Writes both the user prompt and the final answer back to Memory.
"""
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import urllib.request

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
GATEWAY_TOOL_NAME = os.environ.get("GATEWAY_TOOL_NAME", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
MODEL_ID = os.environ.get("MODEL_ID", "amazon.nova-lite-v1:0")

SYSTEM_PROMPT = (
    "You help with two kinds of requests: checking a retail order's status "
    "using the order_status tool, and remembering short facts the user tells "
    "you (such as a Salesforce case number) so you can recall them later in "
    "a different conversation. Always call order_status instead of guessing "
    "a status. When asked to recall something, answer only from the "
    "conversation history provided to you."
)

TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "order_status",
                "description": "Look up the status of a retail order.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "orderId": {
                                "type": "string",
                                "description": "The order identifier to look up, e.g. ORD-1001",
                            }
                        },
                        "required": ["orderId"],
                    }
                },
            }
        }
    ]
}

_bedrock = None
_agentcore = None


def bedrock_client():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock


def agentcore_client():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore", region_name=AWS_REGION)
    return _agentcore


def call_gateway_tool(order_id: str) -> str:
    """Invoke the Gateway's order-status MCP tool with the Runtime's own SigV4 identity."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": GATEWAY_TOOL_NAME, "arguments": {"orderId": order_id}},
    }
    body = json.dumps(payload).encode("utf-8")
    credentials = boto3.Session(region_name=AWS_REGION).get_credentials()
    request = AWSRequest(
        method="POST",
        url=GATEWAY_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": "2025-03-26",
        },
    )
    SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", AWS_REGION).add_auth(request)
    urllib_request = urllib.request.Request(
        GATEWAY_URL, data=body, headers=dict(request.headers.items()), method="POST"
    )
    with urllib.request.urlopen(urllib_request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if "error" in result:
        return json.dumps({"error": result["error"]})
    content = result.get("result", {}).get("content", [])
    texts = [item["text"] for item in content if item.get("type") == "text"]
    return "\\n".join(texts) if texts else json.dumps(result.get("result", {}))


def load_memory_turns(actor_id: str, session_id: str):
    """Return prior conversation turns for this actor/session as Converse messages."""
    if not MEMORY_ID:
        return []
    response = agentcore_client().list_events(
        memoryId=MEMORY_ID,
        actorId=actor_id,
        sessionId=session_id,
        includePayloads=True,
        maxResults=100,
    )
    events = sorted(response.get("events", []), key=lambda e: e.get("eventTimestamp", 0))
    messages = []
    for event in events:
        for item in event.get("payload", []):
            turn = item.get("conversational")
            if not turn:
                continue
            text = turn.get("content", {}).get("text", "")
            role = turn.get("role", "USER")
            if not text:
                continue
            messages.append({"role": "user" if role == "USER" else "assistant", "content": [{"text": text}]})
    return messages


def write_memory_turn(actor_id: str, session_id: str, role: str, text: str) -> None:
    if not MEMORY_ID:
        return
    agentcore_client().create_event(
        memoryId=MEMORY_ID,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=time.time(),
        payload=[{"conversational": {"content": {"text": text}, "role": role}}],
    )


def run_conversation(prompt: str, history: list) -> str:
    messages = history + [{"role": "user", "content": [{"text": prompt}]}]

    for _ in range(4):
        response = bedrock_client().converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=TOOL_CONFIG,
        )
        output_message = response["output"]["message"]
        messages.append(output_message)

        if response.get("stopReason") != "tool_use":
            return "".join(block.get("text", "") for block in output_message["content"])

        tool_results = []
        for block in output_message["content"]:
            tool_use = block.get("toolUse")
            if not tool_use:
                continue
            if tool_use["name"] == "order_status":
                result_text = call_gateway_tool(tool_use["input"]["orderId"])
            else:
                result_text = json.dumps({"error": f"unknown tool {tool_use['name']}"})
            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use["toolUseId"],
                        "content": [{"text": result_text}],
                    }
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return "I could not complete that request."


def handle_invocation(body: dict) -> dict:
    prompt = body.get("prompt", "")
    actor_id = body.get("actorId", "")
    session_id = body.get("memorySessionId", "")

    history = load_memory_turns(actor_id, session_id)
    answer = run_conversation(prompt, history)

    write_memory_turn(actor_id, session_id, "USER", prompt)
    write_memory_turn(actor_id, session_id, "ASSISTANT", answer)

    return {"response": answer}


class Handler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/ping":
            self._write_json(200, {"status": "Healthy"})
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invocations":
            self._write_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
            result = handle_invocation(body)
            self._write_json(200, result)
        except Exception as e:
            print(f"INVOCATION_ERROR: {e}", file=sys.stderr, flush=True)
            self._write_json(500, {"error": str(e)})

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}", flush=True)


def main():
    print("AGENT_STARTING: binding 0.0.0.0:8080", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("AGENT_READY: listening on 0.0.0.0:8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''

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
    """LabAgent_TeamXX_Memory (Memory names must match [a-zA-Z][a-zA-Z0-9_]{0,47} — no hyphens)"""
    return f"{prefix.replace('-', '_')}_{_team_tag(team_num)}_Memory"


def _log_group_name(team_num: int) -> str:
    """/aws/bedrock/agentcore/teamXX"""
    return f"/aws/bedrock/agentcore/{_team_tag(team_num).lower()}"


def _agent_name(prefix: str, team_num: int) -> str:
    """LabAgent_TeamXX (used in .env as AGENT_NAME; must match [a-zA-Z][a-zA-Z0-9_]{0,47} — no hyphens)"""
    return f"{prefix.replace('-', '_')}_{_team_tag(team_num)}"


def _gateway_target_lambda_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-OrderStatus (backs the Gateway's read-only tool target)"""
    return f"{prefix}-{_team_tag(team_num)}-OrderStatus"


def _gateway_target_lambda_role_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-OrderStatusRole"""
    return f"{prefix}-{_team_tag(team_num)}-OrderStatusRole"


def _gateway_target_name(prefix: str, team_num: int) -> str:
    """LabAgent-TeamXX-OrderStatusTarget"""
    return f"{prefix}-{_team_tag(team_num)}-OrderStatusTarget"


def _gateway_tool_name(prefix: str, team_num: int) -> str:
    """OrderStatus_TeamXX (Gateway tool names must match [a-zA-Z][a-zA-Z0-9_]{0,63} — no hyphens)"""
    return f"OrderStatus_{_team_tag(team_num)}"


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
    order_status_lambda_arn: str,
) -> dict:
    """
    Inline policy for the execution role — what the agent can do.

    Matches provisioning-student.md section 3.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                # Broad on purpose: this is a 4-hour lab sandbox with a handful
                # of model calls per team, and per-model/per-action scoping here
                # has repeatedly broken (wrong action name, wrong ARN, missing
                # bearer-token action) and cost days of debugging for no real
                # cost-control benefit. Trade narrow scoping for reliability.
                "Effect": "Allow",
                "Action": ["bedrock:*", "bedrock-agentcore:*"],
                "Resource": "*",
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
            {
                # The Gateway assumes this same role (GATEWAY_IAM_ROLE credential
                # provider) to invoke the read-only order-status tool target.
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": order_status_lambda_arn,
            },
        ],
    }


def lambda_trust_policy() -> dict:
    """Trust policy allowing the Lambda service to assume the tool's execution role."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
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
                # Broad on purpose: this is a 4-hour lab sandbox with a handful
                # of model calls per team, and per-model/per-action scoping here
                # has repeatedly broken (wrong action name, wrong ARN, missing
                # bearer-token action) and cost days of debugging for no real
                # cost-control benefit. Trade narrow scoping for reliability.
                "Effect": "Allow",
                "Action": ["bedrock:*", "bedrock-agentcore:*"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": execution_role_arn,
            },
            {
                # Lets students confirm the restricted policy is attached
                # (Lab 05 Checkpoint 1) without granting broader IAM read access.
                # AgentCoreExecutionPolicy is attached via put_role_policy (inline),
                # so students need the inline-policy read APIs, not
                # ListAttachedRolePolicies (which only sees managed policies and
                # would always return empty here).
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:ListRolePolicies",
                    "iam:GetRolePolicy",
                ],
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
            self.lambda_ = None
            self.account_id = "123456789012"
        else:
            self.iam = boto3.client("iam", region_name=region)
            self.s3 = boto3.client("s3", region_name=region)
            self.logs = boto3.client("logs", region_name=region)
            self.sts = boto3.client("sts", region_name=region)
            self.lambda_ = boto3.client("lambda", region_name=region)
            self.account_id = self.sts.get_caller_identity()["Account"]

            try:
                self.agentcore = boto3.client(
                    "bedrock-agentcore-control", region_name=region
                )
            except Exception:
                self.agentcore = None

        self._all_teams: list[dict] = []
        self._agent_zip_bytes: bytes | None = None

    # ------------------------------------------------------------------
    # S3 — one bucket per team
    # ------------------------------------------------------------------
    def _find_bucket(self, team_num: int) -> str | None:
        manifest_path = Path(OUTPUT_DIR) / STUDENT_MANIFEST_FILE
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            return None
        if manifest.get("region") != self.region:
            return None
        for team in manifest.get("teams", []):
            if team.get("team_num") != team_num:
                continue
            existing_bucket = team.get("bucket", "")
            if f"-{self.account_id}-" not in existing_bucket:
                continue
            try:
                self.s3.head_bucket(Bucket=existing_bucket)
                return existing_bucket
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                    raise
                return None
        return None

    def _create_bucket(self, team_num: int) -> str:
        if self.dry_run:
            bucket = _bucket_name(self.prefix, team_num, self.account_id)
            print(f"    [DRY RUN] Would create or reuse S3 bucket: {bucket}")
            return bucket

        existing_bucket = self._find_bucket(team_num)
        if existing_bucket:
            print(f"    S3 bucket already exists: {existing_bucket} (reusing)")
            return existing_bucket

        bucket = _bucket_name(self.prefix, team_num, self.account_id)

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

    def _build_agent_zip(self) -> bytes:
        """
        Build (once per run, then cache) the deployment ZIP: agent.py plus
        boto3/botocore vendored via `pip install --target`.

        AgentCore's direct-code Python runtime has no pre-installed packages —
        a ZIP containing only agent.py fails `import boto3` before the HTTP
        server binds, which surfaces as "Runtime initialization time exceeded"
        with no CloudWatch logs (the crash predates the logging pipeline).
        """
        if self._agent_zip_bytes is not None:
            return self._agent_zip_bytes

        with tempfile.TemporaryDirectory() as tmpdir:
            print("    Vendoring boto3/botocore for the agent package (pip install --target)...")
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "--target", tmpdir,
                    "--only-binary=:all:",
                    "--python-version", "3.12",
                    "--platform", "manylinux2014_aarch64",
                    "boto3",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            agent_path = Path(tmpdir) / "agent.py"
            agent_path.write_text(AGENT_SOURCE)

            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in Path(tmpdir).rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(tmpdir))

            self._agent_zip_bytes = buffer.getvalue()
            print(f"    Built agent package: {len(self._agent_zip_bytes) // 1024} KB")

        return self._agent_zip_bytes

    def _upload_agent(self, bucket: str, key_prefix: str) -> str:
        """
        Upload the agent deployment ZIP (agent.py + vendored boto3/botocore)
        as the direct-deployment artifact so deploy_agent.py (bedrock-companion)
        deploys a real governed agent — Gateway tool calls and Memory
        read/write — not a placeholder stub.
        """
        key = f"{key_prefix}/v1/deployment_package.zip"

        if self.dry_run:
            print(f"    [DRY RUN] Would upload s3://{bucket}/{key}")
            return key

        zip_bytes = self._build_agent_zip()
        self.s3.put_object(Bucket=bucket, Key=key, Body=zip_bytes)
        print(f"    Uploaded agent: s3://{bucket}/{key}")
        return key

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

    def _wait_for_gateway_ready(self, gateway_id: str) -> dict:
        deadline = time.monotonic() + GATEWAY_READY_TIMEOUT_SECONDS
        while True:
            gateway = self.agentcore.get_gateway(gatewayIdentifier=gateway_id)
            status = gateway["status"]
            if status == "READY":
                return gateway
            if status not in {"CREATING", "UPDATING"}:
                reasons = (
                    "; ".join(gateway.get("statusReasons", []))
                    or "no status reason returned"
                )
                raise RuntimeError(
                    f"Gateway {gateway_id} status is {status}: {reasons}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Gateway {gateway_id} status remained {status} after "
                    f"{GATEWAY_READY_TIMEOUT_SECONDS} seconds"
                )
            print(
                f"    Waiting for Gateway to leave {status} "
                f"({GATEWAY_POLL_INTERVAL_SECONDS}s)..."
            )
            time.sleep(GATEWAY_POLL_INTERVAL_SECONDS)

    def _create_gateway(self, team_num: int, role_arn: str) -> dict:
        """Returns dict with keys: name, arn, id, url."""
        gateway_name = _gateway_name(self.prefix, team_num)
        gateway_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":gateway/{gateway_name}"
        )

        if self.dry_run:
            print(f"    [DRY RUN] Would create Gateway: {gateway_name}")
            return {"name": gateway_name, "arn": gateway_arn, "id": "", "url": ""}

        if self.agentcore is None:
            print(
                f"    WARNING: SDK client unavailable — create Gateway manually:\n"
                f"      Name: {gateway_name}\n"
                f"      Protocol: MCP or HTTP (match lab agent)\n"
                f"      Target: read-only endpoint returning structured JSON\n"
                f"      Auth: IAM SigV4"
            )
            return {"name": gateway_name, "arn": gateway_arn, "id": "", "url": ""}

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

        gateway = self._wait_for_gateway_ready(gateway["gatewayId"])
        return {
            "name": gateway_name,
            "arn": gateway["gatewayArn"],
            "id": gateway["gatewayId"],
            "url": gateway.get("gatewayUrl", ""),
        }

    # ------------------------------------------------------------------
    # Gateway tool target — Lambda-backed read-only order-status tool
    # ------------------------------------------------------------------
    def _create_gateway_target_lambda_role(self, team_num: int) -> str:
        role_name = _gateway_target_lambda_role_name(self.prefix, team_num)
        role_arn = f"arn:aws:iam::{self.account_id}:role/{role_name}"

        if self.dry_run:
            print(f"    [DRY RUN] Would create Lambda role: {role_arn}")
            return role_arn

        try:
            resp = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(lambda_trust_policy()),
                Description=f"Order-status tool Lambda role for {_team_tag(team_num)} - Bedrock AgentCore course",
            )
            role_arn = resp["Role"]["Arn"]
            print(f"    Created Lambda role: {role_arn}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                print(f"    Lambda role already exists: {role_arn} (reusing)")
            else:
                raise

        self.iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        return role_arn

    def _create_order_status_lambda(self, team_num: int, lambda_role_arn: str) -> str:
        """
        Deploy the read-only order-status Lambda that backs the Gateway tool
        target. Returns synthetic {orderId, status} fixtures — matches
        check_gateway.py's ORDER_ID / EXPECTED_ORDER_STATUS defaults
        (ORD-1001 -> SHIPPED) and echoes back whatever orderId it is given.
        """
        function_name = _gateway_target_lambda_name(self.prefix, team_num)
        function_arn = (
            f"arn:aws:lambda:{self.region}:{self.account_id}:function:{function_name}"
        )

        if self.dry_run:
            print(f"    [DRY RUN] Would create Lambda: {function_name}")
            return function_arn

        source = textwrap.dedent("""\
        import json

        ORDER_STATUSES = {"ORD-1001": "SHIPPED"}
        DEFAULT_STATUS = "SHIPPED"


        def handler(event, context):
            order_id = event.get("orderId", "ORD-1001")
            status = ORDER_STATUSES.get(order_id, DEFAULT_STATUS)
            return {"orderId": order_id, "status": status}
        """)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("lambda_function.py", source)
        zip_bytes = buffer.getvalue()

        try:
            self.lambda_.get_function(FunctionName=function_name)
            self.lambda_.update_function_code(
                FunctionName=function_name, ZipFile=zip_bytes
            )
            print(f"    Updated Lambda: {function_name} (reusing)")
        except self.lambda_.exceptions.ResourceNotFoundException:
            waiter_needed = True
            for attempt in range(5):
                try:
                    resp = self.lambda_.create_function(
                        FunctionName=function_name,
                        Runtime="python3.12",
                        Role=lambda_role_arn,
                        Handler="lambda_function.handler",
                        Code={"ZipFile": zip_bytes},
                        Description=f"Read-only order-status tool for {_team_tag(team_num)} - Bedrock AgentCore course",
                        Timeout=10,
                    )
                    function_arn = resp["FunctionArn"]
                    print(f"    Created Lambda: {function_name}")
                    waiter_needed = False
                    break
                except ClientError as e:
                    # IAM role propagation lag — retry create_function briefly.
                    if e.response["Error"]["Code"] == "InvalidParameterValueException" and attempt < 4:
                        time.sleep(3)
                        continue
                    raise
            if waiter_needed:
                raise RuntimeError(f"Failed to create Lambda: {function_name}")

        self.lambda_.get_waiter("function_active_v2").wait(FunctionName=function_name)
        return function_arn

    def _allow_gateway_to_invoke_lambda(self, team_num: int, lambda_arn: str) -> None:
        """Lambda resource policy: let the team's execution role invoke this function."""
        function_name = _gateway_target_lambda_name(self.prefix, team_num)
        statement_id = "AllowGatewayExecutionRole"

        if self.dry_run:
            return

        try:
            self.lambda_.add_permission(
                FunctionName=function_name,
                StatementId=statement_id,
                Action="lambda:InvokeFunction",
                Principal="bedrock-agentcore.amazonaws.com",
                SourceAccount=self.account_id,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceConflictException":
                raise

    def _create_gateway_target(
        self, team_num: int, gateway_id: str, lambda_arn: str
    ) -> str:
        """
        Wire the order-status Lambda into the Gateway as an MCP tool target.

        Returns the fully-qualified tool name (GATEWAY_TOOL_NAME for the lab).
        AgentCore Gateway namespaces every Lambda-target tool as
        "{targetName}___{toolName}" in tools/list and tools/call — the bare
        name registered in toolSchema.inlinePayload is never callable on its
        own, so callers must use the qualified form.
        """
        target_name = _gateway_target_name(self.prefix, team_num)
        tool_name = _gateway_tool_name(self.prefix, team_num)
        qualified_tool_name = f"{target_name}___{tool_name}"

        if self.dry_run:
            print(f"    [DRY RUN] Would create Gateway target: {target_name}")
            return qualified_tool_name

        if self.agentcore is None:
            print(
                f"    WARNING: SDK client unavailable — create Gateway target manually:\n"
                f"      Name: {target_name}\n"
                f"      Tool: {tool_name}\n"
                f"      Backing Lambda: {lambda_arn}"
            )
            return qualified_tool_name

        existing = self.agentcore.list_gateway_targets(gatewayIdentifier=gateway_id)
        for item in existing.get("items", []):
            if item.get("name") == target_name:
                print(f"    Gateway target already exists: {target_name} (reusing)")
                return qualified_tool_name

        tool_schema = [
            {
                "name": tool_name,
                "description": "Look up the status of a retail order (read-only, synthetic fixture data).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "orderId": {
                            "type": "string",
                            "description": "The order identifier to look up, e.g. ORD-1001",
                        }
                    },
                    "required": ["orderId"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "orderId": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            }
        ]

        for attempt in range(GATEWAY_TARGET_PERMISSION_RETRIES):
            try:
                self.agentcore.create_gateway_target(
                    gatewayIdentifier=gateway_id,
                    name=target_name,
                    description=f"Order-status tool target for {_team_tag(team_num)} - Bedrock AgentCore course",
                    targetConfiguration={
                        "mcp": {
                            "lambda": {
                                "lambdaArn": lambda_arn,
                                "toolSchema": {"inlinePayload": tool_schema},
                            }
                        }
                    },
                    credentialProviderConfigurations=[
                        {"credentialProviderType": "GATEWAY_IAM_ROLE"}
                    ],
                )
                print(f"    Created Gateway target: {target_name}")
                return qualified_tool_name
            except ClientError as e:
                error = e.response["Error"]
                if error["Code"] == "ConflictException":
                    print(
                        f"    Gateway target already exists: {target_name} (reusing)"
                    )
                    return qualified_tool_name
                permission_pending = (
                    error["Code"] == "ValidationException"
                    and "lacks permission to invoke Lambda" in error.get("Message", "")
                )
                retries_exhausted = (
                    attempt == GATEWAY_TARGET_PERMISSION_RETRIES - 1
                )
                if not permission_pending or retries_exhausted:
                    raise
                print(
                    "    Waiting for execution role permission propagation "
                    f"({GATEWAY_TARGET_PERMISSION_RETRY_SECONDS}s)..."
                )
                time.sleep(GATEWAY_TARGET_PERMISSION_RETRY_SECONDS)

        raise RuntimeError(f"Failed to create Gateway target: {target_name}")

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

    def _create_memory(self, team_num: int) -> tuple[str, str, str]:
        """Returns (memory_name, memory_arn, memory_id)."""
        memory_name = _memory_name(self.prefix, team_num)
        memory_arn = (
            f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}"
            f":memory/{memory_name}"
        )

        if self.dry_run:
            print(f"    [DRY RUN] Would create Memory: {memory_name}")
            return memory_name, memory_arn, ""

        if self.agentcore is None:
            print(
                f"    WARNING: SDK client unavailable — create Memory manually:\n"
                f"      Name: {memory_name}\n"
                f"      Type: Short-term (session-scoped)"
            )
            return memory_name, memory_arn, ""

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

        return memory_name, memory["arn"], memory["id"]

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
        order_status_lambda_arn: str,
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
            order_status_lambda_arn=order_status_lambda_arn,
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

        # Generate access keys — rotate on rerun since a prior run's secret
        # key cannot be retrieved, and IAM caps users at 2 access keys.
        existing_keys = self.iam.list_access_keys(UserName=user_name)["AccessKeyMetadata"]
        for key in existing_keys:
            self.iam.delete_access_key(UserName=user_name, AccessKeyId=key["AccessKeyId"])
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
        print(f"  [1/9] S3 bucket...")
        bucket = self._create_bucket(team_num)
        bucket_arn = f"arn:aws:s3:::{bucket}"

        # 2. CloudWatch log group
        print(f"  [2/9] CloudWatch log group...")
        log_group = self._create_log_group(team_num)

        # 3. IAM execution role (Gateway creation needs its ARN)
        print(f"  [3/9] IAM execution role...")
        execution_role_arn = self._create_execution_role(team_num)

        if not self.dry_run:
            print("    Waiting for IAM propagation (2s)...")
            time.sleep(2)

        # 4. Order-status Lambda (backs the Gateway tool target)
        print(f"  [4/9] Order-status Lambda...")
        lambda_role_arn = self._create_gateway_target_lambda_role(team_num)
        if not self.dry_run:
            print("    Waiting for IAM propagation (5s)...")
            time.sleep(5)
        order_status_lambda_arn = self._create_order_status_lambda(team_num, lambda_role_arn)
        self._allow_gateway_to_invoke_lambda(team_num, order_status_lambda_arn)

        # 5. AgentCore Gateway
        print(f"  [5/9] AgentCore Gateway...")
        gateway = self._create_gateway(team_num, execution_role_arn)

        # 6. AgentCore Memory
        print(f"  [6/9] AgentCore Memory...")
        memory_name, memory_arn, memory_id = self._create_memory(team_num)

        self._attach_execution_policy(
            team_num=team_num,
            gateway_arn=gateway["arn"],
            memory_arn=memory_arn,
            log_group=log_group,
            order_status_lambda_arn=order_status_lambda_arn,
        )

        # 7. Gateway tool target
        print(f"  [7/9] Gateway tool target...")
        gateway_tool_name = self._create_gateway_target(
            team_num, gateway["id"], order_status_lambda_arn
        )

        # 8. IAM sandbox user
        print(f"  [8/9] IAM sandbox user...")
        team_info = self._create_sandbox_user(
            team_num=team_num,
            execution_role_arn=execution_role_arn,
            bucket_arn=bucket_arn,
            gateway_arn=gateway["arn"],
            memory_arn=memory_arn,
            log_group=log_group,
        )

        # 9. Governed agent artifact
        print(f"  [9/9] Governed agent artifact...")
        agent_name = _agent_name(self.prefix, team_num)
        s3_key = self._upload_agent(bucket, team_info["user_name"])

        # Enrich team_info with resource details
        team_info.update({
            "execution_role_arn": execution_role_arn,
            "execution_role_name": _execution_role_name(self.prefix, team_num),
            "bucket": bucket,
            "bucket_arn": bucket_arn,
            "gateway_name": gateway["name"],
            "gateway_arn": gateway["arn"],
            "gateway_url": gateway["url"],
            "gateway_tool_name": gateway_tool_name,
            "memory_name": memory_name,
            "memory_arn": memory_arn,
            "memory_id": memory_id,
            "log_group": log_group,
            "agent_name": agent_name,
            "s3_key": s3_key,
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
            export S3_KEY={t['s3_key']}
            export GATEWAY_URL={t['gateway_url']}
            export GATEWAY_TOOL_NAME={t['gateway_tool_name']}
            export MEMORY_ID={t['memory_id']}
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
                    "S3 Key",
                    "Gateway URL",
                    "Gateway Tool Name",
                    "Memory ID",
                ])
                writer.writerow([
                    t["access_key"],
                    t["secret_key"],
                    t.get("signin_url", ""),
                    t["user_name"],
                    t.get("console_password", ""),
                    t["s3_key"],
                    t["gateway_url"],
                    t["gateway_tool_name"],
                    t["memory_id"],
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
                "S3 Key",
                "Gateway URL",
                "Gateway Tool Name",
                "Memory ID",
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
                    t["s3_key"],
                    t["gateway_url"],
                    t["gateway_tool_name"],
                    t["memory_id"],
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
                    "s3_key": t["s3_key"],
                    "gateway_name": t["gateway_name"],
                    "gateway_target_name": _gateway_target_name(self.prefix, t["team_num"]),
                    "memory_name": t["memory_name"],
                    "log_group": t["log_group"],
                    "order_status_lambda_name": _gateway_target_lambda_name(self.prefix, t["team_num"]),
                    "order_status_lambda_role_name": _gateway_target_lambda_role_name(self.prefix, t["team_num"]),
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
  S3_KEY                  Direct-deployment ZIP object for deploy_agent.py
  GATEWAY_URL             MCP endpoint for the Gateway tool target
  GATEWAY_TOOL_NAME       Qualified tool name for check_gateway.py
  MEMORY_ID               AgentCore Memory resource ID for verify_memory.py

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
