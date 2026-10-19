# W5 final gates — Recommendation + Alerts

W5 is closed at **6/6 issues / 42/42 points**.

## Product surface

- versioned user preference snapshots with hard vs soft preference separation;
- three-valued deterministic eligibility (`ELIGIBLE`, `INELIGIBLE`, `UNKNOWN`) with rule rationale;
- explainable, input-fingerprinted recommendation ranking; hard ineligibility never receives a soft score;
- saved-search definition fingerprinting and notification toggles;
- idempotent new-match, deadline, prize, cancellation/postponement and eligibility-change alerts;
- timezone-aware deadline payloads and canonical/source evidence propagation;
- in-app alert inbox plus SMTP delivery with bounded retry, per-channel/type preference checks and delivery audit;
- `/preferences`, `/recommendations`, `/saved-searches`, `/alerts` Next.js product routes.

## PostgreSQL 17 behavioral gate

The exact W5 physical migration was exercised in an isolated `w5_gate` schema. Final assertions:

```text
preference_versions             2
missing_preserved               1
explicit_false_preserved        1
reproducible_recommendation_rows 1
duplicate_saved_searches_safe   2
dedup_alert_rows                1
idempotent_delivery_rows        1
exact_email_override            true
wildcard_email_setting          false
```

This proves preference history, unknown-vs-false semantics, recommendation input idempotency, duplicate-safe saved searches, alert/delivery deduplication and exact alert-type preference precedence.

## GitHub Actions production web + SMTP gate

Successful run: **35905927315** on Python 3.11 / Node 22.

Passed steps:

- W4 validated baseline + W5 web delta;
- factual W2 snapshot API;
- TypeScript typecheck;
- optimized Next.js production build;
- production HTTP smokes for `/preferences`, `/recommendations`, `/saved-searches`, `/alerts`;
- Dockerized Mailpit SMTP server;
- actual SMTP send through `SmtpEmailSender` and message inspection.

SMTP inspection result:

```json
{"changed_field":true,"deadline_local":true,"direct_link":true,"mailpit_total":1,"smtp_message_id":"w5-mailpit-gate-1","timezone":true}
```

Mailpit is a real SMTP transport sandbox, not a production email provider; the provider adapter remains environment-configurable.

## Offline recommendation eval

The unified eval suite includes `recommendation_ndcg_at_5` and explicitly fails if an `INELIGIBLE` event receives a recommendation score.

Final deterministic seed result:

```text
NDCG@5              1.0000
threshold           0.9000
ineligible_scored   0
```

## Cleanup

- temporary GitHub validation branch reset to `main`;
- `w5_gate` schema dropped;
- Supabase sandbox paused.
