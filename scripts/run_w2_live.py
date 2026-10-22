#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

if __package__:
    from scripts._local_packages import add_repo_packages
else:
    from _local_packages import add_repo_packages
add_repo_packages(Path(__file__).resolve().parents[1])
from hackathon_intelligence.structured_projection import (  # noqa: E402
    PROJECTION_VERSION,
    project_structured_metadata,
)

PIPELINE_VERSION = "w2-live-v2"
RECONCILE_VERSION = "reconcile-v1"
DEDUP_VERSION = "dedup-live-v2"
CLASSIFIER_VERSION = "hackathon-like-det-v1"
ALLOWED_SOURCES = {
    "hackclub_hackathons",
    "hackclub_programs",
    "hackclub_community_events",
    "agenda_tech_brasil",
}
AUTHORITY = {
    "hackclub_hackathons": 0.90,
    "hackclub_programs": 0.95,
    "hackclub_community_events": 0.90,
    "agenda_tech_brasil": 0.65,
}


def now_iso():
    return datetime.now(UTC).isoformat()


def norm_text(v):
    if v is None:
        return None
    s = unicodedata.normalize("NFKD", str(v))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_url(v):
    if not v or not str(v).startswith(("http://", "https://")):
        return None
    p = urlsplit(str(v).strip())
    host = (p.hostname or "").lower()
    scheme = p.scheme.lower()
    port = (
        f":{p.port}"
        if p.port
        and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443))
        else ""
    )
    path = re.sub(r"/+", "/", p.path or "/").rstrip("/") or "/"
    qs = [
        (k, val)
        for k, val in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((scheme, host + port, path, urlencode(sorted(qs)), ""))


def parse_dt(v):
    if not v:
        return None
    s = str(v).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime.fromisoformat(s + "T00:00:00+00:00")
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def iso(dt):
    return dt.astimezone(UTC).isoformat() if dt else None


def event_type(name, tags):
    t = norm_text(" ".join([name or ""] + [str(x) for x in tags or []])) or ""
    for needle, typ in [
        ("buildathon", "buildathon"),
        ("datathon", "datathon"),
        ("game jam", "game_jam"),
        ("ideathon", "ideathon"),
        ("hackathon", "hackathon"),
        ("capture the flag", "security_ctf"),
        (" ctf ", "security_ctf"),
    ]:
        if needle.strip() in t:
            return typ
    return "hackathon"


def classify(source, payload):
    if source == "hackclub_hackathons":
        return True, 1.0, "specialized_hackathon_source"
    name = str(payload.get("name") or "")
    tags = payload.get("tags") or []
    meta = payload.get("metadata") or {}
    fmt = str(payload.get("format_raw") or "")
    text = (
        norm_text(
            " ".join(
                [
                    name,
                    fmt,
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False),
                ]
            )
        )
        or ""
    )
    score = 0.05
    strong = ["hackathon", "buildathon", "datathon", "game jam", "ideathon", "hack day", "hackday"]
    medium = [
        "coding challenge",
        "innovation challenge",
        "ai challenge",
        "data challenge",
        "capture the flag",
    ]
    negative = [
        "workshop",
        "webinar",
        "conference",
        "summit",
        "meetup",
        "expo",
        "career fair",
        "office hours",
        "club meeting",
    ]
    if any(x in text for x in strong):
        score = max(score, 0.95)
    elif any(x in text for x in medium):
        score = max(score, 0.78)
    if source == "hackclub_programs":
        score += 0.08
    if any(x in text for x in negative):
        score -= 0.25
    score = max(0, min(score, 1))
    return score >= 0.75, score, "keyword_precision_gate"


def normalize_format(raw):
    t = norm_text(raw or "") or ""
    if "hybrid" in t or "both" in t:
        return "HYBRID"
    if "online" in t or "virtual" in t or "remote" in t:
        return "ONLINE"
    if "in person" in t or "inperson" in t or "presencial" in t or "offline" in t:
        return "IN_PERSON"
    return "UNKNOWN"


