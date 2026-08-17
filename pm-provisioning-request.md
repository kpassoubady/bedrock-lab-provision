# AWS Environment Provisioning Request - Bedrock AgentCore Course

**Request Summary:**
We need an AWS sandbox environment provisioned for the upcoming "Building Production-Ready Agents with Amazon Bedrock AgentCore" course. 

This requires setting up isolated AWS resources for both the instructor and the students to ensure a secure, hands-on lab experience. We have pre-built automation scripts to handle the entire deployment, we just need an AWS environment and an IT/Cloud engineer with sufficient privileges to run them.

**High-Level Requirements:**

1. **Instructor Environment (1 sandbox):**
   - A dedicated sandbox for the instructor to use for live demonstrations and as a known-good reference during class.
   - It requires an S3 bucket, an IAM execution role, Amazon Bedrock model access, and AgentCore resources (Gateway, Memory, and CloudWatch log groups).

2. **Student Environments (Per-Team sandboxes):**
   - Isolated sandbox environments for each student team.
   - Each sandbox requires tightly scoped IAM credentials (to enforce least-privilege identity boundaries), an isolated S3 bucket, and dedicated AgentCore resources (Gateway, Memory, and CloudWatch log groups).
   - This strict isolation ensures teams cannot interfere with each other's work or access the broader AWS account.

**Next Steps:**
Once this request is assigned, I will provide a zip file containing the automated Python provisioning scripts and a detailed `README.md`. Your engineer will simply need to run the scripts to automatically generate all the required credentials and environments.

