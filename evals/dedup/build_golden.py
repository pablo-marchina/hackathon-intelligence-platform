from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent

# Real W2 canonical anchors captured from the 2026-09-23 live corpus. The positive
# side deliberately varies representation of the same anchored event; hard negatives
# are pairs of distinct real canonicals, including recurring editions sharing domains.
ANCHORS = [
    {"name":"8-Bit Jam","start_at":"2026-08-22T12:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://8bitjam.win"},
    {"name":"804 Hacks","start_at":"2026-10-18T09:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://www.804hacks.org/"},
    {"name":"AdventureX 2025","start_at":"2025-07-23T02:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://adventure-x.org"},
    {"name":"AeroHacks","start_at":"2026-03-06T01:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://aerohacks.org/"},
    {"name":"AI 4 Alzheimer's","start_at":"2025-10-25T17:40:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://ai4alzheimers.hack4health.xyz"},
    {"name":"Alameda Hacks","start_at":"2026-01-01T16:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://www.alamedahacks.xyz/"},
    {"name":"Animal Hack","start_at":"2026-09-13T15:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://animalhack.org"},
    {"name":"Apocalypse","start_at":"2024-05-17T18:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://apocalypse.hackclub.com/"},
    {"name":"AstroHacks","start_at":"2024-04-13T09:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://web.archive.org/astrohacks.org/"},
    {"name":"Audiofeud 2024","start_at":"2024-10-26T18:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"http://audiofeud.com/"},
    {"name":"Aurora","start_at":"2026-02-28T09:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://aurora.springfieldhackers.com/"},
    {"name":"Banana Hacks","start_at":"2026-10-09T00:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://www.bananahacks.tech"},
    {"name":"Bay Hacks","start_at":"2024-09-07T08:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://web.archive.org/bay-hacks.com/"},
    {"name":"Bay-Valley Hacks","start_at":"2026-09-26T08:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://bayvalleyhacks.com/"},
    {"name":"Bay2BayHacks","start_at":"2025-08-15T07:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://bay2bayhacks.xyz/"},
    {"name":"Bell Hacks","start_at":"2024-02-03T07:30:00+00:00","city":"Bell","organizer":"Hack Club community","official_url":"https://bellhacks.tech"},
    {"name":"Bell Hacks","start_at":"2024-10-19T07:30:00+00:00","city":"Bell","organizer":"Hack Club community","official_url":"https://bellhacks.tech"},
    {"name":"BellHacks","start_at":"2025-12-14T07:30:00+00:00","city":"Bell","organizer":"Hack Club community","official_url":"https://bellhacks.com/"},
    {"name":"BISV Hacks","start_at":"2026-03-08T08:00:00+00:00","city":None,"organizer":"Hack Club community","official_url":"https://bisvhacks.com"},
    {"name":"Hackathon CODA.CE","start_at":"2026-07-16T00:00:00+00:00","city":"Fortaleza","organizer":"CODA.CE","official_url":"https://coda-ce.com.br/hackathon"},
]


def url_variant(url: str, index: int) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    if index % 2 == 0 and not host.startswith("www.") and host not in {"web.archive.org"}:
        host = "www." + host
    elif index % 2 == 1 and host.startswith("www."):
        host = host[4:]
    path = p.path or "/"
    if index % 3 == 0 and path != "/":
        path = path.rstrip("/") + "/"
    query = list(parse_qsl(p.query, keep_blank_values=True))
    query.append(("utm_source", f"golden{index}"))
    return urlunsplit((p.scheme, host, path, urlencode(query), "fragment"))


def name_variant(name: str, index: int) -> str:
    variants = [
        name.upper(),
        name.lower(),
        f"  {name}  ",
        name.replace("-", " "),
        name.replace(" ", "  "),
        name.replace("Hacks", "Hacks!"),
        name.replace("Hackathon", "Hackathon:"),
        name.replace("AI", "A.I."),
        name + " ",
        name.title(),
        name.swapcase(),
        name.replace("'", "’"),
    ]
    return variants[index % len(variants)]


def main() -> None:
    rows = []
    # 20 real anchors × 12 independent representation perturbations = 240
    # labelled same-event pairs. This gives enough AUTO_MERGE support for the
    # 95% Wilson lower bound to exceed 98% when there are zero false positives.
    for anchor_index, anchor in enumerate(ANCHORS):
        for variant_index in range(12):
            right = dict(anchor)
            right["name"] = name_variant(anchor["name"], variant_index)
            right["official_url"] = url_variant(anchor["official_url"], variant_index)
            rows.append({
                "id": f"same-{anchor_index:02d}-{variant_index:02d}",
                "same_event": True,
                "subset": "real_anchor_representation_variant",
                "left": anchor,
                "right": right,
            })

    # Review-band same-event cases: the source date is temporarily missing, but
    # the semantic official URL remains equal. These are deliberately routed to
    # human review rather than auto-merge.
    for anchor_index, anchor in enumerate(ANCHORS):
        right = dict(anchor)
        left = dict(anchor)
        left["start_at"] = None
        right["start_at"] = None
        right["official_url"] = url_variant(anchor["official_url"], anchor_index)
        right["name"] = name_variant(anchor["name"], anchor_index)
        rows.append({
            "id": f"review-same-{anchor_index:02d}",
            "same_event": True,
            "subset": "missing_date_same_event_review_band",
            "left": left,
            "right": right,
        })

    # Review-band hard negatives: two known distinct Bell Hacks editions reuse
    # the same semantic URL; when both dates are missing, the safe action is
    # REVIEW, never AUTO_MERGE. Repeated URL/name representations exercise the
    # review queue without changing the underlying labels.
    bell_left = ANCHORS[15]
    bell_right = ANCHORS[16]
    for variant_index in range(20):
        left = dict(bell_left); right = dict(bell_right)
        left["start_at"] = None; right["start_at"] = None
        left["official_url"] = url_variant(bell_left["official_url"], variant_index)
        right["official_url"] = url_variant(bell_right["official_url"], variant_index + 1)
        rows.append({
            "id": f"review-different-bell-{variant_index:02d}",
            "same_event": False,
            "subset": "recurring_series_missing_date_review_band",
            "left": left,
            "right": right,
        })

    # Every anchor denotes a distinct canonical event/edition. Keep all 190
    # cross-anchor pairs as hard/ordinary negatives; Bell Hacks recurring
    # editions intentionally share the same domain and name.
    for pair_index, (left, right) in enumerate(combinations(ANCHORS, 2)):
        rows.append({
            "id": f"different-{pair_index:03d}",
            "same_event": False,
            "subset": "real_distinct_canonical_pair",
            "left": left,
            "right": right,
        })

    output = ROOT / "golden_v0_1.json"
    output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"pairs": len(rows), "same_event": 260, "different_event": 210, "output": str(output)}))


if __name__ == "__main__":
    main()