def status_for(start, end):
    now = datetime.now(UTC)
    if end and end < now:
        return "COMPLETED"
    if start and end and start <= now <= end:
        return "ONGOING"
    if start and start > now:
        return "UPCOMING"
    return "UNKNOWN"


def title_sim(a, b):
    return SequenceMatcher(None, norm_text(a) or "", norm_text(b) or "").ratio()


def date_sim(a, b):
    if not a or not b:
        return None
    days = abs((a - b).total_seconds()) / 86400
    return max(0.0, 1 - min(days, 30) / 30)


def loc_sim(a, b):
    if not a or not b:
        return None
    return SequenceMatcher(None, norm_text(a) or "", norm_text(b) or "").ratio()


def dedup_score(a, b):
    if (
        a["source_key"] == b["source_key"]
        and a.get("source_event_id")
        and a.get("source_event_id") == b.get("source_event_id")
    ):
        return 0.999, "same_source_external_id", {"exact_external_id": 1.0}
    if a["official_url_n"] and a["official_url_n"] == b["official_url_n"]:
        if a["start_at"] and b["start_at"]:
            days = abs((a["start_at"] - b["start_at"]).total_seconds()) / 86400
            if days <= 14:
                return (
                    0.999,
                    "exact_official_url_compatible_date",
                    {"exact_official_url": 1.0, "date_distance_days": round(days, 3)},
                )
            if days <= 45:
                return (
                    0.90,
                    "reused_official_url_near_date",
                    {"exact_official_url": 1.0, "date_distance_days": round(days, 3)},
                )
            return (
                0.70,
                "reused_official_url_different_edition",
                {"exact_official_url": 1.0, "date_distance_days": round(days, 3)},
            )
        return 0.90, "exact_official_url_missing_date", {"exact_official_url": 1.0}
    ts = title_sim(a["name"], b["name"])
    ds = date_sim(a["start_at"], b["start_at"])
    ls = loc_sim(a.get("city") or a.get("country"), b.get("city") or b.get("country"))
    if ts < 0.72:
        return None, None, None
    if ds is not None and ds < 0.45 and ts < 0.94:
        return None, None, None
    feats = [
        ("title_similarity", ts, 0.55),
        ("date_similarity", ds, 0.30),
        ("location_similarity", ls, 0.15),
    ]
    avail = [x for x in feats if x[1] is not None]
    score = sum(v * w for _, v, w in avail) / sum(w for _, _, w in avail)
    reason = "title_date_block" if ds is not None else "high_title_similarity"
    return score, reason, {k: round(v, 6) if v is not None else None for k, v, _ in feats}


