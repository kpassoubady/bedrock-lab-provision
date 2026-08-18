# Instructor Environment Provisioning — Bedrock AgentCore

This document tells the company IT team what to provision for the instructor before the course. The instructor needs an AWS environment with elevated permissions to create and manage student sandboxes, plus their own working demo resources.

## Provisioning Checklist

- [ ] Instructor IAM role or user with the permissions listed below
- [ ] Bedrock model access enabled in the course region
- [ ] Instructor demo sandbox with a known-good Runtime endpoint, Gateway, Memory resource, and CloudWatch log group
- [ ] Ability to create and tear down student sandboxes (IAM users, roles, S3 buckets, and AgentCore resources)
- [ ] Access to view all student sandbox resources for troubleshooting during class

## 1. AWS Account and Region

Use a dedicated AWS account for the course, or a sandbox OU within the company organization. All resources live in a single region — the catalog assumes `us-east-1`. Confirm the following service quotas before class:

| Service | Quota to check |
|---------|---------------|
| Amazon Bedrock | Model invocation rate for the selected model |
| AgentCore Runtime | Maximum concurrent sessions |
| IAM | Number of users and roles (one per student or team) |
| S3 | Number of buckets |
| CloudWatch Logs | Log group and stream limits |

## 2. Instructor IAM Permissions

Create an IAM user or role named `bedrock-instructor` with the following permissions. The instructor needs more than a student — they create and tear down resources, view all student sandboxes, and troubleshoot during class.

### Managed Policies to Attach

| AWS Managed Policy | Purpose |
|--------------------|---------|
| `IAMFullAccess` | Create and manage student IAM users, roles, and policies |
| `AmazonS3FullAccess` | Create and manage student S3 buckets |
| `CloudWatchFullAccess` | Create log groups and view student telemetry |
| `AmazonBedrockFullAccess` | Invoke models, create AgentCore resources |

### Additional Inline Policy

Attach this inline policy to cover AgentCore-specific actions not included in the managed policies above. It's tracked at `env/agentcore-inline-policy.json` and referenced by `permission-request-agentcore.md` — keep both in sync with this block.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateAgentRuntime",
        "bedrock-agentcore:UpdateAgentRuntime",
        "bedrock-agentcore:DeleteAgentRuntime",
        "bedrock-agentcore:GetAgentRuntime",
        "bedrock-agentcore:ListAgentRuntimes",
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:InvokeAgentRuntimeForUser",
        "bedrock-agentcore:CreateGateway",
        "bedrock-agentcore:DeleteGateway",
        "bedrock-agentcore:GetGateway",
        "bedrock-agentcore:ListGateways",
        "bedrock-agentcore:InvokeGateway",
        "bedrock-agentcore:CreateMemory",
        "bedrock-agentcore:DeleteMemory",
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:ListMemories",
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListEvents"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sts:AssumeRole"
      ],
      "Resource": "arn:aws:iam::*:role/LabAgent-*"
    }
  ]
}
```

The action prefix is `bedrock-agentcore:`, not `bedrock:` — AgentCore is a separate service namespace. An earlier draft of this document used the `bedrock:` prefix; that version doesn't grant anything, since none of those actions exist.

## 3. Instructor Demo Sandbox

Provision a working demo sandbox for the instructor. This is separate from student sandboxes and serves as the known-good reference during class.

### Resources to Create

| Resource | Name / Pattern | Notes |
|----------|---------------|-------|
| S3 bucket | `bedrock-instructor-demo-<account-id>` | Stores the demo agent code artifact |
| Execution role | `LabAgent-Instructor-ExecutionRole` | Shared by AgentCore Runtime and Gateway to match the provisioner's scoped IAM permissions |
| AgentCore Gateway | `InstructorDemoGateway` | IAM-authorized MCP Gateway; configure its tool target separately |
| AgentCore Memory | `InstructorDemoMemory` | Short-term memory resource for the demo agent |
| CloudWatch log group | `/aws/bedrock/agentcore/instructor-demo` | Captures Runtime logs and traces |
| Runtime endpoint | Deploy the demo agent via the course provisioning script | Known-good endpoint for fallback during class |

### Execution Role Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": "<account-id>"
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:bedrock-agentcore:<region>:<account-id>:*"
        }
      }
    }
  ]
}
```

### Verification

After provisioning, confirm the instructor demo sandbox works:

1. Invoke the Bedrock model from the instructor role.
2. Deploy the demo agent to the Runtime endpoint.
3. Invoke the Gateway tool and verify the expected response.
4. Confirm Memory continuity across two requests with the same `actorId` and `sessionId`.
5. Open the CloudWatch log group and verify traces appear.
6. Export the trace JSON and screenshots as fallback evidence.

## 4. Student Sandbox Management

The instructor must be able to create and tear down student sandboxes. The course provisioning script in the `bedrock-setup` repository automates this.

### What the Script Creates Per Team

| Resource | Purpose |
|----------|---------|
| IAM user | Sandbox identity for the student or team |
| IAM execution role | Role AgentCore Runtime assumes when running the student agent |
| S3 bucket | Stores the student agent code artifact |
| AgentCore Gateway | Pre-created gateway exposing the read-only tool target |
| AgentCore Memory | Pre-created short-term memory resource |
| CloudWatch log group | Captures student Runtime logs and traces |
| `.env` file | Credentials and resource ARNs for distribution |

Run from the `bedrock-setup` repository:

```bash
pip3 install boto3
python3 scripts/provision.py --teams 10 --prefix LabAgent --region us-east-1
python3 scripts/provision.py --teams 5 --dry-run   # preview without AWS calls
```

After class:

```bash
python3 scripts/teardown.py
```

### Student Credential Distribution

Each team receives an `.env` file from `bedrock-setup/env/team-XX.env` containing:

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
EXECUTION_ROLE_ARN=arn:aws:iam::...
S3_BUCKET=...
TEAM_PREFIX=LabAgent-TeamXX
```

Distribute these securely before class. Do not email credentials in plain text.

## 5. Pre-Class Verification (Per Cohort)

The instructor or IT team must complete these checks before each cohort:

1. Confirm every student sandbox can invoke the selected Bedrock model.
2. Confirm the Gateway target returns the expected synthetic response from each sandbox.
3. Confirm Runtime deployment, Memory access, and CloudWatch trace viewing work with the student role.
4. Confirm each team has a unique resource prefix with no collisions.
5. Run the deployment script and checkpoint commands from a clean environment.
6. Confirm the instructor's fallback endpoint, trace JSON, and screenshots match the current lab version.

