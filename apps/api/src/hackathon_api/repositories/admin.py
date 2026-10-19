from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text


class AdminRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def review_queue(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            text(
                """
                SELECT id, queue_type, entity_type, entity_id, severity::text AS severity,
                       state::text AS state, payload, created_at, assigned_to_user_id
                  FROM review_queue_items
                 WHERE state IN ('OPEN','IN_REVIEW')
                 ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                           WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                          created_at ASC
                 LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return [dict(row) for row in rows]

    def source_health(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            text(
                """
                SELECT s.id, s.name, s.domain, s.health::text AS health, s.priority,
                       s.last_attempt_at, s.last_success_at, s.last_error_at, s.active,
                       st.next_due_at, st.recrawl_priority, st.interval_seconds,
                       st.plan_reason, st.lease_until
                  FROM sources s
                  LEFT JOIN source_operational_state st ON st.source_id = s.id
                 ORDER BY s.active DESC, s.priority DESC, s.name ASC
                """
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    def latest_quality(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            text(
                """
                SELECT DISTINCT ON (scope_type, scope_id)
                       scope_type, scope_id, measured_at, overall_score,
                       identity_score, dates_score, eligibility_score, prize_score,
                       location_score, sources_score, freshness_score, consistency_score,
                       metrics
                  FROM quality_snapshots
                 ORDER BY scope_type, scope_id, measured_at DESC
                 LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return [dict(row) for row in rows]

    def _audit(
        self,
        *,
        principal: Any,
        action: str,
        entity_type: str,
        entity_id: str,
        reason: str,
        before: dict[str, Any],
        after: dict[str, Any],
        request_id: str | None,
    ) -> None:
        import json

        self.connection.execute(
            text(
                """
                INSERT INTO admin_audit_log (
                    actor_user_id, actor_kind, action, entity_type, entity_id,
                    reason, before_json, after_json, request_id
                ) VALUES (
                    CAST(:actor_user_id AS uuid), :actor_kind, :action, :entity_type,
                    :entity_id, :reason, CAST(:before AS jsonb), CAST(:after AS jsonb), :request_id
                )
                """
            ),
            {
                "actor_user_id": principal.actor_user_id,
                "actor_kind": principal.actor_kind,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "reason": reason,
                "before": json.dumps(before),
                "after": json.dumps(after),
                "request_id": request_id,
            },
        )

    def change_source_active(
        self,
        *,
        source_id: str,
        active: bool,
        reason: str,
        principal: Any,
        request_id: str | None,
    ) -> dict[str, Any] | None:
        """Lock the target, change its effective state and audit in one DB transaction."""
        row = self.connection.execute(
            text("SELECT id::text AS id, active FROM sources WHERE id = CAST(:id AS uuid) FOR UPDATE"),
            {"id": source_id},
        ).mappings().first()
        if row is None:
            return None
        old_active = bool(row["active"])
        if old_active != active:
            self.connection.execute(
                text("UPDATE sources SET active = :active, updated_at = now() WHERE id = CAST(:id AS uuid)"),
                {"id": source_id, "active": active},
            )
            self._audit(
                principal=principal,
                action="SOURCE_RESUME" if active else "SOURCE_PAUSE",
                entity_type="source",
                entity_id=source_id,
                reason=reason,
                before={"active": old_active},
                after={"active": active},
                request_id=request_id,
            )
        return {"id": source_id, "active": active, "changed": old_active != active}

    def transition_review(
        self,
        *,
        review_id: str,
        target_state: str,
        assigned_to_user_id: str | None,
        reason: str,
        principal: Any,
        request_id: str | None,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            text(
                """
                SELECT id::text AS id, state::text AS state,
                       assigned_to_user_id::text AS assigned_to_user_id
                FROM review_queue_items WHERE id = CAST(:id AS uuid) FOR UPDATE
                """
            ),
            {"id": review_id},
        ).mappings().first()
        if row is None:
            return None
        before = {"state": str(row["state"]), "assigned_to_user_id": row["assigned_to_user_id"]}
        transitions = {
            "OPEN": {"IN_REVIEW", "RESOLVED", "DISMISSED"},
            "IN_REVIEW": {"RESOLVED", "DISMISSED"},
        }
        if target_state not in transitions.get(before["state"], set()):
            raise ValueError("review state transition not permitted")
        next_assignee = assigned_to_user_id or before["assigned_to_user_id"]
        if assigned_to_user_id is not None:
            role = self.connection.execute(
                text("SELECT role FROM admin_user_roles WHERE user_id = CAST(:id AS uuid)"),
                {"id": assigned_to_user_id},
            ).scalar_one_or_none()
            if role not in {"OPERATOR", "ADMIN"}:
                raise ValueError("assignee must have OPERATOR or ADMIN role")
        self.connection.execute(
            text(
                """
                UPDATE review_queue_items
                   SET state = CAST(:state AS review_state),
                       assigned_to_user_id = CAST(:assignee AS uuid),
                       resolved_at = CASE WHEN :state IN ('RESOLVED', 'DISMISSED')
                                          THEN now() ELSE NULL END
                 WHERE id = CAST(:id AS uuid)
                """
            ),
            {"state": target_state, "assignee": next_assignee, "id": review_id},
        )
        after = {"state": target_state, "assigned_to_user_id": next_assignee}
        self._audit(
            principal=principal,
            action="REVIEW_TRANSITION",
            entity_type="review_queue_item",
            entity_id=review_id,
            reason=reason,
            before=before,
            after=after,
            request_id=request_id,
        )
        return {"id": review_id, **after}

    def audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            text(
                """
                SELECT id, actor_user_id::text AS actor_user_id, actor_kind,
                       action, entity_type, entity_id, reason,
                       before_json, after_json, created_at, request_id
                  FROM admin_audit_log ORDER BY id DESC LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return [dict(row) for row in rows]

    def audit_reindex(
        self, *, count: int, principal: Any, reason: str, request_id: str | None
    ) -> None:
        self._audit(
            principal=principal,
            action="SEARCH_REINDEX",
            entity_type="search_index",
            entity_id="hackathon_search_documents",
            reason=reason,
            before={},
            after={"indexed": count},
            request_id=request_id,
        )
