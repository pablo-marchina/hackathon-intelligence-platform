from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CLASSIFIER_VERSION = "w3-rule-precision-v2"

STRONG_EVENT_TERMS: list[tuple[str, str, int]] = [
    ("hackathon", "hackathon", 8), ("hackatón", "hackathon", 8), ("hackathon de ia", "ai_challenge", 9),
    ("buildathon", "buildathon", 8), ("datathon", "datathon", 8), ("datatón", "datathon", 8),
    ("game jam", "game_jam", 7), ("gamejam", "game_jam", 7), ("ctf", "security_ctf", 7),
    ("capture the flag", "security_ctf", 8), ("maratona de programacao", "coding_competition", 7),
    ("maraton de programacion", "coding_competition", 7), ("defi d innovation", "innovation_challenge", 7),
    ("innovationswettbewerb", "innovation_challenge", 7), ("sfida di innovazione", "innovation_challenge", 7),
    ("ハッカソン", "hackathon", 9), ("データソン", "datathon", 8), ("ゲームジャム", "game_jam", 8),
    ("해커톤", "hackathon", 9), ("데이터톤", "datathon", 8), ("게임잼", "game_jam", 8),
    ("黑客松", "hackathon", 9), ("数据 黑客松", "datathon", 8), ("數據 黑客松", "datathon", 8),
    ("हैकाथॉन", "hackathon", 9),
]

AMBIGUOUS_EVENT_TERMS: list[tuple[str, str, int]] = [
    ("innovation challenge", "innovation_challenge", 4), ("desafio de inovacao", "innovation_challenge", 4),
    ("reto de innovacion", "innovation_challenge", 4), ("coding challenge", "coding_competition", 4),
    ("challenge", "innovation_challenge", 2), ("competition", "other", 1), ("desafio", "innovation_challenge", 2),
    ("concours", "other", 1), ("wettbewerb", "other", 1), ("挑战赛", "innovation_challenge", 3),
    ("챌린지", "innovation_challenge", 3), ("チャレンジ", "innovation_challenge", 3),
]

OPERATIONAL_TERMS = {
    "registration": ("registration", "register", "apply now", "applications open", "inscricao", "inscripción", "inscription", "anmeldung", "iscrizione", "応募", "登録", "신청", "등록", "报名", "申请", "पंजीकरण"),
    "deadline": ("deadline", "submission deadline", "prazo", "fecha limite", "date limite", "frist", "scadenza", "締切", "마감", "截止", "समय सीमा"),
    "prize": ("prize", "award", "premio", "prêmio", "prix", "preis", "賞金", "상금", "奖金", "पुरस्कार"),
    "team": ("team", "teams", "equipe", "equipo", "équipe", "squadra", "チーム", "팀", "团队", "टीम"),
    "build": ("prototype", "build", "submit project", "prototipo", "protótipo", "prototyp", "プロトタイプ", "프로토타입", "原型"),
}

NEGATIVE_PATTERNS: list[tuple[str, tuple[str, ...], int]] = [
    ("guide", ("how to organize", "how to run", "hackathon guide", "blog guide", "tools for hackathon", "guia de hackathon", "guide du hackathon", "ハッカソンの開催方法"), -14),
    ("winner_profile", ("winner interview", "meet the winner", "winner profile", "entrevista com vencedor", "portrait du gagnant", "수상팀 인터뷰"), -12),
    ("job", ("job opening", "we are hiring", "career", "vaga", "emploi", "stellenangebot", "採用", "채용", "招聘"), -14),
    ("course", ("course", "curso", "cours en ligne", "kurs", "corso", "講座", "강좌", "课程"), -10),
    ("article", ("news article", "reported yesterday", "recap", "wrap-up", "retrospective", "noticia", "notícia", "communique", "pressemitteilung", "risultati hackathon", "成果発表", "成果发布", "收官"), -9),
    ("conference", ("conference", "conferencia", "conferência", "conférence", "konferenz", "conferenza", "カンファレンス", "컨퍼런스", "大会"), -6),
    ("generic_landing", ("welcome to our community", "developer community homepage", "about our organization"), -8),
]

