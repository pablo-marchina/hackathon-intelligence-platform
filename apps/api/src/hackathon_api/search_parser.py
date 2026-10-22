from __future__ import annotations

import re
from dataclasses import dataclass

from hackathon_api.schemas import HackathonFilters, NaturalLanguageParseResponse


@dataclass(frozen=True)
class PatternRule:
    pattern: re.Pattern[str]
    field: str
    value: object
    label: str


_RULES = [
    PatternRule(
        re.compile(r"\b(online|remote|remoto|remota)\b", re.I), "format", "ONLINE", "format=ONLINE"
    ),
    PatternRule(
        re.compile(r"\b(in[ -]?person|presencial)\b", re.I),
        "format",
        "IN_PERSON",
        "format=IN_PERSON",
    ),
    PatternRule(
        re.compile(r"\b(hybrid|h[ií]brido|h[ií]brida)\b", re.I), "format", "HYBRID", "format=HYBRID"
    ),
    PatternRule(
        re.compile(r"\b(free|gratuito|gratuita|sem taxa)\b", re.I),
        "free_only",
        True,
        "free_only=true",
    ),
    PatternRule(
        re.compile(
            r"\b(registration open|open registration|inscri[cç][aã]o aberta|inscri[cç][oõ]es abertas)\b",
            re.I,
        ),
        "registration_state",
        "OPEN",
        "registration_state=OPEN",
    ),
    PatternRule(
        re.compile(r"\b(for students|student only|para estudantes|somente estudantes)\b", re.I),
        "student_required",
        True,
        "student_required=true",
    ),
    PatternRule(
        re.compile(r"\b(individual|solo|sozinho|individualmente)\b", re.I),
        "individual_allowed",
        True,
        "individual_allowed=true",
    ),
]

_COUNTRIES = {
    "brazil": "BR",
    "brasil": "BR",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "portugal": "PT",
    "canada": "CA",
    "india": "IN",
    "japan": "JP",
    "japão": "JP",
    "france": "FR",
    "frança": "FR",
    "germany": "DE",
    "alemanha": "DE",
    "spain": "ES",
    "espanha": "ES",
}

_EVENT_TYPES = {
    "hackathon": "hackathon",
    "buildathon": "buildathon",
    "datathon": "datathon",
    "game jam": "game_jam",
    "ideathon": "ideathon",
    "ctf": "security_ctf",
}

_PRIZE_RE = re.compile(
    r"(?:prize|pr[eê]mio)\s*(?:over|above|>=|de pelo menos|acima de)?\s*[$R$\s]*([0-9][0-9.,]*)(k|m)?",
    re.I,
)
_TEAM_RE = re.compile(r"(?:team|equipe)\s*(?:of|de|with|com)?\s*(\d{1,3})", re.I)
_UNSUPPORTED = [
    re.compile(r"\b(guaranteed acceptance|garantia de aceita[cç][aã]o)\b", re.I),
    re.compile(r"\b(best|melhor|top)\b", re.I),
    re.compile(r"\b(easy win|f[aá]cil de ganhar)\b", re.I),
]


def _number(text: str, suffix: str | None) -> float:
    clean = text.replace(",", "")
    value = float(clean)
    if suffix and suffix.lower() == "k":
        value *= 1_000
    elif suffix and suffix.lower() == "m":
        value *= 1_000_000
    return value


def parse_natural_language_query(query: str) -> NaturalLanguageParseResponse:
    working = query
    data: dict[str, object] = {}
    interpreted: list[str] = []
    unsupported: list[str] = []

    for regex in _UNSUPPORTED:
        for match in regex.finditer(working):
            unsupported.append(match.group(0))
        working = regex.sub(" ", working)

    for rule in _RULES:
        if rule.pattern.search(working):
            if rule.field in {"format", "registration_state"}:
                data.setdefault(rule.field, [])
                values = data[rule.field]
                assert isinstance(values, list)
                if rule.value not in values:
                    values.append(rule.value)
            else:
                data[rule.field] = rule.value
            interpreted.append(rule.label)
            working = rule.pattern.sub(" ", working)

    for label, code in sorted(_COUNTRIES.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(rf"\b(?:in|em|no|na|for|para)?\s*{re.escape(label)}\b", re.I)
        if pattern.search(working):
            data.setdefault("country_code", [])
            values = data["country_code"]
            assert isinstance(values, list)
            if code not in values:
                values.append(code)
            interpreted.append(f"country_code={code}")
            working = pattern.sub(" ", working)
            break

    for label, event_type in sorted(
        _EVENT_TYPES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        pattern = re.compile(rf"\b{re.escape(label)}s?\b", re.I)
        if pattern.search(working):
            data.setdefault("event_type", [])
            values = data["event_type"]
            assert isinstance(values, list)
            if event_type not in values:
                values.append(event_type)
            interpreted.append(f"event_type={event_type}")
            working = pattern.sub(" ", working)
            break

    if match := _PRIZE_RE.search(working):
        amount = _number(match.group(1), match.group(2))
        data["min_prize_usd"] = amount
        interpreted.append(f"min_prize_usd={amount:g}")
        working = _PRIZE_RE.sub(" ", working)

    if match := _TEAM_RE.search(working):
        size = int(match.group(1))
        data["team_size"] = size
        interpreted.append(f"team_size={size}")
        working = _TEAM_RE.sub(" ", working)

    remaining = re.sub(r"\s+", " ", working).strip(" ,.-")
    if remaining:
        data["q"] = remaining

    return NaturalLanguageParseResponse(
        original_query=query,
        filters=HackathonFilters.model_validate(data),
        interpreted=interpreted,
        unsupported_constraints=unsupported,
        remaining_text=remaining or None,
    )
