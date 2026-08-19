# Distributing Student Credentials

After running `provision-students.py`, each team has its own `.env` file in `env/` containing an AWS access key, secret key, and console password. This document covers how to get that file to the right team without ever putting it in git, a shared chat channel, or a companion repo.

## Do Not Copy Credential Files Into `bedrock-companion`

It is tempting to drop `team-01.env` into the `bedrock-companion` repo so a student can pick it up alongside the lab materials. Do not do this, even though that repo is private:

- `bedrock-companion/.gitignore` only ignores a file literally named `.env`. It does **not** match `team-01.env` (no wildcard), so `team-01.env` is one `git add -A` away from being committed and pushed with a live AWS access key, secret key, and console password inside it.
- Git history is permanent. Rotating the key afterward does not remove it from history, and it stays visible to every past or future collaborator on the repo.
- `bedrock-lab-provision`'s own `.gitignore` already treats `env/*.env`, `env/*.csv`, `env/*manifest*.json`, and `env/handout-log.json` as untracked for exactly this reason. Copying those files into a different tracked repo defeats that safeguard.

Keep `env/` as the only place these files live until they are handed to a student through one of the channels below.

## Recommended: Short-Lived Presigned S3 URLs

`distribute-credentials.py` uploads each team's file to a private, encrypted S3 bucket and prints a presigned download link that expires in a few hours. The link is what you paste into a private Zoom DM or email — never the file itself, and never a broadcast/shared channel, since anyone holding the link can use it until it expires.

### Send links

```bash
python3 distribute-credentials.py --teams 1 2 --expires-hours 4 --region us-east-1
```

- Creates (once) a bucket named `<prefix>-handout-<account-id>` — SSE-S3 encrypted, all public access blocked, with a 7-day lifecycle rule on the `handouts/` prefix as a backstop cleanup.
- Uploads each team's file under a random per-run path: `handouts/<run-id>/team-XX.env`.
- Prints one presigned URL per team plus its expiry time and a ready-to-copy revoke command.
- Logs upload metadata (bucket, key, timestamp — never the URL itself) to `env/handout-log.json`, which is gitignored.

Add `--include-csv` to also ship `team-XX-credentials.csv`. Add `--dry-run` to preview without making AWS calls.

### Revoke links early

A presigned URL can only be invalidated by deleting the underlying object — there is no separate revocation list. Once a team has confirmed they've saved their credentials, kill the link:

```bash
python3 distribute-credentials.py --revoke <run-id> --bucket <prefix>-handout-<account-id> --region us-east-1
```

### Options

| Flag | Default | Purpose |
| --- | --- | --- |
| `--teams` | all teams found in `env/` | Which team numbers to send |
| `--expires-hours` | `4` | Link lifetime (max `168`, the S3 presign hard cap) |
| `--include-csv` | off | Also upload `team-XX-credentials.csv` |
| `--bucket` | `<prefix>-handout-<account-id>` | Override the handout bucket name |
| `--prefix` | `LabAgent` | Used only to compute the default bucket name |
| `--region` | `us-east-1` | AWS region for the bucket and API calls |
| `--dry-run` | off | Print what would happen, no AWS calls |
| `--revoke RUN_ID` | — | Delete a run's files immediately, invalidating its links |

## Fallback: Corporate Network Blocks AWS Domains

Some students join from a network that blocks S3 or other AWS domains. When that happens, skip the presigned link and use one of these instead:

- **Zoom file transfer** — share `env/team-XX.env` directly through Zoom's in-meeting file transfer to that student only, not to everyone.
- **Google Drive / OneDrive** — upload the file manually and generate an expiring "anyone with the link" share, sent privately to that team.

Both are manual steps; `distribute-credentials.py` does not automate them.

## Security Notes

- A presigned URL is a bearer credential for its entire lifetime — anyone who obtains the link can use it, no login required. Keep `--expires-hours` as short as your class logistics allow; the default of 4 hours is meant for "send it now, they use it within the session."
- Send each team only their own link. Do not paste multiple teams' links into one message or channel.
- The handout bucket is reused across runs; only the `handouts/<run-id>/` prefix changes per invocation, so revoking one run's id does not affect other in-flight links.

## Related

- [`provision-students.py`](../provision-students.py) — generates the `.env` files this workflow distributes.
- [`validate-students.py`](../validate-students.py) — confirms each team's provisioned resources are healthy.
- [`provisioning-student.md`](../provisioning-student.md) — full student sandbox specification.
