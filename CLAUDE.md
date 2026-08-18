# Project Overview
This project provides lab environment provisioning for "Building Production-Ready Agents with Amazon Bedrock AgentCore".

## Directory Layout
- `llm-context/`: Stores context for LLM agents.
- `docs/`: Supplementary docs, e.g. `credential-distribution.md`.
- `env/`: Output directory for generated environment files and manifests. Tracked files: `agentcore-inline-policy.json`. Runtime artifacts (`*.env`, `*.csv`, `*manifest*.json`, `handout-log.json`) are ignored.
- `provision-instructor.py`: Provisions the instructor demo sandbox.
- `provision-students.py`: Provisions per-team student sandboxes.
- `validate-instructor.py`: Validates provisioned instructor resources.
- `validate-students.py`: Validates provisioned per-team student resources.
- `distribute-credentials.py`: Ships each team's credential file via a short-lived presigned S3 URL (never via git or plaintext chat).
- `teardown.py`: Tears down all resources using manifest files.
- `instructor-provision.sh`: Shell script to install dependencies and provision the instructor environment.
- `provisioning-instructor.md`: Full specification for the instructor environment.
- `provisioning-student.md`: Full specification for student sandboxes.
- `permission-request-agentcore.md`: Template for requesting the AgentCore inline policy.
- `pm-provisioning-request.md`: Provisioning request for project management.
- `requirements.txt`: Python dependencies.

## Verification Steps
TBD

## Content Guidelines
TBD

## Related Repositories
- [bedrock](https://github.com/kpassoubady/bedrock) — Core courseware and instructor materials.
- [bedrock-companion](https://github.com/kpassoubady/bedrock-companion) — Hands-on lab exercises and student materials.
- [bedrock-setup](https://github.com/kpassoubady/bedrock-setup) — Pre-class environment verification and installation steps.
