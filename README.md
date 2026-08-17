# Bedrock AgentCore — Provisioning Scripts (Vendor)

These scripts provision the AWS environment for the "Building Production-Ready Agents with Amazon Bedrock AgentCore" course. They are intended for the external vendor or IT team that sets up the environment before handing it over to the instructor.

## What You Are Provisioning

Two separate environments:

| Environment           | Script                    | What it creates                                                                                                                               |
| --------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **Instructor demo**   | `provision-instructor.py` | S3 bucket, IAM execution role, AgentCore Gateway, AgentCore Memory, CloudWatch log group — the instructor's known-good reference during class |
| **Student sandboxes** | `provision-students.py`   | Per-team: IAM user, IAM execution role, S3 bucket, AgentCore Gateway, AgentCore Memory, CloudWatch log group, access keys, `.env` files       |

The full specifications are in this directory:

- [provisioning-instructor.md](./provisioning-instructor.md)
- [provisioning-student.md](./provisioning-student.md)

## Installation (macOS / Linux)

Everything you need to install on your local machine before running the scripts.

### 1. Python 3.10+

```bash
python3 --version   # must be 3.10 or later
```

If you don't have it, install via [pyenv](https://github.com/pyenv/pyenv) (recommended) or [Homebrew](https://brew.sh):

```bash
# Option A: pyenv
brew install pyenv
pyenv install 3.12
pyenv local 3.12

# Option B: Homebrew directly
brew install python@3.12
```

### 2. AWS CLI

The verification steps use the `aws` command. Install it:

```bash
brew install awscli
```

Verify:

```bash
aws --version
```

### 3. Python Virtual Environment

```bash
cd bedrock-lab-provision
python3 -m venv .venv
source .venv/bin/activate
```

If you are using fish shell:

```bash
python3 --version
source .venv/bin/activate.fish
```


Your prompt should show `(.venv)`. To leave later: `deactivate`

### 4. Python Dependencies (boto3)

```bash
pip install -r requirements.txt
```

Verify:

```bash
python3 -c "import boto3; print(boto3.__version__)"
```

### 5. AWS Credentials (Programmatic Access)

You received an IAM user with **console access** (username/password from `logistics/amazon.md`). The provisioning scripts need **programmatic access** (access key + secret key). Create access keys for your IAM user:

1. Log in to the AWS Console at the URL in `logistics/amazon.md`
2. Go to **IAM → Users → your-username → Security credentials**
3. Click **Create access key** → choose **Command Line Interface (CLI)**
4. Download or copy the **Access Key ID** and **Secret Access Key**

Set up **two** named profiles — one for the instructor demo, one for student sandboxes. Run `aws configure` twice:

```bash
aws configure --profile bedrock-course-instructor
```

When prompted:
- **AWS Access Key ID**: paste the key used for the instructor account/role
- **AWS Secret Access Key**: paste the matching secret
- **Default region name**: `us-west-2`
- **Default output format**: `json`

```bash
aws configure --profile bedrock-course-student
```

When prompted, use the key/secret for the student provisioning account/role, with the same region (`us-west-2`) and output format (`json`).

Export the profile that matches the step you're running:

```bash
# Run the instructor steps first
export AWS_PROFILE=bedrock-course-instructor

# Then switch for the student steps
export AWS_PROFILE=bedrock-course-student
```

Alternatively, use environment variables directly:

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-west-2
```

### 6. Bedrock Model Access

Enable model access in the target region before provisioning:

1. Log in to the AWS Console
2. Go to **Bedrock → Model access** (in `us-west-2` region)
3. Enable the models the course uses (check with the instructor)

> **Target region**: This provisioning targets `us-west-2`. See `logistics/amazon.md` for console login details.

## Step-by-Step Provisioning Order

Provision in this order. The instructor demo should be created first so it can serve as a reference while student sandboxes are being verified.

### 1. Dry-run both scripts (no AWS calls)

```bash
python3 provision-instructor.py --region us-west-2 --dry-run
python3 provision-students.py --teams 2 --region us-west-2 --dry-run
```

Review the output to confirm the naming and resource counts match your expectations.

### 2. Provision the instructor demo

```bash
export AWS_PROFILE=bedrock-course-instructor
python3 provision-instructor.py --region us-west-2
```

This creates the instructor's working demo sandbox and writes:

- `env/instructor.env` — environment variables for the instructor
- `env/instructor-manifest.json` — resource manifest for teardown

Rerunning the command reuses the S3 bucket, Gateway, Memory, and IAM role recorded for the same account and region.

### 3. Validate the instructor demo

Run the automated validator with the same `bedrock-course-instructor` profile used for provisioning:

```bash
export AWS_PROFILE=bedrock-course-instructor
python3 validate-instructor.py --manifest env/instructor-manifest.json --region us-west-2
```

The validator checks the AWS account, S3 bucket security and placeholder artifact, IAM execution role and inline policy, Gateway `READY` status, Memory `ACTIVE` status, and CloudWatch log group retention. It exits with status `1` if any required check fails, making it suitable for a shell script or CI job.

Optionally include a live model invocation:

```bash
python3 validate-instructor.py --manifest env/instructor-manifest.json --region us-west-2 \
  --model-id <model-id>
