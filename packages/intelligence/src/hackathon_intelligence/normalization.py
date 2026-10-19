from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as dtparser

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_URL_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source"}
_URL_TRACKING_PREFIXES = ("utm_",)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_value = ascii_value.casefold()
    return _NON_ALNUM.sub(" ", ascii_value).strip()


def normalize_url_signature(value: str) -> str:
    """Return a comparison-only URL signature while preserving display URLs elsewhere.

    This deliberately treats a leading ``www.`` host alias as equivalent to the
    apex host and removes common tracking parameters/fragments. It must not be
    used as the user-visible URL because redirects/canonical host preference are
    separate concerns.
    """
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower() or "https"
    hostname = (parts.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = parts.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query_items: list[tuple[str, str]] = []
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in _URL_TRACKING_KEYS or lower_key.startswith(_URL_TRACKING_PREFIXES):
            continue
        query_items.append((key, item_value))
    query_items.sort()
    query = urlencode(query_items, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def parse_datetime(value: str, *, default_tz=UTC) -> datetime:
    parsed = dtparser.parse(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)


@dataclass(frozen=True)
class MoneyValue:
    original_amount: Decimal
    original_currency: str
    normalized_usd_amount: Decimal | None
    fx_rate: Decimal | None
    fx_rate_date: datetime | None


def normalize_money(
    amount: str | int | float | Decimal,
    currency: str,
    *,
    usd_fx_rate: str | int | float | Decimal | None = None,
    fx_rate_date: datetime | None = None,
) -> MoneyValue:
    try:
        original = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("invalid monetary amount") from exc
    if original < 0:
        raise ValueError("monetary amount cannot be negative")
    currency_code = currency.upper().strip()
    if len(currency_code) != 3:
        raise ValueError("currency must be ISO-4217 style 3-letter code")
    rate = Decimal(str(usd_fx_rate)) if usd_fx_rate is not None else None
    if rate is not None and rate <= 0:
        raise ValueError("FX rate must be positive")
    normalized = original * rate if rate is not None else (original if currency_code == "USD" else None)
    return MoneyValue(original, currency_code, normalized, rate, fx_rate_date)
