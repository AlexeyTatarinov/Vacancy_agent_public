"""Отчёты: читаемый Markdown (для человека и ИИ) и полный JSON (для программ)."""
from __future__ import annotations

import json
from urllib.parse import urlsplit
import re
from pathlib import Path
from typing import Any

from . import __version__
from .i18n import t
from .reader import PageResult

LINK_CLASS_ORDER = ["pagination", "ats_board", "ats_job", "job_internal", "pdf", "document", "internal",
                    "external", "social", "image", "anchor", "mailto", "tel", "javascript", "other"]


def _cell(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _fence(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def _fmt_warning(lang: str, item: dict[str, Any]) -> str:
    detail = {k: v for k, v in item.items() if k not in ("code", "severity")}
    try:
        return t(lang, item["code"], **detail)
    except (KeyError, IndexError):
        return f"{item['code']} {detail}"


def _fmt_limit(lang: str, hit: dict[str, Any]) -> str:
    name = hit["limit"]
    label = t(lang, "lim_" + name)
    if label == "lim_" + name:
        label = name
    extra = ", ".join(f"{k}={v}" for k, v in hit.items() if k != "limit" and v not in (None, [], ""))
    return f"{label}" + (f" ({extra})" if extra else "")


def slugify(url: str) -> str:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    raw = (parts.hostname or "page") + parts.path
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-")[:50] or "page"


def _job_rows(res: PageResult, lang: str, limit: int) -> list[str]:
    rows: list[str] = []
    sources = [("page", res.jobs.get("links", [])), ("network", res.jobs.get("network", [])),
               ("json-ld", res.jobs.get("jsonld", [])), ("embedded", res.jobs.get("embedded", []))]
    header = (f"| {t(lang, 'col_title')} | {t(lang, 'col_location')} | {t(lang, 'col_url')} | {t(lang, 'col_source')} |\n"
              "|---|---|---|---|")
    for source, items in sources:
        for item in items:
            rows.append(f"| {_cell(item.get('title'), 90)} | {_cell(item.get('location'), 50)} | "
                        f"{_cell(item.get('url'), 160)} | {source} |")
    if not rows:
        return []
    shown = rows[:limit]
    out = [header, *shown]
    if len(rows) > limit:
        out.append(t(lang, "more_items", n=len(rows) - limit))
    return out


def render_page_md(res: PageResult, settings: Any, lang: str, level: int = 1) -> str:
    h = "#" * level
    h2 = "#" * (level + 1)
    out: list[str] = []
    title = res.title or res.h1 or res.url
    out.append(f"{h} {_cell(title, 150)}\n")
    out.append(f"- **{t(lang, 'f_url')}**: {res.url}")
    if res.final_url and res.final_url != res.url:
        out.append(f"- **{t(lang, 'f_final_url')}**: {res.final_url}")
    if res.redirect_chain:
        out.append(f"- redirects: {' → '.join(res.redirect_chain)} → {res.final_url}")
    out.append(f"- **{t(lang, 'f_status')}**: {t(lang, 'status_' + res.status)}"
               + (f" ({res.skip_reason})" if res.skip_reason else "")
               + (f" — {res.error}" if res.error else ""))
    out.append(f"- **{t(lang, 'f_kind')}**: {t(lang, 'kind_' + res.kind)}")
    out.append(f"- **{t(lang, 'f_http')}**: {res.http_status if res.http_status is not None else '—'}")
    out.append(f"- **{t(lang, 'f_mode')}**: {res.mode} · {t(lang, 'f_depth')} {res.depth} · {res.relation}"
               + (f" · «{_cell(res.link_text, 60)}»" if res.link_text else ""))
    out.append(f"- **{t(lang, 'f_time')}**: {res.seconds}")
    if res.screenshot:
        out.append(f"- **{t(lang, 'screenshot')}**: {res.screenshot}")
    out.append("")

    if res.pdf is not None:
        out.append(f"{h2} PDF\n")
        out.append(f"pages: {res.pdf.get('pages')} (read {res.pdf.get('pages_read')})")
        for note in res.pdf.get("notes", []):
            out.append(f"- {note}")
        if res.pdf.get("error"):
            out.append(f"- error: {res.pdf['error']}")
        text = res.pdf.get("text", "")
        limit = settings.run.followed_text_chars
        if text:
            out.append("\n" + _fence(text[:limit]))
            if len(text) > limit:
                out.append(t(lang, "truncated_text"))
        out.append("")
        return "\n".join(out)

    # --- полнота
    out.append(f"{h2} {t(lang, 's_completeness')}\n")
    warns = [w for w in res.warnings if w["severity"] == "warn"]
    infos = [w for w in res.warnings if w["severity"] != "warn"]
    if not res.limits_hit and not warns:
        out.append(t(lang, "complete_ok"))
    if res.limits_hit:
        out.append(f"**{t(lang, 'limits_hit')}:**")
        out.extend(f"- {_fmt_limit(lang, hit)}" for hit in res.limits_hit)
    if warns:
        out.append(f"**{t(lang, 'warnings')}:**")
        out.extend(f"- {_fmt_warning(lang, w)}" for w in warns)
    if infos or res.notes:
        out.append(f"**{t(lang, 'notes')}:**")
        out.extend(f"- {_fmt_warning(lang, n)}" for n in infos + res.notes)
    declared = res.jobs.get("declared", {}) if res.jobs else {}
    if declared.get("value"):
        counts = res.jobs.get("counts", {})
        chain = f", {res.jobs['chain_total']} with list pages" if res.jobs.get("chain_total") else ""
        out.append(f"- {t(lang, 'declared')}: {declared['value']} ({declared['confidence']}) · "
                   f"{t(lang, 'collected')}: {res.jobs.get('best', 0)}{chain} "
                   f"(page {counts.get('links', 0)}, network {counts.get('network', 0)}, "
                   f"json-ld {counts.get('jsonld', 0)}, embedded {counts.get('embedded', 0)})")
    out.append("")

    # --- вакансии
    rows = _job_rows(res, lang, settings.limits.max_jobs_per_page * 2)
    if rows:
        out.append(f"{h2} {t(lang, 's_jobs')}\n")
        out.extend(rows)
        out.append("")

    # --- попапы и раскрытие
    if res.popups and (res.popups.get("dismissed") or res.popups.get("obstructions")):
        out.append(f"{h2} {t(lang, 's_popups')}\n")
        if res.popups.get("dismissed"):
            out.append(f"- {t(lang, 'dismissed')}: " + "; ".join(res.popups["dismissed"]))
        if res.popups.get("obstructions"):
            out.append(f"- {t(lang, 'obstructions')}: " + " | ".join(_cell(o, 100) for o in res.popups["obstructions"]))
        out.append("")
    if res.expansion and res.mode == "full":
        out.append(f"{h2} {t(lang, 's_expansion')}\n")
        out.append(f"- {t(lang, 'scrolls')}: {res.expansion.get('scrolls', 0)} · "
                   f"{t(lang, 'expansions')}: {res.expansion.get('expansions', 0)} · "
                   f"{t(lang, 'stop_reason')}: {res.expansion.get('stop_reason') or '—'}")
        for click in res.expansion.get("clicks", [])[:40]:
            out.append(f"  - {click}")
        out.append("")

    # --- фреймы
    if res.frames:
        out.append(f"{h2} {t(lang, 's_frames')}\n")
        for fr in res.frames:
            state = "read" if fr["read"] else f"not read ({fr['reason']})"
            out.append(f"- [{fr['kind']}] {_cell(fr['url'], 140)} — {state}")
        out.append("")

    # --- сеть
    if res.network:
        out.append(f"{h2} {t(lang, 's_network')}\n")
        for entry in res.network:
            extra = f", {entry.get('jobs_found', 0)} vacancies" if entry.get("jobs_found") else ""
            skipped = f", {entry['skipped']}" if entry.get("skipped") else ""
            repeat = f" ×{entry['repeat']}" if entry.get("repeat", 1) > 1 else ""
            out.append(f"- {entry['method']} {_cell(entry['url'], 150)} ({entry['status']}, {entry['bytes']} B{extra}{skipped}){repeat}")
        out.append("")

    # --- текст
    limit = settings.output.report_text_chars if res.mode == "full" else settings.run.followed_text_chars
    out.append(f"{h2} {t(lang, 's_text')}\n")
    used = 0
    cut = False
    for section in res.sections:
        remaining = limit - used
        if remaining <= 0:
            cut = True
            break
        text = section["text"]
        if len(text) > remaining:
            text, cut = text[:remaining], True
        used += len(text)
        out.append(f"**[{section['label']}]** ({section['source'][:80]})")
        out.append(_fence(text))
        out.append("")
    if cut or res.text_truncated:
        out.append(t(lang, "truncated_text") + "\n")

    # --- ссылки
    if res.links:
        out.append(f"{h2} {t(lang, 's_links')} ({len(res.links)})\n")
        groups: dict[str, list[dict[str, Any]]] = {}
        for link in res.links:
            groups.setdefault(link["class"], []).append(link)
        for cls in LINK_CLASS_ORDER + sorted(set(groups) - set(LINK_CLASS_ORDER)):
            items = groups.get(cls)
            if not items:
                continue
            out.append(f"**{cls}** ({len(items)})\n")
            out.append(f"| {t(lang, 'col_text')} | {t(lang, 'col_url')} | {t(lang, 'col_decision')} | {t(lang, 'col_where')} |\n|---|---|---|---|")
            for link in items[:150]:
                decision = t(lang, link["decision"]) if link.get("decision") else ""
                text = link["text"] or link.get("context", "")
                out.append(f"| {_cell(text, 70)} | {_cell(link['url'], 150)} | {_cell(decision, 60)} | {_cell(link.get('where'), 120)} |")
            if len(items) > 150:
                out.append(t(lang, "more_items", n=len(items) - 150))
            out.append("")

    # --- дочерние страницы
    if res.children:
        out.append(f"{h2} {t(lang, 's_children')} ({len(res.children)})\n")
        for child in res.children:
            out.append(render_page_md(child, settings, lang, level + 2))
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- запись файлов

def write_group(run_dir: Path, index: int, res: PageResult, settings: Any, lang: str) -> str:
    """Записывает отчёт по одному адресу (Markdown и JSON). Возвращает имя файла без расширения."""
    stem = f"{index:02d}-{slugify(res.url)}"
    md = render_page_md(res, settings, lang)
    if settings.authorship_mark:
        md += f"\n---\n{settings.authorship_mark}\n"
    (run_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    (run_dir / f"{stem}.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return stem


def _walk(res: PageResult):
    yield res
    for child in res.children:
        yield from _walk(child)


def _total_jobs(res: PageResult) -> int:
    return sum(p.jobs.get("best", 0) for p in [res] + [c for c in res.children if c.relation == "pagination"] if p.jobs)


_SITE_LIMIT_SECTION = {
    "max_pagination_pages": "run", "max_pages_per_start": "run",
    "max_scrolls": "limits", "max_expansions": "limits", "max_jobs_per_page": "limits",
}


def _limit_sites_section(summary: Any, lang: str) -> list[str]:
    """Сайты, у которых сработал лимит, который можно поднять отдельно для сайта, с готовой строкой настройки."""
    from urllib.parse import urlsplit
    rows: list[str] = []
    for top in summary.results:
        hits = {}
        for res in _walk(top):
            for h in res.limits_hit:
                if h["limit"] in _SITE_LIMIT_SECTION and isinstance(h.get("value"), (int, float)):
                    hits[h["limit"]] = max(hits.get(h["limit"], 0), h["value"])
        if not hits:
            continue
        host = (urlsplit(top.url).hostname or top.url)
        parts: dict[str, dict[str, int]] = {}
        for name, value in hits.items():
            parts.setdefault(_SITE_LIMIT_SECTION[name], {})[name] = int(value)
        rows.append(f"- {host}: " + "; ".join(_fmt_limit(lang, {"limit": n, "value": v}) for n, v in hits.items()))
        snippet = json.dumps({host: parts}, ensure_ascii=False)
        rows.append(f"  `\"site_overrides\": {snippet}`  ({t(lang, 'limit_site_hint')})")
    if not rows:
        return []
    return [f"## {t(lang, 's_limit_sites')}\n", *rows, ""]


def _roles_rows(items: list[dict[str, Any]], with_region: bool = False) -> list[str]:
    rows = ["| Роль | Название | Место | Ссылка | Откуда |" + (" Регион |" if with_region else ""),
            "|---|---|---|---|---|" + ("---|" if with_region else "")]
    for e in items:
        roles = ", ".join(r["label"].split(" / ")[0] for r in e["roles"])
        extra = f" {e['excluded_region']} |" if with_region else ""
        langs = f" [{'/'.join(e['languages'])}]" if e.get("languages") else ""
        rows.append(f"| {roles} | {_cell(e['title'], 90)}{langs} | {_cell(e['location'], 40)} | {_cell(e['url'], 160)} | "
                    f"{_cell(urlsplit(e['page']).hostname or '', 40)} |" + extra)
    return rows


def _roles_section(summary: Any, lang: str) -> list[str]:
    matches = getattr(summary, "role_matches", None)
    if matches is None or not getattr(summary, "roles_source", ""):
        return []
    good = [e for e in matches if e["level"] == "title" and not e["excluded_region"]]
    weak = [e for e in matches if e["level"] == "text" and not e["excluded_region"]]
    excluded = [e for e in matches if e["excluded_region"]]
    out = [f"## {t(lang, 's_roles')} ({len(good)})\n"]
    out.extend(_roles_rows(good) if good else [t(lang, "roles_none")])
    out.append("")
    if weak:
        out.append(f"## {t(lang, 's_roles_text')} ({len(weak)})\n")
        out.extend(_roles_rows(weak))
        out.append("")
    if excluded:
        out.append(f"## {t(lang, 's_roles_excluded')} ({len(excluded)})\n")
        out.extend(_roles_rows(excluded, with_region=True))
        out.append("")
    out.append(f"_{t(lang, 'roles_disclaimer')}_\n")
    return out


def render_index_md(summary: Any, settings: Any, lang: str) -> str:
    out: list[str] = [f"# {t(lang, 'report_title')} {summary.run_id}\n"]
    out.append(f"- web_reader {__version__} · browser: {summary.browser_label}")
    out.append(f"- {summary.started} → {summary.finished} · pages: {summary.pages_used}/{summary.pages_budget}")
    out.append("")

    attention: list[str] = []
    for top in summary.results:
        for res in _walk(top):
            if res.status == "ok" and res.kind not in ("closed_posting", "not_found"):
                continue
            if res.status == "skipped" and res.relation != "start":
                continue
            reasons = [t(lang, "status_" + res.status)]
            if res.skip_reason:
                reasons.append(res.skip_reason)
            if res.error:
                reasons.append(res.error)
            reasons += [_fmt_limit(lang, h) for h in res.limits_hit]
            reasons += [_fmt_warning(lang, w) for w in res.warnings if w["severity"] == "warn"]
            if res.kind in ("closed_posting", "not_found"):
                reasons.append(t(lang, "kind_" + res.kind))
            attention.append(f"- {res.final_url or res.url}: " + "; ".join(reasons))
    out.append(f"## {t(lang, 's_unread')}\n")
    out.extend(attention or [t(lang, "none")])
    out.append("")

    out.extend(_roles_section(summary, lang))
    out.extend(_limit_sites_section(summary, lang))

    out.append(f"## {t(lang, 's_all')}\n")
    out.append(f"| {t(lang, 'f_url')} | {t(lang, 'col_status')} | {t(lang, 'f_kind')} | {t(lang, 's_jobs')} | {t(lang, 'col_children')} | file |\n|---|---|---|---|---|---|")
    for top, stem in zip(summary.results, summary.stems):
        if top.status == "skipped":
            continue
        kids = list(_walk(top))[1:]
        ok_kids = sum(1 for k in kids if k.status == "ok")
        out.append(f"| {_cell(top.url, 80)} | {t(lang, 'status_' + top.status)} | {t(lang, 'kind_' + top.kind)} | "
                   f"{_total_jobs(top)} | {ok_kids}/{len(kids)} | {stem}.md |")
    out.append("")

    skipped = [r for r in summary.results if r.status == "skipped"]
    out.append(f"## {t(lang, 's_not_processed')}\n")
    out.extend([f"- {r.url}: {r.skip_reason}" for r in skipped] or [t(lang, "none")])
    out.append("")
    if summary.input_problems:
        out.append(f"## {t(lang, 's_input_problems')}\n")
        out.extend(f"- {p}" for p in summary.input_problems)
        out.append("")
    if summary.settings_warnings:
        out.append(f"## {t(lang, 's_settings_warnings')}\n")
        out.extend(f"- {w}" for w in summary.settings_warnings)
        out.append("")
    if settings.authorship_mark:
        out.append(f"---\n{settings.authorship_mark}")
    return "\n".join(out)


def write_index(run_dir: Path, summary: Any, settings: Any, lang: str) -> None:
    if getattr(summary, "roles_source", ""):
        (run_dir / "roles.json").write_text(json.dumps(summary.role_matches, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "index.md").write_text(render_index_md(summary, settings, lang), encoding="utf-8")
    brief = {
        "run_id": summary.run_id, "started": summary.started, "finished": summary.finished,
        "browser": summary.browser_label, "pages_used": summary.pages_used, "pages_budget": summary.pages_budget,
        "role_matches": len([e for e in getattr(summary, "role_matches", []) if e["level"] == "title" and not e["excluded_region"]]),
        "input_problems": summary.input_problems, "settings_warnings": summary.settings_warnings,
        "results": [
            {"file": stem, "url": r.url, "status": r.status, "kind": r.kind, "jobs": _total_jobs(r),
             "children": len(list(_walk(r))) - 1}
            for r, stem in zip(summary.results, summary.stems)
        ],
    }
    (run_dir / "summary.json").write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
