"""W6 DR: backup and restore are verified without requiring live infrastructure."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[3] / "scripts/ops/disaster_recovery.py"
spec = importlib.util.spec_from_file_location("hip_dr_w6_7", MODULE)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def example_archive(tmp_path: Path, *, object_key="nested/a.json") -> Path:
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"sample-postgres-dump")
    (tmp_path / "objects").mkdir()
    obj = tmp_path / "objects" / "a.obj"
    obj.write_bytes(b'{"example": true}')
    manifest = {
        "format": "hip-dr-v1",
        "db": {
            "file": "database.dump",
            "sha256": dr.sha256_file(dump),
            "size": dump.stat().st_size,
        },
        "objects": [
            {
                "key": object_key,
                "file": "objects/a.obj",
                "sha256": dr.sha256_file(obj),
                "size": obj.stat().st_size,
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_url_conversion_handles_sqlalchemy_and_sanitizes_environment(monkeypatch):
    monkeypatch.setenv("PGHOST", "stale.production.example")
    monkeypatch.setenv("PGPASSWORD", "stale")
    monkeypatch.setenv("PGHOSTADDR", "169.254.169.254")
    monkeypatch.setenv("PGSERVICE", "external")
    env, name = dr.postgres_connection_env(
        "postgresql+psycopg://hip:p%40ss%3Aword@postgres.local:5433/dr_restore?sslmode=verify-full"
    )
    assert name == "dr_restore"
    assert env["PGHOST"] == "postgres.local"
    assert env["PGPORT"] == "5433"
    assert env["PGUSER"] == "hip"
    assert env["PGPASSWORD"] == "p@ss:word"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGDATABASE"] == "dr_restore"
    assert "PGHOSTADDR" not in env and "PGSERVICE" not in env


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.db",
        "postgresql://user@server/",
        "postgresql://h/db?untrusted=x",
        "postgresql://h/db?sslmode=require&sslmode=disable",
        "postgresql://h/db#fragment",
    ],
)
def test_invalid_db_connections_rejected(url):
    with pytest.raises(ValueError):
        dr.postgres_connection_env(url)


def test_storage_uses_explicit_minio_credentials_and_path_style(monkeypatch):
    import boto3

    captured = {}

    def fake_client(*args, **kwargs):
        captured.update(kwargs)
        assert args == ("s3",)
        return "client"

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "local_key")
    monkeypatch.setenv("S3_SECRET_KEY", "local_secret")
    assert dr._storage() == "client"
    assert captured["aws_access_key_id"] == "local_key"
    assert captured["aws_secret_access_key"] == "local_secret"
    assert captured["config"].s3["addressing_style"] == "path"
    monkeypatch.delenv("S3_SECRET_KEY")
    with pytest.raises(ValueError, match="both S3_ACCESS_KEY"):
        dr._storage()


def test_backup_uses_dbname_and_never_exposes_credentials_in_arguments(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, *, env, check):
        seen.append((args, env, check))
        (tmp_path / "backups" / "database.dump").write_bytes(b"pg-content")

    class FakeS3:
        def list_objects_v2(self, **kwargs):
            return {"Contents": [{"Key": "nested/document.json"}], "IsTruncated": False}

        def download_file(self, bucket, key, filename):
            Path(filename).write_bytes(b"document-content")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    monkeypatch.setattr(dr, "_storage", FakeS3)
    manifest = dr.backup(
        tmp_path / "backups", "postgresql+psycopg://hip:password@db/hip_src", "raw"
    )
    args, env, check = seen[0]
    assert args[0] == "pg_dump" and args[args.index("--dbname") + 1] == "hip_src"
    assert "password" not in " ".join(args)
    assert env["PGPASSWORD"] == "password"
    assert dr.validate(tmp_path / "backups") == manifest


def test_restore_executes_pg_restore_with_target_db_and_uploads(monkeypatch, tmp_path):
    example_archive(tmp_path)
    captured = []
    uploads = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        dr.subprocess, "run", lambda args, *, check, env: captured.append((args, env))
    )
    monkeypatch.setattr(
        dr, "_storage", lambda: SimpleNamespace(upload_file=lambda *args: uploads.append(args))
    )
    dr.restore(tmp_path, "postgresql+psycopg://hip:secret@db/hip_restore", "restore-bucket")
    args, env = captured[0]
    assert args[0] == "pg_restore"
    assert args[args.index("--dbname") + 1] == "hip_restore"
    assert "--exit-on-error" in args and "secret" not in " ".join(args)
    assert env["PGPASSWORD"] == "secret"
    assert uploads == [(str(tmp_path / "objects/a.obj"), "restore-bucket", "nested/a.json")]


def test_corrupt_archive_cannot_start_db_restore(monkeypatch, tmp_path):
    example_archive(tmp_path)
    (tmp_path / "objects/a.obj").write_bytes(b"different")
    invoked = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(dr.subprocess, "run", lambda *args, **kwargs: invoked.append(args))
    with pytest.raises(ValueError, match="size mismatch"):
        dr.restore(tmp_path, "postgresql://db/hip_restore", "target")
    assert not invoked


@pytest.mark.parametrize("corruption", ["duplicate_file", "duplicate_key", "size", "symlink"])
def test_archive_rejects_unsafe_manifest_files(tmp_path, corruption):
    example_archive(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if corruption == "duplicate_file":
        manifest["objects"].append({**manifest["objects"][0], "key": "another"})
    elif corruption == "duplicate_key":
        manifest["objects"].append({**manifest["objects"][0], "file": "objects/another.obj"})
    elif corruption == "size":
        manifest["objects"][0]["size"] += 1
    elif corruption == "symlink":
        file = tmp_path / "objects/a.obj"
        file.rename(tmp_path / "objects/b.obj")
        file.symlink_to(tmp_path / "objects/b.obj")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        dr.validate(tmp_path)


def test_refuses_source_db_and_production_restore_without_override(monkeypatch, tmp_path):
    example_archive(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:pw@db/source")
    with pytest.raises(ValueError, match="source database"):
        dr.restore(tmp_path, "postgresql://other:pw@db/source", "target")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="APP_ENV=production"):
        dr.restore(tmp_path, "postgresql://db/restore", "target")


def test_rejects_object_symlink_parent(tmp_path):
    example_archive(tmp_path)
    (tmp_path / "objects/a.obj").unlink()
    (tmp_path / "objects").rmdir()
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    (outside / "a.obj").write_bytes(b'{"example": true}')
    (tmp_path / "objects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        dr.validate(tmp_path)


def test_integration_drill_refuses_unsafe_environment(monkeypatch):
    import sys

    root = MODULE.parents[2]
    sys.path.insert(0, str(root))
    from scripts.ops import dr_integration_check as check

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HIP_ALLOW_DR_DB_DRILL", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/hackathon")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "fake")
    monkeypatch.setenv("S3_SECRET_KEY", "fake")
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        check._allowed()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.amazonaws.com")
    with pytest.raises(RuntimeError, match="locally hosted"):
        check._allowed()
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@production-db/hackathon")
    with pytest.raises(RuntimeError, match="local PostgreSQL"):
        check._allowed()


def test_restore_refuses_source_bucket_before_db_mutation(monkeypatch, tmp_path):
    example_archive(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["object_bucket"] = "original"
    manifest_path.write_text(json.dumps(manifest))
    called = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(dr.subprocess, "run", lambda *args, **kwargs: called.append(args))
    with pytest.raises(ValueError, match="source bucket"):
        dr.restore(tmp_path, "postgresql://db/restore", "original")
    assert not called


def test_pg_only_restore_wrapper_requires_confirmation(tmp_path):
    import os
    import subprocess

    wrapper = MODULE.parent / "restore_postgres.sh"
    result = subprocess.run(
        ["bash", str(wrapper), str(tmp_path / "archive.dump")],
        env={"PATH": os.getenv("PATH", ""), "DATABASE_URL": "postgresql://localhost/restore"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "HIP_CONFIRM_PG_RESTORE" in result.stderr