class DSU:
    def __init__(self, ids):
        self.p = {x: x for x in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


class Gate:
    def __init__(self):
        self.url = os.environ["W2_GATE_URL"]
        self.token = os.environ["W2_GATE_TOKEN"]
        self.c = httpx.AsyncClient(
            timeout=60, headers={"x-w2-gate-token": self.token, "content-type": "application/json"}
        )

    async def close(self):
        await self.c.aclose()

    async def call(self, action, **kwargs):
        r = await self.c.post(self.url, json={"action": action, **kwargs})
        r.raise_for_status()
        return r.json()

    async def read_all(self):
        out = []
        off = 0
        while True:
            d = await self.call("list_refs", offset=off, limit=100)
            rows = d.get("rows", [])
            out.extend(rows)
            if len(rows) < 100:
                break
            off += len(rows)
        return out

    async def insert(self, table, rows, batch=100):
        results = []
        for i in range(0, len(rows), batch):
            d = await self.call("batch_insert", table=table, rows=rows[i : i + batch])
            results.extend(d.get("rows", []))
        return results

    async def update(self, table, rows, batch=100):
        for i in range(0, len(rows), batch):
            await self.call("batch_update", table=table, rows=rows[i : i + batch])


async def main():
    gate = Gate()
    run_id = str(uuid4())
    started = now_iso()
    try:
        refs = await gate.read_all()
        refs = [r for r in refs if r.get("source_key") in ALLOWED_SOURCES]
        print(json.dumps({"stage": "input", "refs": len(refs)}))
        await gate.insert(
            "w2_pipeline_runs",
            [{"id": run_id, "status": "RUNNING", "started_at": started, "input_refs": len(refs)}],
        )
        candidates = []
        rejected = []
        observation_rows = []
        for r in refs:
            p = r.get("payload") or {}
            source = r["source_key"]
            accepted, cls_score, cls_reason = classify(source, p)
            name = str(p.get("name") or r.get("name") or "").strip()
            if not name:
                accepted = False
                cls_reason = "missing_name"
                cls_score = 0
            start = parse_dt(p.get("starts_at_raw"))
            end = parse_dt(p.get("ends_at_raw"))
            if start and end and end < start:
                end = None
            off = norm_url(p.get("official_url") or r.get("official_url"))
            surl = norm_url(p.get("source_url") or r.get("source_url"))
            tags = [str(x) for x in p.get("tags", []) if x]
            provisional_id = str(uuid4()) if accepted else None
            c = {
                "ref_id": r["id"],
                "raw_document_id": r.get("raw_document_id"),
                "source_key": source,
                "source_event_id": p.get("source_event_id") or r.get("source_event_id"),
                "name": name,
                "normalized_name": norm_text(name),
                "official_url": off,
                "official_url_n": off,
                "source_url": surl,
                "start_at": start,
                "end_at": end,
                "format": normalize_format(p.get("format_raw")),
                "city": p.get("city"),
                "state_region": p.get("state_region"),
                "country": p.get("country"),
                "country_code": p.get("country_code"),
                "eligibility_raw": p.get("eligibility_raw"),
                "tags": tags,
                "event_type": event_type(name, tags),
                "provisional_id": provisional_id,
                "classification_score": cls_score,
                "classification_reason": cls_reason,
            }
            if accepted:
                candidates.append(c)
            else:
                rejected.append(
                    {
                        "pipeline_run_id": run_id,
                        "source_event_ref_id": r["id"],
                        "source_key": source,
                        "reason": cls_reason,
                        "classifier_version": CLASSIFIER_VERSION,
                        "score": cls_score,
                    }
                )
            fields = {
                "name": name,
                "normalized_name": c["normalized_name"],
                "official_url": off,
                "source_url": surl,
                "start_at": iso(start),
                "end_at": iso(end),
                "format": c["format"],
                "city": c["city"],
                "state_region": c["state_region"],
                "country": c["country"],
                "country_code": c["country_code"],
                "eligibility": c["eligibility_raw"],
                "tags": tags,
                "event_type": c["event_type"],
                "is_hackathon_like": accepted,
                "classification_score": cls_score,
            }
            for fp, val in fields.items():
                if val is None or val == []:
                    continue
                observation_rows.append(
                    {
                        "pipeline_run_id": run_id,
                        "hackathon_id": provisional_id,
                        "source_event_ref_id": r["id"],
                        "raw_document_id": r.get("raw_document_id"),
                        "source_key": source,
                        "field_path": fp,
                        "value_json": val,
                        "normalized_value_json": norm_text(val)
                        if isinstance(val, str)
                        and fp in {"name", "city", "state_region", "country", "eligibility"}
                        else val,
                        "extraction_method": "API"
                        if fp
                        not in {
                            "normalized_name",
                            "is_hackathon_like",
                            "classification_score",
                            "event_type",
                        }
                        else "DETERMINISTIC",
                        "extraction_confidence": 0.99
                        if fp not in {"is_hackathon_like", "classification_score", "event_type"}
                        else round(max(0.5, cls_score), 4),
                        "evidence_locator": "source_event_ref.payload",
                        "derived": fp
                        in {
                            "normalized_name",
                            "is_hackathon_like",
                            "classification_score",
                            "event_type",
                        },
                        "method_version": PIPELINE_VERSION,
                        "observed_at": started,
                    }
                )
            # Structured HTML evidence is stored separately from derived conclusions.
            # A missing or ambiguous field never becomes a guessed deadline, price or eligibility.
            for evidence in project_structured_metadata(p):
                observation_rows.append(
                    {
                        "pipeline_run_id": run_id,
                        "hackathon_id": provisional_id,
                        "source_event_ref_id": r["id"],
                        "raw_document_id": r.get("raw_document_id"),
                        "source_key": source,
                        "field_path": evidence.field_path,
                        "value_json": evidence.original,
                        "normalized_value_json": evidence.normalized,
                        "extraction_method": "JSON_LD",
                        "extraction_confidence": evidence.confidence,
                        "evidence_locator": evidence.evidence_locator,
                        "derived": False,
                        "method_version": PROJECTION_VERSION,
                        "observed_at": started,
                    }
                )
        print(
            json.dumps(
                {
                    "stage": "extract",
                    "observations": len(observation_rows),
                    "accepted": len(candidates),
                    "rejected": len(rejected),
                }
            )
        )
        # provisional hackathons
        prov_rows = []
        for c in candidates:
            suffix = c["provisional_id"].split("-")[0]
            slug = (c["normalized_name"].replace(" ", "-")[:70] or "event") + "-" + suffix
            prov_rows.append(
                {
                    "id": c["provisional_id"],
                    "slug": slug,
                    "name": c["name"],
                    "normalized_name": c["normalized_name"],
                    "event_type": c["event_type"],
                    "is_hackathon_like": True,
                    "status": status_for(c["start_at"], c["end_at"]),
                    "format": c["format"],
                    "city": c["city"],
                    "state_region": c["state_region"],
                    "country": c["country"],
                    "country_code": c["country_code"],
                    "official_url": c["official_url"],
                    "source_count": 1,
                    "start_at": iso(c["start_at"]),
                    "end_at": iso(c["end_at"]),
                    "first_seen_at": started,
                    "last_seen_at": started,
                    "last_verified_at": started,
                    "quality_score": None,
                    "is_active": True,
                    "merged_into_id": None,
                    "metadata": {
                        "source_key": c["source_key"],
                        "source_event_ref_id": c["ref_id"],
                        "pipeline_version": PIPELINE_VERSION,
                    },
                }
            )
        # dedup all accepted
        dsu = DSU([c["provisional_id"] for c in candidates])
        dedup_rows = []
        auto_pairs = []
        for i, a in enumerate(candidates):
            for b in candidates[i + 1 :]:
                score, reason, features = dedup_score(a, b)
                if score is None:
                    continue
                decision = (
                    "AUTO_MERGE" if score >= 0.97 else ("REVIEW" if score >= 0.80 else "SEPARATE")
                )
                if decision == "SEPARATE" and score < 0.76:
                    continue
                dedup_rows.append(
                    {
                        "id": str(uuid4()),
                        "pipeline_run_id": run_id,
                        "left_hackathon_id": a["provisional_id"],
                        "right_hackathon_id": b["provisional_id"],
                        "blocking_reason": reason,
                        "feature_json": features,
                        "score": round(score, 6),
                        "model_version": DEDUP_VERSION,
                        "decision": decision,
                        "review_state": "AUTO_RESOLVED"
                        if decision == "AUTO_MERGE"
                        else ("OPEN" if decision == "REVIEW" else "CLOSED"),
                        "created_at": started,
                    }
                )
                if decision == "AUTO_MERGE":
                    dsu.union(a["provisional_id"], b["provisional_id"])
                    auto_pairs.append((a, b, score, reason))
        groups = defaultdict(list)
        by_id = {c["provisional_id"]: c for c in candidates}
        for c in candidates:
            groups[dsu.find(c["provisional_id"])].append(c)
        # choose survivor per component by authority + completeness
        survivor_for = {}
        merge_rows = []

        def completeness(c):
            return sum(
                x is not None and x != "UNKNOWN"
                for x in [
                    c["official_url"],
                    c["start_at"],
                    c["end_at"],
                    c["city"],
                    c["country"],
                    c["format"],
                ]
            )

        for members in groups.values():
            survivor = max(
                members,
                key=lambda c: (
                    AUTHORITY[c["source_key"]],
                    completeness(c),
                    c["classification_score"],
                ),
            )
            for c in members:
                survivor_for[c["provisional_id"]] = survivor["provisional_id"]
            for c in members:
                if c["provisional_id"] != survivor["provisional_id"]:
                    merge_rows.append(
                        {
                            "id": str(uuid4()),
                            "pipeline_run_id": run_id,
                            "survivor_hackathon_id": survivor["provisional_id"],
                            "retired_hackathon_id": c["provisional_id"],
                            "reason": "auto_merge_component",
                            "decision_source": "MODEL",
                            "model_version": DEDUP_VERSION,
                            "created_at": started,
                        }
                    )
        # remap obs hackathon IDs before insert
        for o in observation_rows:
            if o["hackathon_id"]:
                o["hackathon_id"] = survivor_for[o["hackathon_id"]]
        # persist provisional rows first
        await gate.insert("hackathons", prov_rows)
        await gate.insert("dedup_candidates", dedup_rows)
        # deactivate retired + reconcile survivors
        updates = []
        members_rows = []
        canonical_selections = []
        conflicts = []
        # observations now, need returned IDs
        obs_inserted = await gate.insert("source_observations", observation_rows, batch=80)
        obs_by_ref_field = {(x["source_event_ref_id"], x["field_path"]): x for x in obs_inserted}
        for members in groups.values():
            survivor_id = survivor_for[members[0]["provisional_id"]]
            for c in members:
                members_rows.append(
                    {
                        "hackathon_id": survivor_id,
                        "source_event_ref_id": c["ref_id"],
                        "source_key": c["source_key"],
                    }
                )
            for c in members:
                if c["provisional_id"] != survivor_id:
                    updates.append(
                        {
                            "id": c["provisional_id"],
                            "is_active": False,
                            "merged_into_id": survivor_id,
                        }
                    )
            fields = [
                "name",
                "official_url",
                "start_at",
                "end_at",
                "format",
                "city",
                "state_region",
                "country",
                "country_code",
                "eligibility",
                "event_type",
                "short_description",
                "registration_url",
                "is_free",
                "participation_fee",
                "participation_fee_currency",
                "language",
                "image_url",
                "organizers",
                "sponsors",
                "offer_valid_through",
                "capacity",
                "source_event_status",
            ]
            selected = {}
            for fp in fields:
                vals = []
                for c in members:
                    ob = obs_by_ref_field.get((c["ref_id"], fp))
                    if not ob:
                        continue
                    value = ob["value_json"]
                    sig = json.dumps(
                        ob.get("normalized_value_json"), sort_keys=True, ensure_ascii=False
                    )
                    vals.append((c, ob, value, sig))
                if not vals:
                    continue
                freq = defaultdict(int)
                for _, _, _, sig in vals:
                    freq[sig] += 1
                scored = []
                for c, ob, value, sig in vals:
                    agreement = freq[sig] / len(vals)
                    score = (
                        0.45 * AUTHORITY[c["source_key"]]
                        + 0.25 * float(ob.get("extraction_confidence") or 0.9)
                        + 0.15 * 1.0
                        + 0.15 * agreement
                    )
                    scored.append((score, c, ob, value))
                scored.sort(key=lambda x: (x[0], AUTHORITY[x[1]["source_key"]]), reverse=True)
                sc, cw, ow, value = scored[0]
                selected[fp] = (
                    ow.get("normalized_value_json")
                    if fp in {"participation_fee", "participation_fee_currency"}
                    else value
                )
                canonical_selections.append(
                    {
                        "pipeline_run_id": run_id,
                        "hackathon_id": survivor_id,
                        "field_path": fp,
                        "selected_observation_id": ow["id"],
                        "policy_version": RECONCILE_VERSION,
                        "score": round(sc, 6),
                        "selected_at": started,
                        "active": True,
                    }
                )
                unique = {x[3] for x in vals}
                if len(unique) > 1 and fp in {
                    "start_at",
                    "end_at",
                    "official_url",
                    "city",
                    "country",
                    "format",
                }:
                    severity = "HIGH" if fp in {"start_at", "end_at"} else "MEDIUM"
                    conflicts.append(
                        {
                            "id": str(uuid4()),
                            "pipeline_run_id": run_id,
                            "hackathon_id": survivor_id,
                            "field_path": fp,
                            "severity": severity,
                            "state": "OPEN",
                            "observation_ids": [
                                x[1]["id"] for x in [(c, ob, v, s) for c, ob, v, s in vals]
                            ],
                            "detected_at": started,
                        }
                    )
            # active canonical update
            start = parse_dt(selected.get("start_at"))
            end = parse_dt(selected.get("end_at"))
            src_count = len({c["source_key"] for c in members})
            completeness_fields = [
                selected.get("name"),
                selected.get("official_url"),
                selected.get("start_at"),
                selected.get("end_at"),
                selected.get("city") or selected.get("country"),
                selected.get("format"),
            ]
            q = round(
                100
                * sum(v not in {None, "", "UNKNOWN"} for v in completeness_fields)
                / len(completeness_fields),
                2,
            )
            updates.append(
                {
                    "id": survivor_id,
                    "name": selected.get("name") or by_id[survivor_id]["name"],
                    "normalized_name": norm_text(
                        selected.get("name") or by_id[survivor_id]["name"]
                    ),
                    "official_url": selected.get("official_url"),
                    "short_description": selected.get("short_description"),
                    "registration_url": selected.get("registration_url"),
                    "is_free": selected.get("is_free"),
                    "participation_fee": selected.get("participation_fee"),
                    "participation_fee_currency": selected.get("participation_fee_currency"),
                    "start_at": selected.get("start_at"),
                    "end_at": selected.get("end_at"),
                    "format": selected.get("format") or "UNKNOWN",
                    "city": selected.get("city"),
                    "state_region": selected.get("state_region"),
                    "country": selected.get("country"),
                    "country_code": selected.get("country_code"),
                    "event_type": selected.get("event_type") or "hackathon",
                    "status": status_for(start, end),
                    "source_count": src_count,
                    "quality_score": q,
                    "last_verified_at": started,
                    "is_active": True,
                    "merged_into_id": None,
                }
            )
        await gate.insert("canonical_event_members", members_rows)
        await gate.insert("canonical_field_selections", canonical_selections, batch=80)
        if conflicts:
            await gate.insert("field_conflicts", conflicts)
        if merge_rows:
            await gate.insert("merge_audit", merge_rows)
        if rejected:
            await gate.insert("rejected_event_refs", rejected)
        await gate.update("hackathons", updates, batch=80)
        active = len(groups)
        review = sum(1 for d in dedup_rows if d["decision"] == "REVIEW")
        auto = len(merge_rows)
        metrics = {
            "source_distribution": dict(
                (s, sum(1 for r in refs if r["source_key"] == s)) for s in sorted(ALLOWED_SOURCES)
            ),
            "accepted_by_source": dict(
                (s, sum(1 for c in candidates if c["source_key"] == s))
                for s in sorted(ALLOWED_SOURCES)
            ),
            "dedup_candidates": len(dedup_rows),
            "review_candidates": review,
            "auto_merges": auto,
            "active_canonicals": active,
            "conflicts": len(conflicts),
        }
        await gate.update(
            "w2_pipeline_runs",
            [
                {
                    "id": run_id,
                    "status": "SUCCEEDED",
                    "finished_at": now_iso(),
                    "extracted_refs": len(refs),
                    "accepted_refs": len(candidates),
                    "rejected_refs": len(rejected),
                    "observation_count": len(observation_rows),
                    "provisional_hackathons": len(candidates),
                    "active_canonical_hackathons": active,
                    "auto_merge_count": auto,
                    "review_candidate_count": review,
                    "conflict_count": len(conflicts),
                    "metrics": metrics,
                }
            ],
        )
        print(json.dumps({"stage": "done", "run_id": run_id, **metrics}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(
            json.dumps({"stage": "failed", "run_id": run_id, "error": f"{type(e).__name__}: {e}"})
        )
        try:
            await gate.update(
                "w2_pipeline_runs",
                [
                    {
                        "id": run_id,
                        "status": "FAILED",
                        "finished_at": now_iso(),
                        "metrics": {"error": f"{type(e).__name__}: {e}"},
                    }
                ],
            )
        except Exception:
            pass
        return 1
    finally:
        await gate.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