NON_BUILD_NEGATIONS = (
    "no team", "no prototype", "without team", "without prototype",
    "nao exige equipe", "não exige equipe", "sem equipe", "sem prototipo", "sem protótipo",
    "sin equipo", "sin prototipo", "sans equipe", "sans équipe", "sans prototype",
    "kein team", "kein prototyp", "no hackathon", "not a hackathon",
    "チームやプロトタイプ開発は不要", "팀 개발 또는 프로토타입 요구가 없습니다",
    "不要求团队开发或原型", "不要求團隊開發或原型",
)


@dataclass(frozen=True)
class Classification:
    is_event: bool
    is_hackathon_like: bool
    event_type: str
    confidence: float
    score: int
    reasons: tuple[str, ...]
    negative_class: str | None = None
    classifier_version: str = CLASSIFIER_VERSION


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def classify_page(*, title: str, text: str) -> Classification:
    title_norm = _norm(title)
    content = _norm(f"{title} {text}")
    score = 0
    reasons: list[str] = []
    event_type = "other"
    strong_term = False
    ambiguous_term = False

    for phrase, candidate_type, weight in STRONG_EVENT_TERMS:
        if _norm(phrase) in content:
            strong_term = True
            if event_type == "other" or weight > 8:
                event_type = candidate_type
            score += weight
            reasons.append(f"event:{phrase}")
            break
    if not strong_term:
        for phrase, candidate_type, weight in AMBIGUOUS_EVENT_TERMS:
            if _norm(phrase) in content:
                ambiguous_term = True
                event_type = candidate_type
                score += weight
                reasons.append(f"ambiguous:{phrase}")
                break

    operational_count = 0
    for signal, phrases in OPERATIONAL_TERMS.items():
        if any(_norm(phrase) in content for phrase in phrases):
            operational_count += 1
            score += 2
            reasons.append(f"signal:{signal}")

    negative_class: str | None = None
    negative_weight = 0
    if any(_norm(phrase) in content for phrase in NON_BUILD_NEGATIONS):
        negative_class = "non_build_competition"
        negative_weight = -14
    for candidate, phrases, weight in NEGATIVE_PATTERNS:
        if any(_norm(phrase) in content for phrase in phrases):
            if candidate == "conference" and strong_term and operational_count >= 2:
                continue
            negative_class = candidate
            negative_weight = min(negative_weight, weight)
    if negative_class:
        score += negative_weight
        reasons.append(f"negative:{negative_class}")

    year_signal = bool(re.search(r"\b20(?:2[4-9]|3[0-5])\b", content))
    date_signal = bool(re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\w*\b", content))
    if year_signal:
        score += 1
        reasons.append("signal:year")
    if date_signal:
        score += 1
        reasons.append("signal:date")

    title_has_event_term = any(_norm(term) in title_norm for term, _, _ in STRONG_EVENT_TERMS)
    strong_accept = strong_term and (operational_count >= 1 or year_signal or date_signal or title_has_event_term)
    ambiguous_accept = ambiguous_term and operational_count >= 3 and any(x in reasons for x in ("signal:team", "signal:build"))
    is_hackathon_like = negative_class is None and score >= 8 and (strong_accept or ambiguous_accept)
    is_event = negative_class not in {"guide", "job", "course", "generic_landing"} and (score >= 4 or is_hackathon_like)

    margin = score - 8
    confidence = 0.50 + min(0.47, abs(margin) * 0.055 + operational_count * 0.025)
    if not is_hackathon_like and negative_class:
        confidence = max(confidence, 0.90)
    confidence = round(min(0.99, max(0.01, confidence)), 4)
    return Classification(is_event, is_hackathon_like, event_type, confidence, score, tuple(reasons), negative_class)
