from __future__ import annotations

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def sha256_normalized_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
