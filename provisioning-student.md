# Student Sandbox Provisioning — Bedrock AgentCore

This document tells the company IT team what to provision for each student or team before the course. Students receive a scoped sandbox with pre-created resources. They deploy and invoke an agent — they do not create infrastructure.

## Provisioning Checklist (Per Team)

- [ ] IAM user with the scoped policy below
- [ ] Console login profile for the IAM user (browser sign-in for trace inspection)
- [ ] IAM execution role with trust policy for AgentCore Runtime
- [ ] S3 bucket for the agent code artifact
- [ ] AgentCore Gateway exposing a deterministic read-only tool target
- [ ] AgentCore Memory resource for short-term memory
- [ ] CloudWatch log group for Runtime logs and traces
- [ ] Unique resource prefix to prevent name collisions across teams
- [ ] Bedrock model access in the course region
- [ ] `.env` file with credentials and resource ARNs for distribution

## 1. Resource Naming Convention

Use a consistent prefix scheme to isolate teams within the same account. The catalog recommends `LabAgent-TeamXX`.

| Resource | Name Pattern | Example (Team 01) |
|----------|-------------|--------------------|
| IAM user | `LabAgent-TeamXX` | `LabAgent-Team01` |
| Execution role | `LabAgent-TeamXX-ExecutionRole` | `LabAgent-Team01-ExecutionRole` |
| S3 bucket | `labagent-teamXX-<account-id>` | `labagent-team01-123456789012` |
| AgentCore Gateway | `LabAgent-TeamXX-Gateway` | `LabAgent-Team01-Gateway` |
| AgentCore Memory | `LabAgent-TeamXX-Memory` | `LabAgent-Team01-Memory` |
| CloudWatch log group | `/aws/bedrock/agentcore/teamXX` | `/aws/bedrock/agentcore/team01` |

## 2. IAM User (Sandbox Identity)

Create one IAM user per student or team. Attach only the scoped policy below — no managed policies.

### Scoped Student Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeAgentRuntime",
        "bedrock:GetAgentRuntime"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:<account-id>:agent-runtime/LabAgent-TeamXX-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeAgentGateway"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:<account-id>:agent-gateway/LabAgent-TeamXX-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:GetAgentMemory",
        "bedrock:UpdateAgentMemory"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:<account-id>:agent-memory/LabAgent-TeamXX-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::<account-id>:role/LabAgent-TeamXX-ExecutionRole"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::labagent-teamXX-<account-id>",
        "arn:aws:s3:::labagent-teamXX-<account-id>/*"
      ]
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
        "logs:GetLogRecord"
      ],
      "Resource": "arn:aws:logs:us-east-1:<account-id>:log-group:/aws/bedrock/agentcore/teamXX:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:StopQuery",
        "logs:DescribeQueries",
        "cloudwatch:GetMetricData",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricStatistics",
        "xray:GetTraceSummaries",
        "xray:BatchGetTraces",
        "xray:GetTraceGraph",
        "xray:GetServiceGraph",
        "xray:GetGroups",
        "xray:GetGroup",
        "xray:GetInsightSummaries"
      ],
      "Resource": "*"
    }
  ]
}
```

Replace `<account-id>` and `TeamXX` with the actual values for each team.

The X-Ray, `logs:StartQuery`/`GetQueryResults`, and expanded CloudWatch read actions let students open the **CloudWatch → GenAI Observability → AgentCore → Traces** view in Checkpoint 5. These read APIs do not support resource-level scoping, so their statement uses `Resource: "*"`; students still see only traces their own Runtime emits.

### Console Login Profile

The scoped access keys above cover the CLI checkpoints, but lab Checkpoint 5 asks students to inspect the end-to-end trace in the **AWS Console**, which needs browser sign-in. Create a console login profile for each IAM user in addition to the access keys.

- **Sign-in URL:** `https://<account-id>.signin.aws.amazon.com/console`
- **Username:** the IAM user name (`LabAgent-TeamXX`)
- **Password:** a unique, randomly generated password per user
- **Reset on first sign-in:** optional; leave off for a smoother short session

