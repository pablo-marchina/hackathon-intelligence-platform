#!/usr/bin/env bash
# PostgreSQL-only backup helper. For PostgreSQL + MinIO use disaster_recovery.py.
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
python3 - "$BACKUP_DIR" <<'PY'
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from scripts.ops.disaster_recovery import postgres_connection_env

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
output = root / f"hackathon-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
if output.exists():
    raise FileExistsError(f"refusing to overwrite existing backup: {output}")
env, name = postgres_connection_env(os.environ["DATABASE_URL"])
subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(output),
                "--dbname", name], env=env, check=True)
output.chmod(0o600)
print(output)
PY
