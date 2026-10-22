import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "dr", Path(__file__).resolve().parents[3] / "scripts/ops/disaster_recovery.py"
)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


def test_manifest_validation_and_corruption(tmp_path):
    (tmp_path / "objects").mkdir()
    (tmp_path / "database.dump").write_bytes(b"postgres")
    (tmp_path / "objects/one.obj").write_bytes(b"raw-document")
    manifest = {
        "format": "hip-dr-v1",
        "db": {"file": "database.dump", "sha256": dr.sha256_file(tmp_path / "database.dump")},
        "objects": [
            {
                "key": "nested/../safe",
                "file": "objects/one.obj",
                "sha256": dr.sha256_file(tmp_path / "objects/one.obj"),
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert len(dr.validate(tmp_path)["objects"]) == 1
    (tmp_path / "objects/one.obj").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        dr.validate(tmp_path)


def test_manifest_traversal_rejected(tmp_path):
    (tmp_path / "database.dump").write_bytes(b"x")
    manifest = {
        "format": "hip-dr-v1",
        "db": {"file": "../escape", "sha256": "anything"},
        "objects": [],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe"):
        dr.validate(tmp_path)
