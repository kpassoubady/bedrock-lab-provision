# Permission Fix Request — Instructor AgentCore Access

**Account:** 962804699607
**Region:** us-west-2
**Affected identity:** `arn:aws:iam::962804699607:user/instructor-01`

## Problem

The instructor IAM user (`instructor-01`) cannot create or manage Amazon Bedrock AgentCore resources. Deploying the instructor demo Runtime fails with:

```
AccessDeniedException: User: arn:aws:iam::962804699607:user/instructor-01 is not
authorized to perform: bedrock-agentcore:CreateAgentRuntime on resource:
arn:aws:bedrock-agentcore:us-west-2:962804699607:runtime/* because no
identity-based policy allows the bedrock-agentcore:CreateAgentRuntime action
```

`instructor-01` also cannot self-remediate — it lacks `iam:PutUserPolicy`, so it cannot attach a policy to itself. This needs an admin with IAM write access on the account.

## Ask

Attach the following inline policy to IAM user `instructor-01` (policy name: `AgentCoreInstructorPolicy`):

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
      "Action": ["sts:AssumeRole"],
      "Resource": "arn:aws:iam::*:role/LabAgent-*"
    }
  ]
}
```

Equivalent CLI command, for whoever has IAM admin rights on this account:

```bash
aws iam put-user-policy \
  --user-name instructor-01 \
  --policy-name AgentCoreInstructorPolicy \
  --policy-document file://env/agentcore-inline-policy.json
```

(The `agentcore-inline-policy.json` file with this exact content is attached / included in the provisioning package under `env/agentcore-inline-policy.json`.)

## Context

`instructor-01` was originally supposed to receive `IAMFullAccess`, `AmazonS3FullAccess`, `CloudWatchFullAccess`, `AmazonBedrockFullAccess`, plus this AgentCore inline policy, per our provisioning spec (`provisioning-instructor.md`, section 2). The managed policies appear to be in place; only this inline policy is missing or was attached with incorrect action names (an earlier draft of our doc mistakenly used a `bedrock:` prefix instead of `bedrock-agentcore:` — that's been corrected on our end).

## Verification

Once attached, we'll confirm from our side by re-running:

```bash
python3 deploy_agent.py
```

from the course lab, and expect it to proceed past `CreateAgentRuntime` without an `AccessDeniedException`.
