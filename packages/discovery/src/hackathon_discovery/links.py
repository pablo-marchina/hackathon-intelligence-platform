from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

ROLE_TERMS = {
    "rules": (
        "rules",
        "regulation",
        "regulamento",
        "reglamento",
        "règlement",
        "terms",
        "eligibility",
        "参加規約",
        "규정",
        "规则",
    ),
    "registration": (
        "register",
        "registration",
        "apply",
        "inscri",
        "报名",
        "登録",
        "신청",
        "bewerb",
        "candidatura",
    ),
    "results": (
        "results",
        "winners",
        "resultado",
        "résultat",
        "gewinner",
        "risultati",
        "受賞",
        "수상",
        "获奖",
    ),
    "projects": (
        "projects",
        "submissions",
        "showcase",
        "gallery",
        "projet",
        "proyectos",
        "作品",
        "프로젝트",
        "项目",
    ),
    "faq": (
        "faq",
        "frequently asked",
        "questions",
        "dúvidas",
        "preguntas",
        "質問",
        "자주 묻",
        "常见问题",
    ),
    "schedule": (
        "schedule",
        "agenda",
        "timeline",
        "program",
        "cronograma",
        "programme",
        "zeitplan",
        "スケジュール",
        "일정",
        "日程",
    ),
    "organizer": (
        "organizer",
        "organiser",
        "host",
        "about us",
        "organization",
        "organização",
        "主催",
        "주최",
        "主办",
    ),
    "challenges": (
        "challenge",
        "challenges",
        "desafio",
        "défi",
        "challenge statements",
        "課題",
        "도전",
        "挑战",
    ),
}

ROLE_PRIORITY = {
    "rules": 100,
    "registration": 98,
    "challenges": 95,
    "schedule": 90,
    "results": 88,
    "projects": 86,
    "faq": 82,
    "organizer": 75,
}


@dataclass(frozen=True)
class RelatedLink:
    url: str
    role: str
    external_domain: bool
    anchor_text: str
    priority: int


@dataclass
class ExplorationBudget:
    max_depth: int = 2
    max_pages_per_domain: int = 40
    max_external_domains: int = 12
    visited_pages: int = 0
    external_domains: set[str] | None = None

    def __post_init__(self) -> None:
        if self.external_domains is None:
            self.external_domains = set()

    def allow_page(self, *, depth: int) -> bool:
        return depth <= self.max_depth and self.visited_pages < self.max_pages_per_domain

    def consume_page(self) -> None:
        self.visited_pages += 1

    def allow_external(self, host: str) -> bool:
        assert self.external_domains is not None
        if host in self.external_domains:
            return True
        if len(self.external_domains) >= self.max_external_domains:
            return False
        self.external_domains.add(host)
        return True


def classify_related_link(anchor_text: str, url: str) -> str | None:
    haystack = f"{anchor_text} {url}".casefold()
    for role, terms in ROLE_TERMS.items():
        if any(term.casefold() in haystack for term in terms):
            return role
    return None


def discover_related_links(
    html: str,
    base_url: str,
    *,
    max_links: int = 100,
    depth: int = 0,
    budget: ExplorationBudget | None = None,
) -> list[RelatedLink]:
    budget = budget or ExplorationBudget()
    if not budget.allow_page(depth=depth):
        return []
    budget.consume_page()
    soup = BeautifulSoup(html, "html.parser")
    base_host = (urlsplit(base_url).hostname or "").casefold().removeprefix("www.")
    seen: set[str] = set()
    result: list[RelatedLink] = []
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(base_url, anchor["href"])
        if absolute in seen or not absolute.startswith(("http://", "https://")):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        role = classify_related_link(text, absolute)
        if role is None:
            continue
        host = (urlsplit(absolute).hostname or "").casefold().removeprefix("www.")
        external = host != base_host
        if external and not budget.allow_external(host):
            continue
        seen.add(absolute)
        result.append(RelatedLink(absolute, role, external, text, ROLE_PRIORITY[role] - depth * 5))
    result.sort(key=lambda item: (-item.priority, item.external_domain, item.url))
    return result[:max_links]
