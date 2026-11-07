"""Отбор вакансий по ролям (QA, LQA, compliance, certification) по словарю config/roles.json.

Словарь: аббревиатуры, полные названия и украинские основы слов (производные формы).
Совпадение «в названии» считается сильным, «в тексте вакансии» слабым и показывается отдельно.
Сравнение без учёта регистра, дефисы и пробелы равнозначны.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("ʼ", "'")
    text = re.sub(r"[-_/–—]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_re(parts: Iterable[str], prefix: bool) -> re.Pattern[str] | None:
    items = sorted({_norm(p) for p in parts if _norm(p)}, key=len, reverse=True)
    if not items:
        return None
    body = "|".join(re.escape(i) for i in items)
    tail = r"\w*" if prefix else r"(?!\w)"
    return re.compile(rf"(?<!\w)(?:{body}){tail}", re.I)


@dataclass
class Group:
    id: str
    label: str
    strong: list[re.Pattern[str]] = field(default_factory=list)
    weak: list[re.Pattern[str]] = field(default_factory=list)


@dataclass
class RoleRules:
    groups: list[Group]
    exclude: re.Pattern[str] | None
    languages: dict[str, re.Pattern[str]]
    source: str = ""

    def match(self, text: str, level: str = "strong") -> list[dict[str, Any]]:
        """Находит совпадения в тексте: [{group, label, terms}]. level=strong: только сильные признаки."""
        norm = _norm(text)
        found = []
        for g in self.groups:
            patterns = g.strong if level == "strong" else g.weak
            terms: list[str] = []
            for pat in patterns:
                terms += [m.group(0) for m in pat.finditer(norm)]
            if terms:
                found.append({"group": g.id, "label": g.label, "terms": sorted(set(terms))[:6]})
        return found

    def excluded_region(self, text: str) -> str | None:
        if not self.exclude:
            return None
        m = self.exclude.search(_norm(text))
        return m.group(0) if m else None

    def languages_in(self, text: str) -> list[str]:
        norm = _norm(text)
        return [name for name, pat in self.languages.items() if pat.search(norm)]


def load_rules(path: str | Path) -> RoleRules:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    groups = []
    for g in data.get("groups", []):
        strong = [p for p in (_word_re(g.get("abbreviations", []), False), _word_re(g.get("phrases", []), False),
                              _word_re(g.get("stems", []), True)) if p]
        weak = [p for p in (_word_re(g.get("weak_phrases", []), False), _word_re(g.get("weak_stems", []), True)) if p]
        groups.append(Group(g["id"], g.get("label", g["id"]), strong, weak))
    exclude = _word_re((data.get("exclude_regions") or {}).get("terms", []), False)
    languages = {}
    for name, terms in (data.get("languages") or {}).items():
        if name.startswith("_") or not isinstance(terms, list):
            continue
        languages[name] = _word_re(terms, True)
    return RoleRules(groups, exclude, {k: v for k, v in languages.items() if v}, str(path))


# ---------------------------------------------------------------- применение к результатам запуска

def _walk(res: Any):
    yield res
    for c in res.children:
        yield from _walk(c)


def collect_matches(summary_results: list[Any], rules: RoleRules) -> list[dict[str, Any]]:
    """Все вакансии из результатов запуска, подходящие по ролям. Дубликаты (по адресу) объединяются."""
    seen: dict[str, dict[str, Any]] = {}

    def add(entry: dict[str, Any]) -> None:
        key = entry["url"] or entry["title"]
        old = seen.get(key)
        if old is None or (old["level"] == "text" and entry["level"] == "title"):
            seen[key] = entry

    for top in summary_results:
        for res in _walk(top):
            site = res.final_url or res.url
            jobs = res.jobs or {}
            for src in ("links", "network", "jsonld", "embedded"):
                for job in jobs.get(src, []) or []:
                    title = job.get("title", "")
                    ctx = f"{title} {job.get('url', '')}"
                    hits = rules.match(ctx)
                    if not hits:
                        continue
                    loc = job.get("location", "") or ""
                    region = rules.excluded_region(f"{loc} {title} {job.get('context', '')}")
                    add({"title": title, "url": job.get("url", ""), "location": loc, "level": "title", "source": src,
                         "page": site, "roles": hits, "excluded_region": region, "languages": []})
            # отдельная страница вакансии: заголовок h1 или текст
            if res.relation in ("job_internal", "ats_job") and res.status in ("ok", "partial"):
                title = res.h1 or res.title
                text = res.full_text()[:6000]
                hits = rules.match(f"{title} {res.url}")
                level = "title"
                if not hits:
                    hits, level = rules.match(text), "text"
                if hits:
                    region = rules.excluded_region(f"{title} {text[:1500]}")
                    add({"title": title, "url": res.final_url or res.url, "location": "", "level": level, "source": "job_page",
                         "page": site, "roles": hits, "excluded_region": region, "languages": rules.languages_in(text)})
    order = {"title": 0, "text": 1}
    return sorted(seen.values(), key=lambda e: (e["excluded_region"] is not None, order[e["level"]], e["title"].lower()))
