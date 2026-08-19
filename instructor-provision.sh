#!/bin/bash
python3 --version

cd /Users/kangs/code/github/bedrock-lab-provision

python3 -m venv .venv

source .venv/bin/activate
pip install -r requirements.txt

python3 -c "import boto3; print(boto3.__version__)"

export AWS_PROFILE=bedrock-course-instructor
export AWS_ACCESS_KEY_ID=${INST_AWS_ACCESS_KEY_ID}
export AWS_SECRET_ACCESS_KEY=${INST_AWS_SECRET_ACCESS_KEY}
export AWS_DEFAULT_REGION=us-east-1

aws configure --profile bedrock-course-instructor
export AWS_PROFILE=bedrock-course-instructor
export AWS_DEFAULT_REGION=us-east-1

aws sts get-caller-identity
python ./provision-instructor.py --region us-east-1
python ./validate-instructor.py \
  --manifest env/instructor-manifest.json \
  --region us-east-1


python3 ./provision-instructor.py --region us-east-1

python3 ./validate-instructor.py \
  --manifest env/instructor-manifest.json \
  --region us-east-1

