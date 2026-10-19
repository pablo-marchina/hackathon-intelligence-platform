#!/usr/bin/env bash
# PostgreSQL-only emergency restore; explicit confirmation is mandatory.
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${HIP_CONFIRM_PG_RESTORE:?Set HIP_CONFIRM_PG_RESTORE=YES to authorize destructive restore}"
if [[ "$HIP_CONFIRM_PG_RESTORE" != "YES" ]]; then
  echo "HIP_CONFIRM_PG_RESTORE must be exactly YES" >&2
  exit 2
fi
BACKUP_FILE="${1:?usage: HIP_CONFIRM_PG_RESTORE=YES restore_postgres.sh path/to/backup.dump}"
test -f "$BACKUP_FILE"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
python3 - "$BACKUP_FILE" <<'PY'
import os
import subprocess
import sys
from pathlib import Path
from scripts.ops.disaster_recovery import postgres_connection_env

if os.getenv("APP_ENV", "").lower() in {"production", "prod"} and os.getenv("HIP_ALLOW_PROD_RESTORE") != "YES":
    raise RuntimeError("production restore requires HIP_ALLOW_PROD_RESTORE=YES")
env, name = postgres_connection_env(os.environ["DATABASE_URL"])
archive = Path(sys.argv[1]).resolve(strict=True)
subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl",
                "--exit-on-error", "--dbname", name, str(archive)], env=env, check=True)
print("PostgreSQL restore command completed")
PY
