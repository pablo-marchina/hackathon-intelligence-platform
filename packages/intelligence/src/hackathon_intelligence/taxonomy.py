from __future__ import annotations

from dataclasses import dataclass

from .normalization import normalize_name

EVENT_TYPE_ALIASES = {
    "hackathon": "hackathon", "hack day": "hackathon", "hackday": "hackathon",
    "buildathon": "buildathon", "datathon": "datathon", "game jam": "game_jam",
    "gamejam": "game_jam", "ideathon": "ideathon", "ctf": "security_ctf",
    "capture the flag": "security_ctf", "coding competition": "coding_competition",
    "data science competition": "data_science_competition", "innovation challenge": "innovation_challenge",
    "ai challenge": "ai_challenge", "startup challenge": "startup_challenge",
}
TRACK_ALIASES = {
    "ai": "Artificial Intelligence", "artificial intelligence": "Artificial Intelligence",
    "ia": "Artificial Intelligence", "genai": "Generative AI", "generative ai": "Generative AI",
    "fintech": "FinTech", "climate": "Climate", "climate tech": "Climate",
    "health": "Health", "healthcare": "Health", "saude": "Health",
    "cybersecurity": "Cybersecurity", "cyber security": "Cybersecurity",
    "web3": "Web3", "blockchain": "Web3", "sustainability": "Sustainability",
}
TECHNOLOGY_ALIASES = {
    "js": "JavaScript", "javascript": "JavaScript", "typescript": "TypeScript", "ts": "TypeScript",
    "py": "Python", "python": "Python", "reactjs": "React", "react": "React",
    "nextjs": "Next.js", "next js": "Next.js", "nodejs": "Node.js", "node js": "Node.js",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "open ai": "OpenAI", "openai": "OpenAI",
    "hugging face": "Hugging Face", "hf": "Hugging Face", "aws": "AWS", "gcp": "Google Cloud",
}
CANONICAL_EVENT_TYPES = frozenset(EVENT_TYPE_ALIASES.values()) | {"other"}


@dataclass(frozen=True)
class TaxonomyMapping:
    taxonomy: str
    source_label: str
    canonical_label: str
    method: str
    version: str
    confidence: float


def _mapping(taxonomy: str, raw: str, aliases: dict[str, str], *, version: str) -> TaxonomyMapping:
    key = normalize_name(raw)
    canonical = aliases.get(key, raw.strip())
    inferred = key in aliases
    return TaxonomyMapping(taxonomy, raw, canonical, "ALIAS" if inferred else "PRESERVE", version, 0.99 if inferred else 0.60)


def normalize_event_type(raw: str) -> str:
    key = normalize_name(raw)
    return EVENT_TYPE_ALIASES.get(key, "other")


def map_event_type(raw: str, *, version: str = "event-type-v2") -> TaxonomyMapping:
    canonical = normalize_event_type(raw)
    return TaxonomyMapping("event_type", raw, canonical, "ALIAS" if canonical != "other" else "FALLBACK", version, 0.99 if canonical != "other" else 0.50)


def normalize_track(raw: str) -> str:
    return TRACK_ALIASES.get(normalize_name(raw), raw.strip())


def map_track(raw: str, *, version: str = "track-v2") -> TaxonomyMapping:
    return _mapping("track", raw, TRACK_ALIASES, version=version)


def normalize_technology(raw: str) -> str:
    return TECHNOLOGY_ALIASES.get(normalize_name(raw), raw.strip())


def map_technology(raw: str, *, version: str = "technology-v1") -> TaxonomyMapping:
    return _mapping("technology", raw, TECHNOLOGY_ALIASES, version=version)
