"""Assert that W6 migration statements are executable units before CI runs them in PG."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_w6_migration_upgrade_and_downgrade_statements(monkeypatch) -> None:
    location = (
        Path(__file__).resolve().parents[3] / "db/alembic/versions/0006_w6_admin_control_plane.py"
    )
    spec = importlib.util.spec_from_file_location("w6_control_migration", location)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Operations:
        statements: list[str] = []

        @classmethod
        def execute(cls, statement):
            cls.statements.append(str(statement))

    monkeypatch.setattr(module, "op", Operations)
    module.upgrade()
    assert len(Operations.statements) == 4
    assert "CREATE TABLE admin_user_roles" in Operations.statements[0]
    assert "ALTER TABLE admin_audit_log" in Operations.statements[2]
    Operations.statements.clear()
    module.downgrade()
    assert len(Operations.statements) == 3
    assert "DROP TABLE IF EXISTS admin_user_roles" in Operations.statements[-1]
