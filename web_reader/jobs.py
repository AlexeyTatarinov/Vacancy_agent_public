"""Поиск вакансий в структурированных данных: JSON страницы, JSON-LD, заявленные числа.

Данные из JSON и JSON-LD надёжнее разбора внешнего вида страницы, поэтому reader
собирает их отдельно и сравнивает с заявленным на странице количеством.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urljoin

TITLE_KEYS = ("title", "jobTitle", "job_title", "positionTitle", "postingTitle", "name", "position", "role", "text")
URL_KEYS = ("absolute_url", "hostedUrl", "applyUrl", "apply_url", "jobUrl", "job_url", "canonicalUrl",
            "url", "link", "href", "externalPath", "path", "slug")
LOC_KEYS = ("location", "locations", "locationName", "jobLocation", "city", "office", "workplace", "country", "remote")
ID_KEYS = ("id", "jobId", "job_id", "reqId", "req_id", "requisitionId", "uuid", "shortcode", "postingId")
DEPT_KEYS = ("department", "departments", "team", "category", "categories", "function", "businessUnit")
DATE_KEYS = ("updated_at", "posted", "postedDate", "datePosted", "createdAt", "created_at", "publishedAt", "published_at", "date")
TYPE_KEYS = ("employmentType", "employment_type", "commitment", "contractType", "type")
JOBISH_KEYS = set(LOC_KEYS[:5]) | set(DEPT_KEYS) | set(TYPE_KEYS) | {"jobId", "job_id", "requisitionId", "applyUrl", "hostedUrl"}

MAX_NODES = 60_000
MAX_DEPTH = 9


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _to_text(value: Any, limit: int = 160) -> str:
    """Превращает значение (строка, словарь, список) в короткую строку."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts = [_to_text(value.get(k)) for k in ("name", "label", "city", "addressLocality", "region", "addressRegion",
                                                  "state", "country", "addressCountry", "text", "value")
                 if value.get(k)]
        if not parts and isinstance(value.get("address"), dict):
            return _to_text(value["address"], limit)
        return ", ".join(p for p in parts if p)[:limit]
    if isinstance(value, list):
        return "; ".join(filter(None, (_to_text(v, 60) for v in value[:5])))[:limit]
    return ""


def _is_taxonomy_term(item: dict[str, Any]) -> bool:
    """Рубрики и метки (страны, отделы, фильтры) похожи на вакансии по полям name/slug/link, но вакансиями не являются."""
    return "taxonomy" in item or ("count" in item and "slug" in item and ("parent" in item or "taxonomy" in item))


def _title_value(item: dict[str, Any]) -> Any:
    title = _first(item, TITLE_KEYS)
    if isinstance(title, dict) and isinstance(title.get("rendered"), str):   # WordPress REST: {"title": {"rendered": "..."}}
        return html.unescape(re.sub(r"<[^>]+>", "", title["rendered"])).strip()
    return title


def _looks_like_job(item: dict[str, Any]) -> bool:
    if _is_taxonomy_term(item):
        return False
    title = _title_value(item)
    if not isinstance(title, str) or not (3 <= len(title.strip()) <= 200):
        return False
    return any(k in item for k in URL_KEYS + LOC_KEYS + ID_KEYS + DEPT_KEYS)


def _normalize(item: dict[str, Any], base_url: str | None) -> dict[str, Any]:
    url = _first(item, URL_KEYS)
    url_text = _to_text(url, 400) if url is not None else ""
    if url_text and base_url and not re.match(r"^[a-z][a-z0-9+.-]*:", url_text):
        url_text = urljoin(base_url, url_text)
    return {
        "title": _to_text(_title_value(item), 200),
        "location": _to_text(_first(item, LOC_KEYS)),
        "department": _to_text(_first(item, DEPT_KEYS)),
        "employment": _to_text(_first(item, TYPE_KEYS), 60),
        "posted": _to_text(_first(item, DATE_KEYS), 40),
        "id": _to_text(_first(item, ID_KEYS), 80),
        "url": url_text,
    }


def _qualifies_as_job_list(items: list[Any]) -> bool:
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts or len(dicts) < 0.6 * len(items):
        return False
    good = [d for d in dicts if _looks_like_job(d)]
    if len(good) < 0.6 * len(dicts):
        return False
    if len(good) >= 3:
        return True
    # Короткий список засчитываем только при явных признаках вакансии,
    # иначе меню сайта («Home», «About») сойдёт за список вакансий.
    return any(set(d) & JOBISH_KEYS for d in good)


def extract_job_items(data: Any, base_url: str | None = None, max_items: int = 100) -> tuple[list[dict[str, Any]], int]:
    """Ищет в произвольном JSON списки, похожие на вакансии.
    Возвращает (первые max_items вакансий, общее число найденных)."""
    found: list[dict[str, Any]] = []
    total = 0
    nodes = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal total, nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            return
        if isinstance(node, list):
            if node and _qualifies_as_job_list(node):
                for item in node:
                    if isinstance(item, dict) and _looks_like_job(item):
                        total += 1
                        if len(found) < max_items:
                            found.append(_normalize(item, base_url))
                return
            for item in node[:2000]:
                walk(item, depth + 1)
        elif isinstance(node, dict):
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value, depth + 1)

    walk(data, 0)
    return found, total