```

After deploying the real demo agent, separately verify Runtime invocation, Memory continuity across two requests, and CloudWatch trace events because those end-to-end checks depend on the course deployment artifact.

### 4. Provision student sandboxes

```bash
export AWS_PROFILE=bedrock-course-student
python3 provision-students.py --teams 2 --prefix LabAgent --region us-west-2
```

This creates one isolated sandbox per team and writes:

- `env/credentials.csv` — master sheet (all teams, keep secure)
- `env/team-XX.env` — per-team environment variables for distribution
- `env/team-XX-credentials.csv` — per-team AWS credentials
- `env/student-manifest.json` — resource manifest for teardown

**For late additions**, use `--start-team` to avoid collisions:

```bash
python3 provision-students.py --teams 1 --start-team 11 --prefix LabAgent --region us-west-2
```

### 5. Verify student sandboxes

For each team, using their credentials from `env/team-XX.env`:

```bash
# Source the team's environment
source env/team-01.env

# Confirm identity
aws sts get-caller-identity --region us-west-2

# Invoke model
aws bedrock invoke-model --model-id <model-id> --body '{"prompt":"test"}' --region us-west-2 output.json

# Verify Gateway
aws bedrock-agentcore invoke-agent-gateway --gateway-name LabAgent-Team01-Gateway --region us-west-2
```

See the verification section at the end of `provision-students.py` output or `provisioning-student.md` section 11 for the full checklist.

### 6. Distribute credentials

Each team receives one `.env` file from the `env/` directory. **Do not email credentials in plain text.** Use a one-time secret service or the company's internal credential vault.

## Resource Naming Summary

### Instructor

| Resource       | Name                                            |
| -------------- | ----------------------------------------------- |
| S3 bucket      | `bedrock-instructor-demo-<account-id>-<random>` |
| Execution role | `LabAgent-Instructor-ExecutionRole`             |
| Gateway        | `InstructorDemoGateway`                         |
| Memory         | `InstructorDemoMemory`                          |
| Log group      | `/aws/bedrock/agentcore/instructor-demo`        |

### Students (per team)

| Resource       | Name (Team 01 example)                  |
| -------------- | --------------------------------------- |
| IAM user       | `LabAgent-Team01`                       |
| Execution role | `LabAgent-Team01-ExecutionRole`         |
| S3 bucket      | `labagent-team01-<account-id>-<random>` |
| Gateway        | `LabAgent-Team01-Gateway`               |
| Memory         | `LabAgent-Team01-Memory`                |
| Log group      | `/aws/bedrock/agentcore/team01`         |

## Teardown

After the course, tear down all resources with `teardown.py`. It reads the manifest files produced during provisioning and deletes every resource in the correct dependency order (users before roles, bucket contents before buckets, etc.).

```bash
# Dry run first — see what would be deleted
python3 teardown.py --all --region us-west-2 --dry-run

# Tear down both instructor and student resources
python3 teardown.py --all --region us-west-2

# Or tear down one at a time
python3 teardown.py --manifest env/instructor-manifest.json --region us-west-2
python3 teardown.py --manifest env/student-manifest.json --region us-west-2
```

Resources deleted per environment:

| Environment       | Resources cleaned up                                                                                                                         |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Instructor demo   | S3 bucket (emptied first), shared IAM execution role, Gateway, Memory, CloudWatch log group                                              |
| Student sandboxes | Per team: IAM user + access keys + policies, IAM execution role + policies, S3 bucket (emptied first), Gateway, Memory, CloudWatch log group |

## AgentCore SDK Note

Instructor provisioning and teardown use the `bedrock-agentcore-control` boto3 client. The script creates an IAM-authorized MCP Gateway and short-term Memory resource, records their AWS-generated IDs and ARNs, and reuses resources with matching names on later runs. Install the minimum boto3 version from `requirements.txt` before provisioning.

## Files in This Directory

| File                         | Purpose                                                           |
| ---------------------------- | ----------------------------------------------------------------- |
| `README.md`                  | This file                                                         |
| `requirements.txt`           | Python dependencies (boto3) — use with venv                       |
| `provision-instructor.py`    | Provisions the instructor demo sandbox                            |
| `validate-instructor.py`     | Validates provisioned instructor resources and optional model access |
| `provision-students.py`      | Provisions per-team student sandboxes                             |
| `teardown.py`                | Tears down all resources (reads manifest files from provisioning) |
| `provisioning-instructor.md` | Full specification — instructor environment                       |
| `provisioning-student.md`    | Full specification — student sandboxes                            |
