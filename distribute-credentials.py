#!/usr/bin/env python3
"""
Distribute per-team student credential files without ever putting them in
git, chat history, or email as plaintext.

Uploads each team's env/team-XX.env (and optionally team-XX-credentials.csv)
to a private, encrypted S3 bucket and prints one short-lived presigned URL
per team. Paste each team's own link into a private Zoom chat/DM or email —
never into a shared/broadcast channel, since the link IS the credential
while it's valid.

If a team's network blocks AWS domains, fall back to sharing their
env/team-XX.env file directly through Zoom's in-meeting file transfer, or
upload it manually to Google Drive/OneDrive with an expiring link.

Usage
-----
  python3 distribute-credentials.py --teams 1 2 --expires-hours 4
  python3 distribute-credentials.py --revoke <run-id>
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("ERROR: boto3 is required. Install it: pip3 install -r requirements.txt")
    sys.exit(2)


OUTPUT_DIR = "env"
HANDOUT_PREFIX = "handouts"
HANDOUT_LOG_FILE = "handout-log.json"
LIFECYCLE_RULE_ID = "handout-expiry"
LIFECYCLE_DAYS = 7
MAX_EXPIRES_HOURS = 7 * 24  # S3 SigV4 presign hard cap


class DistributionError(Exception):
    pass


def _bucket_name(prefix: str, account_id: str) -> str:
    return f"{prefix.lower()}-handout-{account_id}"


def _ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        return
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("404", "NoSuchBucket"):
            raise

    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print(f"Created handout bucket: {bucket}")

    # Backstop cleanup, independent of --expires-hours, in case a link is
    # never revoked and the instructor forgets about it.
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": LIFECYCLE_RULE_ID,
                    "Status": "Enabled",
                    "Filter": {"Prefix": f"{HANDOUT_PREFIX}/"},
                    "Expiration": {"Days": LIFECYCLE_DAYS},
                }
            ]
        },
    )


def _load_log(out_dir: Path) -> list[dict]:
    log_path = out_dir / HANDOUT_LOG_FILE
    if not log_path.exists():
        return []
    try:
        return json.loads(log_path.read_text())
    except json.JSONDecodeError:
        return []


def _append_log(out_dir: Path, entries: list[dict]) -> None:
    log_path = out_dir / HANDOUT_LOG_FILE
    existing = _load_log(out_dir)
    existing.extend(entries)
    log_path.write_text(json.dumps(existing, indent=2))


def send(args) -> int:
    out_dir = Path(OUTPUT_DIR)
    if not out_dir.exists():
        print(f"ERROR: {out_dir} not found; run provision-students.py first")
        return 2

    team_files: dict[int, list[Path]] = {}
    for env_path in sorted(out_dir.glob("team-*.env")):
        team_num = int(env_path.stem.split("-")[1])
        if args.teams and team_num not in args.teams:
            continue
        files = [env_path]
        if args.include_csv:
            csv_path = out_dir / f"team-{team_num:02d}-credentials.csv"
            if csv_path.exists():
                files.append(csv_path)
        team_files[team_num] = files

    if not team_files:
        print("ERROR: no matching team-XX.env files found in env/")
        return 2

    if args.teams:
        missing = set(args.teams) - set(team_files)
        if missing:
            print(f"ERROR: no env file for team(s): {sorted(missing)}")
            return 2

    if args.dry_run:
        s3 = None
        bucket = args.bucket or _bucket_name(args.prefix, "123456789012")
        print(f"[DRY RUN] Would ensure bucket: {bucket}")
    else:
        sts = boto3.client("sts", region_name=args.region)
        account_id = sts.get_caller_identity()["Account"]
        s3 = boto3.client("s3", region_name=args.region)
        bucket = args.bucket or _bucket_name(args.prefix, account_id)
        _ensure_bucket(s3, bucket, args.region)
    run_id = secrets.token_hex(4)

    print()
    print("=" * 70)
    print(f"  Credential handout links — run {run_id}")
    print(f"  Expire in {args.expires_hours}h; revoke early with:")
    print(f"    python3 distribute-credentials.py --revoke {run_id} --bucket {bucket} --region {args.region}")
    print("=" * 70)
    print(
        "  Paste each link into that team's own private Zoom chat/DM or email\n"
        "  only. Do not post into a shared/broadcast channel."
    )
    print()

    log_entries = []
    for team_num in sorted(team_files):
        for file_path in team_files[team_num]:
            key = f"{HANDOUT_PREFIX}/{run_id}/{file_path.name}"
            if args.dry_run:
                print(f"[DRY RUN] Team{team_num:02d}: would upload {file_path} -> s3://{bucket}/{key}")
                continue

            s3.upload_file(str(file_path), bucket, key)
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=args.expires_hours * 3600,
            )
            expires_at = time.strftime(
                "%Y-%m-%d %H:%M %Z", time.localtime(time.time() + args.expires_hours * 3600)
            )
            print(f"  Team{team_num:02d} ({file_path.name}), expires {expires_at}:")
            print(f"    {url}")
            print()
            log_entries.append({
                "run_id": run_id,
                "team_num": team_num,
                "bucket": bucket,
                "key": key,
                "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "expires_hours": args.expires_hours,
            })

    if not args.dry_run and log_entries:
        _append_log(out_dir, log_entries)
        print(f"Logged upload metadata (no secrets) to {out_dir / HANDOUT_LOG_FILE}")

    print()
    print(
        "If a team's network blocks AWS/S3 domains: share their env/team-XX.env\n"
        "directly through Zoom's in-meeting file transfer, or upload it manually\n"
        "to Google Drive/OneDrive with an expiring 'anyone with the link' share."
    )
    return 0


def revoke(args) -> int:
    if not args.bucket:
        print("ERROR: --revoke requires --bucket")
        return 2

    s3 = boto3.client("s3", region_name=args.region)
    prefix = f"{HANDOUT_PREFIX}/{args.revoke}/"
    paginator = s3.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix)
        for obj in page.get("Contents", [])
    ]
    if not keys:
        print(f"No objects found under s3://{args.bucket}/{prefix}")
        return 0

    s3.delete_objects(
        Bucket=args.bucket,
        Delete={"Objects": [{"Key": k} for k in keys]},
    )
    print(f"Revoked {len(keys)} file(s) under s3://{args.bucket}/{prefix}")
    print("Any previously issued presigned URLs for this run now return 403.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Distribute per-team student credential files via short-lived presigned S3 URLs"
    )
    parser.add_argument("--teams", type=int, nargs="+", help="Team numbers to send (default: all found in env/)")
    parser.add_argument("--expires-hours", type=float, default=4, help="Link lifetime in hours (default: 4, max: 168)")
    parser.add_argument("--include-csv", action="store_true", help="Also upload team-XX-credentials.csv")
    parser.add_argument("--bucket", help="Handout bucket name (default: <prefix>-handout-<account-id>)")
    parser.add_argument("--prefix", default="LabAgent", help="Resource prefix, used to name the default bucket (default: LabAgent)")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen without making AWS calls")
    parser.add_argument("--revoke", metavar="RUN_ID", help="Delete all handout files for this run id, invalidating its links immediately")
    args = parser.parse_args()

    if args.expires_hours <= 0 or args.expires_hours > MAX_EXPIRES_HOURS:
        print(f"ERROR: --expires-hours must be between 0 and {MAX_EXPIRES_HOURS}")
        return 2

    if args.revoke:
        return revoke(args)
    return send(args)


if __name__ == "__main__":
    sys.exit(main())
