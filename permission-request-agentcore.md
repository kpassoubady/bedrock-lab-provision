# Permission Fix Request — Instructor Bedrock + AgentCore Access

**Account:** 962804699607
**Region:** us-east-1
**Affected identity:** `arn:aws:iam::962804699607:user/instructor-01`

## Problem

The instructor IAM user (`instructor-01`) cannot use Amazon Bedrock (chat, API keys) or create/manage AgentCore resources. Deploying the instructor demo Runtime fails with:

```
AccessDeniedException: User: arn:aws:iam::962804699607:user/instructor-01 is not
authorized to perform: bedrock-agentcore:CreateAgentRuntime on resource:
arn:aws:bedrock-agentcore:us-east-1:962804699607:runtime/* because no
identity-based policy allows the bedrock-agentcore:CreateAgentRuntime action
```

and Bedrock playground/chat and short-term API key (bearer-token) calls are separately denied.

`instructor-01` also cannot self-remediate — it lacks `iam:PutUserPolicy`, so it cannot attach a policy to itself. This needs an admin with IAM write access on the account.

This is a 4-hour training account with negligible call volume. Rather than chase individually-missing actions one at a time (we've lost multiple days to exactly that), this request grants broad Bedrock + AgentCore access in one shot, and does not depend on any other managed policy being separately attached correctly.

## Ask

Attach the following inline policy to IAM user `instructor-01` (policy name: `AgentCoreInstructorPolicy`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock:*", "bedrock-agentcore:*"],
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

`instructor-01` was originally supposed to receive `IAMFullAccess`, `AmazonS3FullAccess`, `CloudWatchFullAccess`, `AmazonBedrockFullAccess`, plus a narrower AgentCore-only inline policy, per an earlier draft of our provisioning spec (`provisioning-instructor.md`, section 2). In practice that split across several policies has been unreliable to verify and slow to fix piecemeal (missing actions, wrong action namespace, a missing bearer-token action for API-key auth). This single inline policy replaces the AgentCore-only one and is broad enough that it doesn't matter whether the managed policies were attached correctly — attaching just this unblocks everything.

## Verification

Once attached, we'll confirm from our side by:

- Re-running `python3 deploy_agent.py` from the course lab, expecting it to proceed past `CreateAgentRuntime` without an `AccessDeniedException`.
- Generating a short-term Bedrock API key as `instructor-01` and running `bedrock_first_request.py` against it, expecting a successful chat completion.
