#!/usr/bin/env python3
"""Destructive-in-ephemeral-resources disaster recovery test for CI/staging.

Creates its own isolated PostgreSQL databases and MinIO buckets, writes fixture
rows/objects, runs backup + restore and verifies recovered bytes. Never targets
existing application databases/buckets. Requires explicit test-only opt-in.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ops import disaster_recovery as dr


def _allowed() -> None:
    if os.getenv("APP_ENV", "").lower() != "test" or os.getenv("HIP_ALLOW_DR_DB_DRILL") != "1":
        raise RuntimeError("DR drill requires APP_ENV=test and HIP_ALLOW_DR_DB_DRILL=1")
    endpoint = os.getenv("S3_ENDPOINT_URL", "")
    # This integration test always creates/deletes buckets. Never point at AWS S3.
    if not endpoint.startswith(("http://localhost:", "http://127.0.0.1:")):
        raise RuntimeError("DR drill requires a locally hosted S3-compatible endpoint")
    if not os.getenv("S3_ACCESS_KEY") or not os.getenv("S3_SECRET_KEY"):
        raise RuntimeError("DR drill requires explicit test MinIO credentials")
    env, name = dr.postgres_connection_env(os.environ["DATABASE_URL"])
    if env.get("PGHOST") not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("DR drill requires a local PostgreSQL host")
    if name not in {"hackathon", "postgres", "hip_test"}:
        raise RuntimeError("DR drill requires an explicitly named CI/staging base database")


def _connect_params() -> dict:
    env, _ = dr.postgres_connection_env(os.environ["DATABASE_URL"])
    return {
        "host": env.get("PGHOST", "localhost"),
        "port": int(env.get("PGPORT", "5432")),
        "user": env.get("PGUSER", ""),
        "password": env.get("PGPASSWORD", ""),
    }


def _ready(s3) -> None:
    last = None
    for _ in range(40):
        try:
            s3.list_buckets()
            return
        except Exception as exc:  # Connection may not yet be ready at CI startup.
            last = exc
            time.sleep(1)
    raise RuntimeError("local MinIO did not become ready") from last


def main() -> None:
    _allowed()
    import psycopg
    from psycopg import sql

    params = _connect_params()
    s3 = dr._storage()
    _ready(s3)
    suffix = secrets.token_hex(5)
    db_source = f"hip_dr_ci_src_{suffix}"
    db_target = f"hip_dr_ci_dst_{suffix}"
    bucket_source = f"hip-dr-ci-src-{suffix}"
    bucket_target = f"hip-dr-ci-dst-{suffix}"
    created_databases = []
    created_buckets = []
    base = os.environ["DATABASE_URL"]
    # Identical connectivity + credentials, but two fresh ephemeral databases.
    parsed = urlsplit(base)
    source_url = urlunsplit(parsed._replace(path="/" + db_source))
    target_url = urlunsplit(parsed._replace(path="/" + db_target))
    rows = [("demo-1", 4), ("demo-2", 9)]
    objects = {
        "source/demo.json": b'{"title":"hackathon","version":1}',
        "nested/unicode-olá.txt": "arquivo íntegro".encode("utf-8"),
    }
    try:
        with psycopg.connect(dbname="postgres", autocommit=True, **params) as admin:
            for name in (db_source, db_target):
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created_databases.append(name)
        for name in (bucket_source, bucket_target):
            s3.create_bucket(Bucket=name)
            created_buckets.append(name)
        with psycopg.connect(dbname=db_source, **params) as src:
            src.execute("CREATE TABLE dr_fixture (id TEXT PRIMARY KEY, score INTEGER NOT NULL)")
            with src.cursor() as cursor:
                cursor.executemany("INSERT INTO dr_fixture (id, score) VALUES (%s, %s)", rows)
            src.commit()
        for key, contents in objects.items():
            s3.put_object(Bucket=bucket_source, Key=key, Body=contents)
        with tempfile.TemporaryDirectory(prefix="hip-dr-ci-") as scratch:
            backup_dir = Path(scratch) / "backup"
            started = time.monotonic()
            manifest = dr.backup(backup_dir, source_url, bucket_source)
            backup_seconds = time.monotonic() - started
            assert manifest["format"] == "hip-dr-v1"
            assert len(dr.validate(backup_dir)["objects"]) == len(objects)
            started = time.monotonic()
            dr.restore(backup_dir, target_url, bucket_target)
            restore_seconds = time.monotonic() - started
            with psycopg.connect(dbname=db_target, **params) as dest:
                recovered = dest.execute("SELECT id, score FROM dr_fixture ORDER BY id").fetchall()
            assert recovered == rows, "PostgreSQL rows were not faithfully restored"
            for key, expected in objects.items():
                response = s3.get_object(Bucket=bucket_target, Key=key)
                actual = response["Body"].read()
                assert actual == expected, f"S3 object differs after restore: {key}"
            print(json.dumps({
                "ok": True, "postgres_rows": len(recovered), "s3_objects": len(objects),
                "backup_seconds": round(backup_seconds, 3),
                "restore_seconds": round(restore_seconds, 3),
                "sample_object_sha256": hashlib.sha256(objects["source/demo.json"]).hexdigest(),
                "mode": "isolated-ci-drill", "consistency": "quiesced-fixture-only",
            }))
    finally:
        # Only delete ephemeral names generated by this invocation.
        for bucket in created_buckets:
            if not bucket.startswith("hip-dr-ci-"):
                raise RuntimeError("refusing to delete an unexpected bucket")
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                entries = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if entries:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": entries})
            s3.delete_bucket(Bucket=bucket)
        with psycopg.connect(dbname="postgres", autocommit=True, **params) as admin:
            for name in created_databases:
                if not name.startswith("hip_dr_ci_"):
                    raise RuntimeError("refusing to drop an unexpected database")
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
