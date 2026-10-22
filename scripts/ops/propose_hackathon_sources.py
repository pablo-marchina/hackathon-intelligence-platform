#!/usr/bin/env python3
"""Convert externally discovered hackathon URLs into review-only source proposals.

OFFLINE ONLY: no web requests, no database writes, no seed-registry mutation.
Import candidates from your authorized discovery/search provider as one URL per line.
Only an operator can add proposals to the source registry and approve collection.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/source_sdk/src"))
from hackathon_sources.registry import load_source_definitions  # noqa: E402

MAX_CANDIDATES = 500
MAX_URL_LENGTH = 2048


def safe_candidate_url(raw: str) -> tuple[str, str] | None:
    """Strictly validate untrusted proposed URLs (does not fetch them)."""
    value = raw.strip()
    if not value or len(value) > MAX_URL_LENGTH:
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        if (
            url.scheme != "https" or not host or url.username is not None
            or url.password is not None or url.port is not None
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or ".." in host or host.startswith("-")
            or host.endswith((".local", ".localhost", ".internal", ".test", ".example"))
            or "." not in host
        ):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        return urlunsplit(("https", host, url.path or "/", url.query, "")), host
    except ValueError:
        return None


def propose(urls: list[str], registered: list) -> dict[str, object]:
    """Propose unknown hosts; do not infer a source's legal permissions."""
    known = {
        (host.removeprefix("www.")).lower()
        for src in registered
        for host in [src.domain, *src.allowed_hosts]
    }
    used_keys = {src.key for src in registered}
    proposed: dict[str, dict] = {}
    rejected = 0
    skipped_known = 0
    for raw in urls[:MAX_CANDIDATES]:
        candidate = safe_candidate_url(raw)
        if not candidate:
            if raw.strip() and not raw.lstrip().startswith("#"):
                rejected += 1
            continue
        url, host = candidate
        domain_id = host.removeprefix("www.")
        if domain_id in known:
            skipped_known += 1
            continue
        if domain_id not in proposed:
            base = re.sub(r"[^a-z0-9]+", "_", domain_id).strip("_")
            key = base
            index = 2
            while key in used_keys:
                key = f"{base}_{index}"
                index += 1
            used_keys.add(key)
            proposed[domain_id] = {
                "key": key,
                "name": domain_id,
                "domain": host,
                "source_family": key,
                "source_type": "unverified_hackathon_source",
                "ingestion_mode": "STATIC_HTML",
                "permission_status": "REVIEW_REQUIRED",
                "discovery_enabled": False,
                "detail_extraction_enabled": False,
                "seed_urls": [],
                "notes": "Candidate only: independently verify relevance, legal permission, robots, rate limits, supported extraction and source identity.",
            }
        if url not in proposed[domain_id]["seed_urls"]:
            proposed[domain_id]["seed_urls"].append(url)
    if len(urls) > MAX_CANDIDATES:
        rejected += len(urls) - MAX_CANDIDATES
    return {
        "status": "REVIEW_ONLY",
        "candidate_count": len(urls),
        "new_source_count": len(proposed),
        "skipped_registered_urls": skipped_known,
        "rejected_or_over_budget_urls": rejected,
        "sources": list(proposed.values()),
        "note": "No network activity or approval. Sources must pass operator and policy review before registration or fetch.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 file, one HTTPS URL per line")
    parser.add_argument(
        "--registry", type=Path,
        default=ROOT / "db/seeds/sources_v0_1.json",
    )
    parser.add_argument("--output", type=Path, help="optional JSON report file")
    args = parser.parse_args(argv)
    candidates = args.input.read_text(encoding="utf-8").splitlines()
    report = propose(candidates, load_source_definitions(args.registry))
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
