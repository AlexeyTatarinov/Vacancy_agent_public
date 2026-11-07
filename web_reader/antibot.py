"""Распознавание защиты от ботов. Программа только определяет и честно помечает такие страницы.

Обход капч и жёсткой защиты намеренно не реализован: это защита, поставленная владельцем
сайта. Простую проверку «подождите несколько секунд» настоящий браузер проходит сам, поэтому
reader немного ждёт и перепроверяет, не более.
"""
from __future__ import annotations

import re

_TITLE_PATTERNS = re.compile(
    r"just a moment|attention required|checking your browser|verifying you are human|"
    r"one more step|security check|access denied|pardon our interruption|are you a robot|"
    r"human verification|ddos protection|un instant|einen moment|un momento|один момент",
    re.I,
)
_TEXT_PATTERNS = [
    (re.compile(r"verify you are human|checking if the site connection is secure|"
                r"enable javascript and cookies to continue|performing security verification", re.I), "cloudflare"),
    (re.compile(r"press\s*&\s*hold|px-captcha|perimeterx", re.I), "perimeterx"),
    (re.compile(r"incapsula incident|request unsuccessful\. incapsula", re.I), "imperva"),
    (re.compile(r"datadome", re.I), "datadome"),
    (re.compile(r"captcha|are you a (?:human|robot)|unusual traffic", re.I), "captcha"),
    (re.compile(r"access denied|forbidden", re.I), "access_denied"),
]


def detect(title: str, text: str, status: int | None, headers: dict[str, str] | None) -> str | None:
    """Возвращает код защиты (cloudflare, captcha, ...) либо None, если страница выглядит обычной."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return "cloudflare_challenge"
    title = title or ""
    head = (text or "")[:4000]
    if _TITLE_PATTERNS.search(title):
        return "challenge_title"
    # Слова о капче встречаются и на обычных страницах (форма с reCAPTCHA),
    # поэтому по тексту судим только если страница почти пустая.
    if len(head) < 2500:
        for pattern, label in _TEXT_PATTERNS:
            if pattern.search(head):
                return label
    if status in (403, 429, 503) and len(head) < 800:
        return f"http_{status}"
    return None
