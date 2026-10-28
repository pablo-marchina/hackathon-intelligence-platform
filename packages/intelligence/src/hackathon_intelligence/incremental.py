"""Opt-in canonicalization of durable, authorized source-event snapshots.

No network access: the source adapter owns fetching and policy. A source record
is written atomically with its reference; this module only consumes that record
under PostgreSQL locks and updates canonical data with per-field provenance.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import Connection, text

from .structured_projection import PROJECTION_VERSION, project_structured_metadata

VERSION = "incremental-canonical-v1"


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return re.sub(
        r"[^a-z0-9]+", " ", "".join(c for c in value if not unicodedata.combining(c)).casefold()
    ).strip()


def parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return (
            date.replace(tzinfo=UTC)
            if date.tzinfo is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip())
            else (date if date.tzinfo else None)
        )
    except ValueError:
        return None


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value.strip())
        if (
            url.scheme.lower() in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
        ):
            return value.strip()
    except ValueError:
        pass
    return None


def event_kind(name: str, tags: list[str]) -> str | None:
    value = normalized_name(name + " " + " ".join(tags))
    for needle, kind in (
        ("hackathon", "hackathon"),
        ("buildathon", "buildathon"),
        ("datathon", "datathon"),
        ("game jam", "game_jam"),
        ("ideathon", "ideathon"),
        ("glamhack", "hackathon"),
        ("hackdays", "hackathon"),
        ("hack day", "hackathon"),
    ):
        if needle in value:
            return kind
    return None  # Avoid promoting generic conferences, programs and meetups.


def normalize_format(value: Any) -> str:
    word = normalized_name(str(value or ""))
    if word in {"both", "online and in person", "online e presencial"} or (
        "hybrid" in word or "hibrid" in word
    ):
        return "HYBRID"
    if any(w in word for w in ("online", "virtual", "remote")):
        return "ONLINE"
    if any(w in word for w in ("in person", "presencial", "offline")):
        return "IN_PERSON"
    return "UNKNOWN"


@dataclass(frozen=True)
class EventCandidate:
    name: str
    name_key: str
    event_type: str
    official_url: str | None
    start_at: datetime | None
    end_at: datetime | None
    event_format: str
    fields: dict[str, tuple[Any, Any, str, str]]  # original, normalized, method, locator
    city: str | None
    country: str | None


def prepare_event(
    payload: dict[str, Any], *, source_key: str | None = None
) -> EventCandidate | None:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    tags = (
        [str(x) for x in payload.get("tags", []) if isinstance(x, str)]
        if isinstance(payload.get("tags"), list)
        else []
    )
    kind = event_kind(name, tags)
    # The existing specialized Hack Club Hackathons feed was validated as a
    # hackathon-only category; its event names often end in "Hacks". Do not
    # silently trust arbitrary new source names as specialized categories.
    if kind is None and source_key in {"hackclub_hackathons", "hackalendar"}:
        # Both publishers explicitly curate a hackathon-only public feed.
        # Never extend this exemption to general event calendars.
        kind = "hackathon"
    # Only this publisher's explicitly verified recurring hackathon series
    # gets an exception; its Data Toolbox and workshops remain unclassified.
    if (
        kind is None
        and source_key == "dribdat_hack4socialgood"
        and re.fullmatch(r"hack4socialgood(?: 20\d{2})?", normalized_name(name))
    ):
        kind = "hackathon"
    # The publisher calls Graffathon 2022 a two-day hackathon. The 2026
    # Echtzeit Graffathon is described as a workshop: no exception.
    if (
        kind is None
        and source_key == "dribdat_echtzeit"
        and normalized_name(name) == "graffathon 2022"
    ):
        kind = "hackathon"
    if kind is None:
        return None
    start, end = parse_date(payload.get("starts_at_raw")), parse_date(payload.get("ends_at_raw"))
    if start and end and end < start:
        end = None
    official_url = safe_url(payload.get("official_url"))
    event_format = normalize_format(payload.get("format_raw"))
    fields: dict[str, tuple[Any, Any, str, str]] = {}
    direct = {
        "name": name.strip(),
        "official_url": official_url,
        "start_at": start.isoformat() if start else None,
        "end_at": end.isoformat() if end else None,
        "format": event_format if event_format != "UNKNOWN" else None,
        "city": payload.get("city") if isinstance(payload.get("city"), str) else None,
        "country": payload.get("country") if isinstance(payload.get("country"), str) else None,
    }
    for key, value in direct.items():
        if value is not None and value != "":
            fields[key] = (value, value, "API", "source_event_ref.payload." + key)
    # Eligibility is a *published statement*, not an inferred student,
    # residency, age or team-size rule. A source can be explicit about
    # eligibility without providing enough evidence for any typed boolean.
    raw_eligibility = payload.get("eligibility_raw")
    if isinstance(raw_eligibility, str) and raw_eligibility.strip():
        eligibility_text = raw_eligibility.strip()[:2000]
        fields["participation_requirements"] = (
            raw_eligibility,
            eligibility_text,
            "API",
            "source_event_ref.payload.eligibility_raw",
        )
    if source_key in {
        "hackclub_programs",
        "dribdat_glamhack",
        "dribdat_imprs_astro",
        "dribdat_opendata_ch",
        "dribdat_hack4socialgood",
        "dribdat_echtzeit",
    }:
        # Approved official APIs publish factual descriptions
        # and lifecycle statuses. These stay source-evidenced, not inferred
        # deadlines or permission to scrape the linked event website.
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            description = metadata.get("description")
            if isinstance(description, str) and description.strip():
                text_value = description.strip()[:2000]
                fields["short_description"] = (
                    description,
                    text_value,
                    "API",
                    "source_event_ref.payload.metadata.description",
                )
            status = metadata.get("status")
            if status in {"upcoming", "ongoing", "ended"}:
                fields["source_event_status"] = (
                    status,
                    status,
                    "API",
                    "source_event_ref.payload.metadata.status",
                )
    if source_key == "hackalendar":
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            # The publisher exposes these as explicit facts. No guessing about
            # unprovided eligibility, prizes, registration opening or timezone.
            registration = safe_url(metadata.get("registration_url"))
            if registration:
                fields["registration_url"] = (
                    registration,
                    registration,
                    "API",
                    "source_event_ref.payload.metadata.registration_url",
                )
            free = metadata.get("is_free")
            if type(free) is bool:
                fields["is_free"] = (
                    free,
                    free,
                    "API",
                    "source_event_ref.payload.metadata.is_free",
                )
            description = metadata.get("description")
            if isinstance(description, str) and description.strip():
                excerpt = description.strip()[:2000]
                fields["short_description"] = (
                    description,
                    excerpt,
                    "API",
                    "source_event_ref.payload.metadata.description",
                )
            raw_deadline = metadata.get("registration_deadline_raw")
            if isinstance(raw_deadline, str) and len(raw_deadline) > 10:
                parsed_deadline = parse_date(raw_deadline)
                if parsed_deadline is not None:
                    deadline = parsed_deadline.isoformat()
                    fields["registration_close_at"] = (
                        raw_deadline,
                        deadline,
                        "API",
                        "source_event_ref.payload.metadata.registration_deadline_raw",
                    )
            # An all-day, date-only deadline is retained as raw metadata
            # without inventing 00:00 UTC or the organiser's local timezone.
            prize_details = metadata.get("prize_pool_raw")
            if isinstance(prize_details, str) and prize_details.strip():
                fields["prize_details_text"] = (
                    prize_details,
                    prize_details.strip()[:2000],
                    "API",
                    "source_event_ref.payload.metadata.prize_pool_raw",
                )
    for evidence in project_structured_metadata(payload):
        # A dedicated structured field wins over any less reliable generic field.
        fields[evidence.field_path] = (
            evidence.original,
            evidence.normalized,
            "JSON_LD",
            evidence.evidence_locator,
        )
    return EventCandidate(
        name=name.strip(),
        name_key=normalized_name(name),
        event_type=kind,
        official_url=official_url,
        start_at=start,
        end_at=end,
        event_format=event_format,
        fields=fields,
        city=direct["city"],
        country=direct["country"],
    )


class CanonicalProcessor:
    """Process at most one pending record, using the caller's transaction.

    Call once per transaction. Two workers cannot consume the same staged row.
    No fuzzy auto-merge: only an exact official URL + same name + close start
    dates can reconcile independent sources. Conflicts remain as evidence.
    """

    def __init__(self, connection: Connection, *, source_key: str | None = None) -> None:
        self.connection = connection
        self.source_key = source_key
        self.last_outcome: str | None = None

    def _execute(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        return self.connection.execute(text(statement), params or {})

    def process_one(self) -> str | None:
        self.last_outcome = None
        row = (
            self._execute(
                """
            SELECT p.source_event_ref_id::text AS ref_id, p.source_id::text AS source_id,
                   p.source_key, p.raw_document_id::text AS raw_document_id,
                   p.payload, p.content_hash, p.staged_crawl_run_id::text AS crawl_run_id,
                   r.hackathon_id::text AS hackathon_id
              FROM source_event_payloads p
              JOIN source_event_refs r ON r.id = p.source_event_ref_id
              JOIN sources s ON s.id = p.source_id
             WHERE p.processed_hash IS DISTINCT FROM p.content_hash
               AND p.last_error IS NULL
               AND (CAST(:source_key AS text) IS NULL OR p.source_key = CAST(:source_key AS text))
               AND s.active AND s.permission_status = 'ALLOWED'
               AND s.discovery_enabled AND s.detail_extraction_enabled
               AND s.ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
             ORDER BY p.observed_at ASC
             LIMIT 1 FOR UPDATE OF p SKIP LOCKED
        """,
                {"source_key": self.source_key},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        ref_id, source_id = row["ref_id"], row["source_id"]
        # Lock the source for the full transaction, conflicting with pause/revoke.
        authorized = self._execute(
            """
            SELECT active AND permission_status='ALLOWED'
                   AND discovery_enabled AND detail_extraction_enabled
                   AND ingestion_mode NOT IN ('BLOCKED','DISCOVERY_ONLY','MANUAL')
              FROM sources WHERE id=CAST(:id AS uuid) FOR SHARE
        """,
            {"id": source_id},
        ).scalar_one_or_none()
        if authorized is not True:
            # Do not mark as processed. May be resumed only when explicitly approved.
            return "SKIPPED_UNAUTHORIZED"
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        event = prepare_event(payload, source_key=row["source_key"])
        if event is None:
            self._mark(row, rejection="not_hackathon_like_or_missing_name")
            self._record_outcome(row, outcome="REJECTED", hackathon_id=None)
            return "REJECTED"
        # Serialise contenders for the same official URL, even across sources.
        if event.official_url:
            self._execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(:url, 0))",
                {"url": event.official_url},
            )
        previous_hackathon_id = row["hackathon_id"]
        hackathon_id = previous_hackathon_id
        if hackathon_id:
            valid = self._execute(
                """
                SELECT id::text FROM hackathons WHERE id=CAST(:id AS uuid)
                AND is_active AND merged_into_id IS NULL FOR UPDATE
            """,
                {"id": hackathon_id},
            ).scalar_one_or_none()
            hackathon_id = valid
        if not hackathon_id and event.official_url and event.start_at:
            possible = (
                self._execute(
                    """
                SELECT id::text FROM hackathons
                 WHERE is_active AND merged_into_id IS NULL
                   AND official_url=:url AND normalized_name=:name
                   AND start_at IS NOT NULL
                   AND ABS(EXTRACT(EPOCH FROM (start_at - CAST(:start AS timestamptz)))) <= 86400
                 ORDER BY id FOR UPDATE
                 LIMIT 2
            """,
                    {"url": event.official_url, "name": event.name_key, "start": event.start_at},
                )
                .scalars()
                .all()
            )
            if len(possible) == 1:
                hackathon_id = possible[0]
        created = not hackathon_id
        if created:
            slug = (event.name_key.replace(" ", "-")[:70] or "hackathon") + "-" + uuid4().hex[:12]
            hackathon_id = self._execute(
                """
                INSERT INTO hackathons
                    (slug, name, normalized_name, event_type, status, format,
                     official_url, start_at, end_at, is_active, source_count)
                VALUES (:slug, :name, :normalized, CAST(:kind AS event_type),
                        'UNKNOWN', CAST(:format AS event_format), :official_url,
                        :start_at, :end_at, true, 1)
                RETURNING id::text
            """,
                {
                    "slug": slug,
                    "name": event.name,
                    "normalized": event.name_key,
                    "kind": event.event_type,
                    "format": event.event_format,
                    "official_url": event.official_url,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                },
            ).scalar_one()
        self._execute(
            """
            UPDATE source_event_refs SET hackathon_id=CAST(:event AS uuid)
             WHERE id=CAST(:ref AS uuid)
        """,
            {"event": hackathon_id, "ref": ref_id},
        )
        selected_dates: dict[str, Any] = {}
        for field, (original, normalized, method, locator) in event.fields.items():
            observation_id = self._execute(
                """
                INSERT INTO source_observations
                    (hackathon_id, source_event_ref_id, source_id, raw_document_id,
                     field_path, value_json, normalized_value_json,
                     extraction_method, extraction_confidence, evidence_locator,
                     method_version, observed_at)
                VALUES (CAST(:hackathon_id AS uuid), CAST(:ref_id AS uuid),
                        CAST(:source_id AS uuid), CAST(:raw_document_id AS uuid),
                        :field, CAST(:original AS jsonb), CAST(:normalized AS jsonb),
                        CAST(:method AS extraction_method), 0.95, :locator,
                        :version, now())
                RETURNING id
            """,
                {
                    "hackathon_id": hackathon_id,
                    "ref_id": ref_id,
                    "source_id": source_id,
                    "raw_document_id": row["raw_document_id"],
                    "field": field,
                    "original": json.dumps(original, ensure_ascii=False),
                    "normalized": json.dumps(normalized, ensure_ascii=False),
                    "method": method,
                    "locator": locator,
                    "version": PROJECTION_VERSION if method == "JSON_LD" else VERSION,
                },
            ).scalar_one()
            current = (
                self._execute(
                    """
                SELECT cf.id, so.source_event_ref_id::text AS origin_ref
                  FROM canonical_field_selections cf
                  JOIN source_observations so ON so.id=cf.selected_observation_id
                 WHERE cf.hackathon_id=CAST(:event AS uuid)
                   AND cf.field_path=:field AND cf.active FOR UPDATE OF cf
            """,
                    {"event": hackathon_id, "field": field},
                )
                .mappings()
                .first()
            )
            # Preserve a canonical selection from another source. It is evidence
            # of a potential conflict, not a license to overwrite that source.
            if current and current["origin_ref"] != ref_id:
                continue
            if current:
                self._execute(
                    "UPDATE canonical_field_selections SET active=false, valid_until=now() WHERE id=:id",
                    {"id": current["id"]},
                )
            self._execute(
                """
                INSERT INTO canonical_field_selections
                    (hackathon_id, field_path, selected_observation_id, policy_version, score, active)
                VALUES (CAST(:event AS uuid), :field, :observation, :version, 0.95, true)
            """,
                {
                    "event": hackathon_id,
                    "field": field,
                    "observation": observation_id,
                    "version": VERSION,
                },
            )
            if field in {"start_at", "end_at"}:
                selected_dates[field] = normalized
            else:
                self._apply_field(hackathon_id, field, normalized)
        if selected_dates:
            self._execute(
                """
                UPDATE hackathons
                   SET start_at=COALESCE(CAST(:start_at AS timestamptz), start_at),
                       end_at=COALESCE(CAST(:end_at AS timestamptz), end_at)
                 WHERE id=CAST(:id AS uuid)
                   AND (COALESCE(CAST(:start_at AS timestamptz), start_at) IS NULL
                        OR COALESCE(CAST(:end_at AS timestamptz), end_at) IS NULL
                        OR COALESCE(CAST(:end_at AS timestamptz), end_at) >=
                           COALESCE(CAST(:start_at AS timestamptz), start_at))
            """,
                {
                    "id": hackathon_id,
                    "start_at": selected_dates.get("start_at"),
                    "end_at": selected_dates.get("end_at"),
                },
            )
        self._sync_location(hackathon_id, event)
        self._execute(
            """
            UPDATE hackathons SET source_count=(
                SELECT COUNT(DISTINCT source_id)::integer FROM source_event_refs
                WHERE hackathon_id=CAST(:event AS uuid) AND active AND source_id IS NOT NULL
            ), last_seen_at=now(), last_verified_at=now(),
               status=CASE
                   WHEN status IN ('CANCELLED'::event_status, 'POSTPONED'::event_status) THEN status
                   WHEN end_at < clock_timestamp() THEN 'COMPLETED'::event_status
                   WHEN start_at <= clock_timestamp() AND end_at >= clock_timestamp()
                        THEN 'ONGOING'::event_status
                   WHEN start_at > clock_timestamp() THEN 'UPCOMING'::event_status
                   ELSE 'UNKNOWN'::event_status END
            WHERE id=CAST(:event AS uuid)
        """,
            {"event": hackathon_id},
        )
        self._mark(row)
        result = "CREATED" if created else ("UPDATED" if previous_hackathon_id else "LINKED")
        self._record_outcome(row, outcome=result, hackathon_id=hackathon_id)
        # Keep return values backward-compatible with existing worker reports.
        return "CREATED" if created else "UPDATED"

    def _record_outcome(self, row: Any, *, outcome: str, hackathon_id: str | None) -> None:
        """Append exactly one durable outcome per source-record version.

        Shares the source-row lock and transaction with canonical creation and
        _mark(): a rollback cannot leave a false discovery count behind. The
        crawl ID comes from the original staging run, never an inferred date.
        """
        self._execute(
            """
            INSERT INTO canonical_processing_outcomes
                (source_event_ref_id, source_id, crawl_run_id, content_hash, outcome, hackathon_id)
            VALUES (CAST(:ref AS uuid), CAST(:source AS uuid), CAST(:run AS uuid),
                    :digest, :outcome, CAST(:event AS uuid))
            ON CONFLICT (source_event_ref_id, content_hash) DO NOTHING
        """,
            {
                "ref": row["ref_id"],
                "source": row["source_id"],
                "run": row["crawl_run_id"],
                "digest": row["content_hash"],
                "outcome": outcome,
                "event": hackathon_id,
            },
        )
        self.last_outcome = outcome

    def _apply_field(self, event_id: str, field: str, value: Any) -> None:
        # Allowlist: relationship arrays, source-specific statuses and unverified
        # offer expiration are stored as observations, NOT guessed catalog columns.
        plain = {
            "name": "name",
            "official_url": "official_url",
            "short_description": "short_description",
            "registration_url": "registration_url",
        }
        if field in plain and isinstance(value, str):
            self._execute(
                f"UPDATE hackathons SET {plain[field]}=:value WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
            if field == "name":
                self._execute(
                    "UPDATE hackathons SET normalized_name=:name WHERE id=CAST(:id AS uuid)",
                    {"name": normalized_name(value), "id": event_id},
                )
        elif field == "format":
            self._execute(
                "UPDATE hackathons SET format=CAST(:value AS event_format) WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
        elif field == "registration_close_at" and isinstance(value, str):
            self._execute(
                "UPDATE hackathons SET registration_close_at=CAST(:value AS timestamptz) "
                "WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
        elif field == "is_free" and type(value) is bool:
            self._execute(
                "UPDATE hackathons SET is_free=:value WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
        elif field == "participation_fee":
            self._execute(
                "UPDATE hackathons SET participation_fee=CAST(:value AS numeric) WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
        elif field == "participation_fee_currency":
            self._execute(
                "UPDATE hackathons SET participation_fee_currency=:value WHERE id=CAST(:id AS uuid)",
                {"value": value, "id": event_id},
            )
        elif field == "participation_requirements" and isinstance(value, str):
            self._execute(
                """
                INSERT INTO eligibility_rules (hackathon_id, eligibility_raw_text)
                VALUES (CAST(:id AS uuid), :value)
                ON CONFLICT (hackathon_id) DO UPDATE
                SET eligibility_raw_text=EXCLUDED.eligibility_raw_text,
                    updated_at=now()
                """,
                {"id": event_id, "value": value},
            )

    def _sync_location(self, event_id: str, event: EventCandidate) -> None:
        if not event.city and not event.country:
            return
        # Preserve an existing curated location rather than overwrite it.
        current = self._execute(
            "SELECT primary_location_id FROM hackathons WHERE id=CAST(:id AS uuid)",
            {"id": event_id},
        ).scalar_one_or_none()
        if current:
            return
        location_id = self._execute(
            """
            INSERT INTO locations (city, country, normalized_key)
            VALUES (:city, :country, :key) RETURNING id::text
        """,
            {
                "city": event.city,
                "country": event.country,
                "key": normalized_name((event.city or "") + " " + (event.country or "")),
            },
        ).scalar_one()
        self._execute(
            "UPDATE hackathons SET primary_location_id=CAST(:loc AS uuid) WHERE id=CAST(:id AS uuid)",
            {"id": event_id, "loc": location_id},
        )
        self._execute(
            """
            INSERT INTO hackathon_locations (hackathon_id, location_id, is_primary)
            VALUES (CAST(:event AS uuid), CAST(:location AS uuid), true)
            ON CONFLICT DO NOTHING
        """,
            {"event": event_id, "location": location_id},
        )

    def _mark(self, row: Any, rejection: str | None = None) -> None:
        self._execute(
            """
            UPDATE source_event_payloads
               SET processed_hash=content_hash, processed_at=now(), last_error=:rejection
             WHERE source_event_ref_id=CAST(:ref AS uuid) AND content_hash=:digest
        """,
            {"ref": row["ref_id"], "digest": row["content_hash"], "rejection": rejection},
        )
