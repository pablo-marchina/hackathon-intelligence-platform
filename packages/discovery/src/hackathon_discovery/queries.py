from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256

QUERY_TEMPLATE_VERSION = "w3-query-v1"

EVENT_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "hackathon",
        "buildathon",
        "datathon",
        "game jam",
        "innovation challenge",
        "AI hackathon",
        "coding challenge",
        "CTF",
    ),
    "pt": (
        "hackathon",
        "maratona de programação",
        "datathon",
        "game jam",
        "desafio de inovação",
        "hackathon de IA",
        "desafio de programação",
    ),
    "es": (
        "hackathon",
        "maratón de programación",
        "datatón",
        "game jam",
        "reto de innovación",
        "hackathon de IA",
    ),
    "fr": ("hackathon", "buildathon", "datathon", "game jam", "défi d'innovation", "hackathon IA"),
    "de": (
        "Hackathon",
        "Buildathon",
        "Datathon",
        "Game Jam",
        "Innovationswettbewerb",
        "KI Hackathon",
    ),
    "it": (
        "hackathon",
        "buildathon",
        "datathon",
        "game jam",
        "sfida di innovazione",
        "hackathon IA",
    ),
    "hi": ("हैकाथॉन", "AI हैकाथॉन", "डेटाथॉन", "innovation challenge", "coding challenge"),
    "ja": (
        "ハッカソン",
        "AI ハッカソン",
        "データソン",
        "ゲームジャム",
        "イノベーション チャレンジ",
    ),
    "ko": ("해커톤", "AI 해커톤", "데이터톤", "게임잼", "혁신 챌린지"),
    "zh": ("黑客松", "人工智能 黑客松", "数据 黑客松", "游戏 创作 马拉松", "创新 挑战赛"),
}

INTENT_TERMS: dict[str, tuple[str, ...]] = {
    "en": ("registration", "deadline", "prize", "apply", "team"),
    "pt": ("inscrição", "prazo", "prêmio", "candidatura", "equipe"),
    "es": ("inscripción", "fecha límite", "premio", "solicitud", "equipo"),
    "fr": ("inscription", "date limite", "prix", "candidature", "équipe"),
    "de": ("Anmeldung", "Frist", "Preis", "Bewerbung", "Team"),
    "it": ("iscrizione", "scadenza", "premio", "candidatura", "squadra"),
    "hi": ("पंजीकरण", "समय सीमा", "पुरस्कार", "आवेदन", "टीम"),
    "ja": ("登録", "締切", "賞金", "応募", "チーム"),
    "ko": ("등록", "마감", "상금", "지원", "팀"),
    "zh": ("报名", "截止", "奖金", "申请", "团队"),
}

QUARTER_TERMS: dict[str, tuple[str, str, str, str]] = {
    "en": ("Q1", "Q2", "Q3", "Q4"),
    "pt": ("1º trimestre", "2º trimestre", "3º trimestre", "4º trimestre"),
    "es": ("1er trimestre", "2º trimestre", "3er trimestre", "4º trimestre"),
    "fr": ("1er trimestre", "2e trimestre", "3e trimestre", "4e trimestre"),
    "de": ("Q1", "Q2", "Q3", "Q4"),
    "it": ("1° trimestre", "2° trimestre", "3° trimestre", "4° trimestre"),
    "hi": ("Q1", "Q2", "Q3", "Q4"),
    "ja": ("第1四半期", "第2四半期", "第3四半期", "第4四半期"),
    "ko": ("1분기", "2분기", "3분기", "4분기"),
    "zh": ("第一季度", "第二季度", "第三季度", "第四季度"),
}


@dataclass(frozen=True)
class QueryTemplate:
    template_key: str
    version: str
    language: str
    event_term: str
    intent_term: str
    time_strategy: str


@dataclass(frozen=True)
class DiscoveryQuery:
    template_key: str
    template_version: str
    language: str
    event_term: str
    intent_term: str
    year: int
    quarter: int | None
    time_window: str
    query: str

    @property
    def attribution_key(self) -> str:
        payload = f"{self.template_version}|{self.template_key}|{self.query}"
        return sha256(payload.encode("utf-8")).hexdigest()[:24]


def current_quarter(value: date) -> int:
    return ((value.month - 1) // 3) + 1


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 4:
        return year + 1, 1
    return year, quarter + 1


def build_templates(*, languages: tuple[str, ...] | None = None) -> list[QueryTemplate]:
    languages = languages or tuple(EVENT_TERMS)
    templates: list[QueryTemplate] = []
    for language in languages:
        if language not in EVENT_TERMS or language not in INTENT_TERMS:
            raise ValueError(f"unsupported discovery language: {language}")
        for event_term in EVENT_TERMS[language]:
            for intent_term in INTENT_TERMS[language]:
                base = f"{language}:{event_term}:{intent_term}".casefold()
                templates.append(
                    QueryTemplate(
                        template_key=sha256(base.encode("utf-8")).hexdigest()[:20],
                        version=QUERY_TEMPLATE_VERSION,
                        language=language,
                        event_term=event_term,
                        intent_term=intent_term,
                        time_strategy="YEAR_AND_CURRENT_NEXT_QUARTER",
                    )
                )
    return templates


def generate_queries(
    *, today: date | None = None, languages: tuple[str, ...] | None = None
) -> list[DiscoveryQuery]:
    today = today or date.today()
    quarter = current_quarter(today)
    next_q_year, next_q = _next_quarter(today.year, quarter)
    result: list[DiscoveryQuery] = []
    for template in build_templates(languages=languages):
        for year in (today.year, today.year + 1):
            text = f'"{template.event_term}" {template.intent_term} {year}'
            result.append(
                DiscoveryQuery(
                    template.template_key,
                    template.version,
                    template.language,
                    template.event_term,
                    template.intent_term,
                    year,
                    None,
                    str(year),
                    text,
                )
            )
        for q_year, q in ((today.year, quarter), (next_q_year, next_q)):
            q_term = QUARTER_TERMS[template.language][q - 1]
            text = f'"{template.event_term}" {template.intent_term} {q_term} {q_year}'
            result.append(
                DiscoveryQuery(
                    template.template_key,
                    template.version,
                    template.language,
                    template.event_term,
                    template.intent_term,
                    q_year,
                    q,
                    f"{q_year}-Q{q}",
                    text,
                )
            )
    return result