def dedupe_jobs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("title", "").lower(), item.get("url", ""), item.get("id", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


# ---------------------------------------------------------------- JSON-LD

def _iter_ld(node: Any, depth: int = 0):
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node:
            yield from _iter_ld(item, depth + 1)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                yield from _iter_ld(node[key], depth + 1)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_jsonld(blocks: list[str], max_items: int = 100) -> list[dict[str, Any]]:
    """Достаёт объекты JobPosting из блоков JSON-LD."""
    jobs: list[dict[str, Any]] = []
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _iter_ld(data):
            types = obj.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if "JobPosting" not in types:
                continue
            org = obj.get("hiringOrganization")
            location = obj.get("jobLocation")
            if isinstance(location, list):
                location = [loc.get("address") if isinstance(loc, dict) and loc.get("address") else loc for loc in location]
            elif isinstance(location, dict) and location.get("address"):
                location = location["address"]
            jobs.append({
                "title": _to_text(obj.get("title"), 200),
                "organization": _to_text(org, 120) if org else "",
                "location": _to_text(location),
                "remote": str(obj.get("jobLocationType") or "")[:30],
                "employment": _to_text(obj.get("employmentType"), 60),
                "posted": _to_text(obj.get("datePosted"), 40),
                "valid_through": _to_text(obj.get("validThrough"), 40),
                "url": _to_text(obj.get("url"), 400),
                "id": _to_text(obj.get("identifier"), 80),
                "description": _strip_html(str(obj.get("description") or ""))[:600],
            })
            if len(jobs) >= max_items:
                return jobs
    return jobs


def parse_embedded_json(blocks: list[str], base_url: str | None, max_items: int = 100) -> tuple[list[dict[str, Any]], int]:
    """Вакансии из встроенного в страницу JSON (например, __NEXT_DATA__)."""
    items: list[dict[str, Any]] = []
    total = 0
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        found, count = extract_job_items(data, base_url, max_items)
        items.extend(found)
        total += count
    return dedupe_jobs(items)[:max_items], total


# ---------------------------------------------------------------- заявленное число

_JOB_WORDS = (
    r"(?:jobs?|open positions?|positions?|openings?|vacanc(?:y|ies)|roles?|opportunit(?:y|ies)|postings?|"
    r"results?|ofertas?(?: de empleo)?|empleos?|vacantes|puestos|offres?(?: d.emploi)?|emplois|postes|"
    r"stellen(?:angebote)?|vagas|вакансі[яйї]\w*|вакансий|вакансии|позиці[йяї]\w*|позиций|позиции|должност\w+|результат\w*)"
)
_NUM = r"(\d{1,3}(?:[.,  ]\d{3})+|\d+)"
_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"(?:showing|displaying|mostrando|affichage|zeige|показано|показано)\s+[\d.,\s–\-]+\s+"
                r"(?:of|de|von|di|sur|из|з|із)\s+" + _NUM, re.I), "range_of", True),
    (re.compile(r"\b(?:of|de|von|sur|из|з|із)\s+" + _NUM + r"\s*" + _JOB_WORDS, re.I), "of_n_words", True),
    (re.compile(_NUM + r"\s+" + _JOB_WORDS + r"\b", re.I), "number_word", False),
    (re.compile(r"\b" + _JOB_WORDS + r"\s*[:(]\s*" + _NUM, re.I), "word_number", False),
]


def _to_int(raw: str) -> int | None:
    digits = re.sub(r"[.,  ]", "", raw)
    return int(digits) if digits.isdigit() else None


def declared_counts(text: str) -> dict[str, Any]:
    """Ищет на странице заявленное число вакансий («134 jobs», «Showing 1-20 of 134»).
    Возвращает {value, confidence, candidates}. value=None, если ничего не найдено."""
    candidates: list[dict[str, Any]] = []
    for pattern, name, strong in _PATTERNS:
        for match in pattern.finditer(text or ""):
            value = _to_int(match.group(1))
            if value is None or not (1 <= value <= 20000):
                continue
            start = max(0, match.start() - 30)
            snippet = re.sub(r"\s+", " ", text[start:match.end() + 30]).strip()[:120]
            candidates.append({"value": value, "pattern": name, "strong": strong, "snippet": snippet})
            if len(candidates) >= 12:
                break
    if not candidates:
        return {"value": None, "confidence": None, "candidates": []}
    strong = [c for c in candidates if c["strong"]]
    if strong:
        chosen = max(strong, key=lambda c: c["value"])
        confidence = "high"
    else:
        chosen = candidates[0]
        confidence = "low"
    return {"value": chosen["value"], "confidence": confidence, "candidates": candidates[:5]}