`provision-students.py` creates this automatically. Disable it with `--no-console-access` (students then rely on the instructor's fallback trace evidence for Checkpoint 5), or require a first-login reset with `--force-password-reset`.

## 3. Execution Role (for AgentCore Runtime)

Create an IAM role that AgentCore Runtime assumes when running the student agent. The role needs permissions to invoke the Bedrock model, call the Gateway, and write logs.

### Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### Execution Role Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeAgentGateway"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:<account-id>:agent-gateway/LabAgent-TeamXX-Gateway"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:GetAgentMemory",
        "bedrock:UpdateAgentMemory"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:<account-id>:agent-memory/LabAgent-TeamXX-Memory"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:<account-id>:log-group:/aws/bedrock/agentcore/teamXX:*"
    }
  ]
}
```

## 4. S3 Bucket

Create one bucket per team to store the agent code artifact. The student uploads their agent package here before deploying to Runtime.

- **Bucket name:** `labagent-teamXX-<account-id>`
- **Region:** `us-east-1`
- **Block public access:** enabled (all four settings)
- **Encryption:** SSE-S3 (default)

## 5. AgentCore Gateway

Pre-create one Gateway per team. It exposes a deterministic read-only tool target that returns a structured synthetic response. This is what the agent calls during the lab.

- **Gateway name:** `LabAgent-TeamXX-Gateway`
- **Protocol:** MCP or HTTP (match the lab agent implementation)
- **Tool target:** A read-only endpoint that returns a predictable JSON response
- **Authentication:** IAM SigV4 (the execution role authorizes the call)

The Gateway target must be operational and returning the expected response before class. The instructor verifies this as part of the pre-class checklist.

## 6. AgentCore Memory

Pre-create one Memory resource per team. The lab exercise verifies short-term memory continuity by repeating a request with the same `actorId` and `sessionId`.

- **Memory name:** `LabAgent-TeamXX-Memory`
- **Type:** Short-term (session-scoped)
- **Retention:** Default (duration of the class session is sufficient)

## 7. CloudWatch Log Group

Pre-create one log group per team. The Runtime writes logs and traces here.

- **Log group name:** `/aws/bedrock/agentcore/teamXX`
- **Retention:** 7 days (covers the class plus a review window)

## 8. Bedrock Model Access

Enable access to the selected foundation model for the course region (`us-east-1`). The catalog does not specify a particular model — confirm with the instructor which model the lab agent uses. Common choices:

- Anthropic Claude (Sonnet or Haiku)
- Amazon Nova

Request model access through the AWS Console: **Bedrock → Model access → Manage model access**.

## 9. Credential Distribution

Generate access keys for each IAM user and package them in an `.env` file. The provisioning script in the `bedrock-setup` repository automates this.

Each team receives:

```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
EXECUTION_ROLE_ARN=arn:aws:iam::<account-id>:role/LabAgent-TeamXX-ExecutionRole
S3_BUCKET=labagent-teamXX-<account-id>
TEAM_PREFIX=LabAgent-TeamXX
```

Plus their **console sign-in details** (URL, username, password) for the Checkpoint 5 trace inspection. `provision-students.py` appends these as comments at the end of each `team-XX.env` file and includes them in `team-XX-credentials.csv`.

Distribute these securely before class. Do not email credentials in plain text. Use a one-time secret service or the company's internal credential vault.

## 10. Provisioning at Scale

For multiple teams, use the `provision-students.py` script provided in this directory instead of creating each sandbox manually through the console.

```bash
pip3 install boto3

# Preview the resources that will be created (no AWS calls)
python3 provision-students.py --teams 5 --dry-run

# Provision sandboxes for 10 teams
python3 provision-students.py --teams 10 --prefix LabAgent --region us-east-1
```

The script creates all resources listed above and writes `.env` files to the `env/` subdirectory.

After class, tear down all student resources using the generated `env/student-manifest.json`.

## 11. Verification Per Team

After provisioning, verify each sandbox:

1. Run `aws sts get-caller-identity` with the team credentials — confirm the correct IAM user.
2. Invoke the Bedrock model — confirm the response.
3. Call the Gateway target — confirm the expected synthetic response.
4. Upload a test artifact to the S3 bucket — confirm access.
5. Deploy a test agent to Runtime — confirm the execution role works.
6. Query the CloudWatch log group — confirm logs and traces appear.
7. Sign in to the AWS Console with the team's console credentials and open **CloudWatch → GenAI Observability → AgentCore → Traces** — confirm the trace view loads (Checkpoint 5).
