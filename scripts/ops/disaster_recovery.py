#!/usr/bin/env python3
"""PostgreSQL + S3 backup and restore with integrity checks and explicit targets.

The archive is NOT a point-in-time consistent snapshot across PostgreSQL and S3.
Quiesce writes or use a coordinated snapshot procedure for a recovery-point SLA.
Never restore an untrusted archive: SHA-256 checksums are not authenticity proofs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

# These are libpq environment variables, not PostgreSQL command-line flags.
# In particular, passwords must never appear in process arguments.
_LIBPQ_QUERY_OPTIONS = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def postgres_connection_env(db_url: str) -> tuple[dict[str, str], str]:
    """Turn an app/SQLAlchemy PostgreSQL URL into libpq env + safe DB name.

    pg_dump/pg_restore do not recognize the `postgresql+psycopg` URL scheme.
    Supplying only PGDATABASE to pg_restore is insufficient: without -d it
    renders the SQL script to stdout instead of connecting to the database.
    """
    parts = urlsplit(db_url)
    if parts.scheme not in {"postgresql", "postgres", "postgresql+psycopg"}:
        raise ValueError("expected PostgreSQL URL (postgresql or postgresql+psycopg)")
    database = unquote(parts.path.removeprefix("/"))
    if not database or "/" in database or "\x00" in database:
        raise ValueError("PostgreSQL URL must specify one database")
    if parts.fragment:
        raise ValueError("PostgreSQL URL fragments are not supported")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid PostgreSQL port") from exc
    env = dict(os.environ)
    # Ignore *all* inherited libpq settings, including PGHOSTADDR/PGSERVICE.
    # Otherwise an attacker-controlled runner environment could redirect the
    # backup/restore even when the application URL contains the intended host.
    for key in tuple(env):
        if key.startswith("PG"):
            env.pop(key, None)
    if parts.hostname is not None:
        env["PGHOST"] = parts.hostname
    if port is not None:
        env["PGPORT"] = str(port)
    if parts.username is not None:
        env["PGUSER"] = unquote(parts.username)
    if parts.password is not None:
        env["PGPASSWORD"] = unquote(parts.password)
    options_seen: set[str] = set()
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key not in _LIBPQ_QUERY_OPTIONS or key in options_seen:
            raise ValueError(f"unsupported or repeated PostgreSQL URL option: {key}")
        options_seen.add(key)
        env[_LIBPQ_QUERY_OPTIONS[key]] = value
    env["PGDATABASE"] = database
    return env, database


def _db_identity(db_url: str) -> tuple[str, str, str]:
    env, name = postgres_connection_env(db_url)
    host = env.get("PGHOST", "localhost").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "localhost"
    return host, env.get("PGPORT", "5432"), name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _storage():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("S3_ENDPOINT_URL") or None
    key = os.getenv("S3_ACCESS_KEY")
    secret = os.getenv("S3_SECRET_KEY")
    if bool(key) != bool(secret):
        raise ValueError("both S3_ACCESS_KEY and S3_SECRET_KEY must be set together")
    kwargs = {
        "endpoint_url": endpoint,
        "region_name": os.getenv("S3_REGION", "us-east-1"),
        "config": Config(
            retries={"max_attempts": 3, "mode": "standard"},
            **({"s3": {"addressing_style": "path"}} if endpoint else {}),
        ),
    }
    if key:
        kwargs.update(aws_access_key_id=key, aws_secret_access_key=secret)
    return boto3.client("s3", **kwargs)


def backup(destination: Path, db_url: str, bucket: str) -> dict:
    # Verify config BEFORE creating a backup directory or doing any work.
    env, dbname = postgres_connection_env(db_url)
    client = _storage()
    destination.mkdir(parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    dump = destination / "database.dump"
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--file",
            str(dump),
            "--dbname",
            dbname,
        ],
        check=True,
        env=env,
    )
    os.chmod(dump, 0o600)
    objects = []
    continuation = None
    seen_keys: set[str] = set()
    while True:
        args = {"Bucket": bucket}
        if continuation:
            args["ContinuationToken"] = continuation
        page = client.list_objects_v2(**args)
        for item in page.get("Contents", []):
            key = item["Key"]
            if not isinstance(key, str) or key in seen_keys:
                raise ValueError("duplicate or invalid object key in S3 listing")
            seen_keys.add(key)
            filename = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".obj"
            local = destination / "objects" / filename
            local.parent.mkdir(exist_ok=True)
            client.download_file(bucket, key, str(local))
            os.chmod(local, 0o600)
            objects.append(
                {
                    "key": key,
                    "file": "objects/" + filename,
                    "sha256": sha256_file(local),
                    "size": local.stat().st_size,
                }
            )
        if not page.get("IsTruncated"):
            break
        next_token = page.get("NextContinuationToken")
        if not next_token or next_token == continuation:
            raise ValueError("invalid S3 listing continuation token")
        continuation = next_token
    manifest = {
        "format": "hip-dr-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "db": {"file": "database.dump", "sha256": sha256_file(dump), "size": dump.stat().st_size},
        "object_bucket": bucket,
        "objects": objects,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return manifest


def validate(directory: Path) -> dict:
    root = directory.resolve(strict=True)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("invalid manifest file")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != "hip-dr-v1":
        raise ValueError("unknown manifest format")
    db = manifest.get("db")
    objects = manifest.get("objects")
    if not isinstance(db, dict) or not isinstance(objects, list):
        raise ValueError("invalid backup manifest structure")
    if db.get("file") != "database.dump":
        raise ValueError("unsafe or unexpected database dump path")
    used_files: set[str] = set()
    used_keys: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get("key"), str):
            raise ValueError("invalid object record")
        if obj["key"] in used_keys:
            raise ValueError("duplicate object key in manifest")
        used_keys.add(obj["key"])
    for entry in [db, *objects]:
        name = entry.get("file")
        digest = entry.get("sha256")
        if not isinstance(name, str) or not name or not isinstance(digest, str):
            raise ValueError("invalid manifest entry")
        if name in used_files:
            raise ValueError("duplicate backup file in manifest")
        used_files.add(name)
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe manifest path")
        candidate = root / relative
        # Reject symbolic links (including in parent directories), not just traversal.
        for segment in (candidate, *candidate.parents):
            if segment == root:
                break
            if segment.is_symlink():
                raise ValueError("unsafe backup symlink")
        if not candidate.resolve().is_relative_to(root):
            raise ValueError("manifest path escape")
        if not candidate.is_file() or not stat.S_ISREG(candidate.stat().st_mode):
            raise ValueError("backup entry is not a regular file")
        if "size" in entry and (
            type(entry["size"]) is not int
            or entry["size"] < 0
            or candidate.stat().st_size != entry["size"]
        ):
            raise ValueError(f"size mismatch: {name}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid SHA-256 checksum")
        if sha256_file(candidate) != digest:
            raise ValueError(f"checksum mismatch: {name}")
    return manifest


def restore(
    directory: Path, db_url: str, destination_bucket: str, *, allow_source_target: bool = False
) -> None:
    if not destination_bucket or not destination_bucket.strip():
        raise ValueError("restore needs an explicit destination bucket")
    source = os.getenv("DATABASE_URL")
    if source and _db_identity(source) == _db_identity(db_url) and not allow_source_target:
        raise ValueError("refusing restore into configured source database")
    if os.getenv("APP_ENV", "").lower() in {"production", "prod"} and not allow_source_target:
        raise ValueError("refusing restore while APP_ENV=production without explicit override")
    env, dbname = postgres_connection_env(db_url)
    manifest = validate(directory)  # Check all archive bytes before destructive restore.
    if manifest.get("object_bucket") == destination_bucket and not allow_source_target:
        raise ValueError("refusing restore into the original source bucket")
    client = _storage()
    subprocess.run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            "--dbname",
            dbname,
            str(directory / manifest["db"]["file"]),
        ],
        check=True,
        env=env,
    )
    for obj in manifest["objects"]:
        client.upload_file(str(directory / obj["file"]), destination_bucket, obj["key"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("directory", type=Path)
    v = sub.add_parser("validate")
    v.add_argument("directory", type=Path)
    r = sub.add_parser("restore")
    r.add_argument("directory", type=Path)
    r.add_argument("--target-db", required=True)
    r.add_argument("--target-bucket", required=True)
    r.add_argument("--confirm-restore", action="store_true")
    r.add_argument(
        "--allow-source-target",
        action="store_true",
        help="DANGEROUS: explicitly permit restore into source or production",
    )
    args = parser.parse_args()
    if args.command == "backup":
        print(
            json.dumps(backup(args.directory, os.environ["DATABASE_URL"], os.environ["S3_BUCKET"]))
        )
    elif args.command == "validate":
        print(json.dumps({"ok": True, "objects": len(validate(args.directory)["objects"])}))
    else:
        if not args.confirm_restore:
            parser.error("restore requires --confirm-restore")
        restore(
            args.directory,
            args.target_db,
            args.target_bucket,
            allow_source_target=args.allow_source_target,
        )
        print("restore completed and input checksums verified")


if __name__ == "__main__":
    main()
