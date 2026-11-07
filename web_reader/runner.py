"""Запуск по списку адресов: чтение, переход по ссылкам на заданную глубину,
листание списков (пагинация), быстрые проверки остальных ссылок, отчёты.

Все решения по каждой ссылке записываются в отчёт: ни одна найденная ссылка
не исчезает молча.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import async_playwright

from .browser import context_options, launch_browser
from .budget import RunBudget
from .fetch import probe_link, read_pdf
from .i18n import t
from .reader import PageResult, RunContext, apply_chain_totals, read_page
from .report import write_group, write_index
from .safety import normalize_url, url_key

_RUN_DIR_RE = re.compile(r"^\d{8}-\d{6}$")
_PRIORITY = {"ats_board": 0, "ats_job": 1, "job_internal": 1, "pdf": 2, "internal": 3}


@dataclass
class InputItem:
    url: str
    note: str = ""


@dataclass
class RunSummary:
    run_id: str
    run_dir: Path
    results: list[PageResult] = field(default_factory=list)
    stems: list[str] = field(default_factory=list)
    input_problems: list[str] = field(default_factory=list)
    settings_warnings: list[str] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    browser_label: str = ""
    pages_used: int = 0
    pages_budget: int = 0
    role_matches: list[dict[str, Any]] = field(default_factory=list)
    roles_source: str = ""


def parse_input_lines(lines: list[str], lang: str) -> tuple[list[InputItem], list[str]]:
    """Строка: адрес, затем необязательная пометка после пробела или «|». Пустые строки и # игнорируются."""
    items: list[InputItem] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s*\|\s*|\s+", line, maxsplit=1)
        candidate = parts[0]
        note = parts[1].strip() if len(parts) > 1 else ""
        norm = normalize_url(candidate)
        if not norm:
            problems.append(t(lang, "bad_line", n=number, text=line[:80]))
            continue
        key = url_key(norm)
        if key in seen:
            continue
        seen.add(key or norm)
        items.append(InputItem(norm, note))
    return items, problems


def _trim(res: PageResult, limit: int) -> None:
    """Для страниц, открытых по ссылке, оставляет в результате лишь начало текста."""
    used = 0
    kept = []
    for section in res.sections:
        room = limit - used
        if room <= 0:
            res.text_truncated = True
            break
        if len(section["text"]) > room:
            section = {**section, "text": section["text"][:room]}
            res.text_truncated = True
        used += len(section["text"])
        kept.append(section)
    res.sections = kept


def _mark_visited(rc: RunContext, res: PageResult) -> None:
    for candidate in (res.url, res.final_url):
        key = url_key(candidate) if candidate else None
        if key:
            rc.visited[key] = res.url


_RETRY_HTTP = (403, 429, 503)


@dataclass
class RetryEntry:
    res: PageResult                 # результат, который будет обновлён на месте
    parent: PageResult | None
    link: dict[str, Any] | None
    due_index: int                  # после какого по счёту стартового адреса возвращаться
    attempts: int = 0


_NOT_RETRYABLE = {"cloudflare", "cloudflare_challenge", "perimeterx", "imperva", "datadome", "captcha"}


def _is_retryable(res: PageResult) -> bool:
    """Отказ «слишком часто / доступ закрыт» (access denied, 403, 429, 503) повторяем позже.
    Капчи и проверки браузера (Cloudflare и подобные) не повторяем: это не вопрос частоты запросов."""
    if res.status != "blocked" or res.antibot in _NOT_RETRYABLE:
        return False
    return res.antibot == "access_denied" or res.http_status in _RETRY_HTTP


class Runner:
    def __init__(self, rc: RunContext, log: Callable[[str], None]):
        self.rc = rc
        self.settings = rc.settings
        self.log = log
        self.lang = rc.lang
        self.retry_queue: list[RetryEntry] = []
        self.start_index = 0            # номер стартового адреса, который читается сейчас

    def _enqueue_retry(self, res: PageResult, parent: PageResult | None, link: dict[str, Any] | None) -> None:
        if not _is_retryable(res) or self.settings.run.max_retry_attempts <= 0:
            return
        self.retry_queue.append(RetryEntry(res, parent, link, self.start_index + self.settings.run.retry_after_starts))

    async def run_retries(self, index: int, final: bool) -> None:
        """Возвращается к страницам, которым сайт отказал. Не в конце запуска: когда накопилось retry_after_starts
        других адресов. В конце запуска (final): с паузами, пока не кончатся попытки. Защита не обходится."""
        run = self.settings.run
        rc = self.rc
        rc.budget.item_limit = None
        while True:
            due = [e for e in self.retry_queue if final or e.due_index <= index]
            if not due:
                return
            if final:
                pause = run.retry_pause_s * (min(e.attempts for e in due) + 1)
                self.log(f"retry pause {pause}s ({len(due)} pages)")
                await asyncio.sleep(pause)
            for entry in due:
                self.retry_queue.remove(entry)
                if not rc.budget.can_read_page()[0]:
                    self._finish_retry(entry, recovered=False)
                    continue
                entry.attempts += 1
                old = entry.res
                self.log(t(self.lang, "reading", url=old.url) + f" (retry {entry.attempts})")
                new = await read_page(rc, old.url, depth=old.depth, mode=old.mode, relation=old.relation,
                                      link_text=old.link_text)
                if new.status not in ("blocked", "skipped"):
                    await self._apply_recovered(entry, new)
                elif entry.attempts >= run.max_retry_attempts:
                    self._finish_retry(entry, recovered=False)
                else:
                    entry.due_index = index + run.retry_after_starts
                    self.retry_queue.append(entry)
                if not final:
                    await asyncio.sleep(run.delay_between_pages_s)
            if not final:
                return

    def _finish_retry(self, entry: RetryEntry, recovered: bool) -> None:
        if not recovered:
            entry.res.warnings.append({"code": "w_retry_failed", "severity": "warn", "attempts": entry.attempts})

    async def _apply_recovered(self, entry: RetryEntry, new: PageResult) -> None:
        old = entry.res
        children = old.children
        old.__dict__.update(new.__dict__)
        old.children = children
        old.warnings.append({"code": "w_retry_recovered", "severity": "info", "attempt": entry.attempts})
        _mark_visited(self.rc, old)
        if entry.link is not None:
            self._record(entry.link, old, "d_read_full" if old.mode == "full" else "d_read_light")
        parent = entry.parent
        if old.mode == "light":
            _trim(old, self.settings.run.followed_text_chars)
        if old.relation == "pagination" and parent is not None:
            await self.follow_pagination(parent, current=old)
        else:
            await self.expand_result(old)

    # ------------------------------------------------------------ верхний уровень
    async def process(self, item: InputItem) -> PageResult:
        key = url_key(item.url)
        if key:
            self.rc.visited.setdefault(key, item.url)
        res = await read_page(self.rc, item.url, depth=0, mode="full", relation="start", link_text=item.note)
        _mark_visited(self.rc, res)
        self._enqueue_retry(res, None, None)
        await self.expand_result(res)
        return res

    async def expand_result(self, res: PageResult) -> None:
        """Листает список (если он постраничный) и переходит по ссылкам страницы и её продолжений."""
        if res.status not in ("ok", "partial"):
            return
        pages = [res]
        if res.mode == "full":
            pages += await self.follow_pagination(res)
        if res.depth < self.settings.run.max_depth:
            for page in pages:
                await self.follow_links(page)

    # ------------------------------------------------------------ пагинация
    async def follow_pagination(self, res: PageResult, current: PageResult | None = None) -> list[PageResult]:
        """Читает следующие страницы списка. С current продолжает цепочку с этой страницы (после успешного повтора)."""
        run = self.settings.run
        chain: list[PageResult] = [c for c in res.children if c.relation == "pagination"] if current is not None else []
        current = current or res
        while len(chain) < run.max_pagination_pages:
            link = next((l for l in current.links if l["class"] == "pagination"), None)
            if link is None:
                break
            key = url_key(link["url"])
            if key and key in self.rc.visited:
                link["decision"] = "d_skip_dup"
                break
            self.log(t(self.lang, "reading", url=link["url"]))
            child = await read_page(self.rc, link["url"], depth=res.depth, mode="full",
                                    relation="pagination", link_text=link["text"])
            _mark_visited(self.rc, child)
            self._record(link, child, "d_pagination")
            res.children.append(child)
            chain.append(child)
            if child.status not in ("ok", "partial"):
                self._enqueue_retry(child, res, link)
                break
            current = child
            await asyncio.sleep(self.settings.run.delay_between_pages_s)
        if len(chain) >= run.max_pagination_pages:
            nxt = next((l for l in current.links if l["class"] == "pagination"), None)
            nkey = url_key(nxt["url"]) if nxt else None
            if nxt and not (nkey and nkey in self.rc.visited):
                if not any(h["limit"] == "max_pagination_pages" for h in res.limits_hit):
                    res.limits_hit.append({"limit": "max_pagination_pages", "value": run.max_pagination_pages})
                if res.status == "ok":
                    res.status = "partial"
        apply_chain_totals(res, chain)
        return chain

    # ------------------------------------------------------------ ссылки страницы
    async def follow_links(self, page: PageResult) -> None:
        run = self.settings.run
        rc = self.rc
        own_key = url_key(page.final_url or page.url)
        browser_left = run.max_followed_per_page
        pdf_left = run.max_pdfs_per_page
        probe_candidates: list[dict[str, Any]] = []
        ordered = sorted(enumerate(page.links), key=lambda p: (_PRIORITY.get(p[1]["class"], 9), p[0]))

        for _, link in ordered:
            cls, url = link["class"], link["url"]
            if link.get("decision"):
                continue
            if cls in ("mailto", "tel"):
                link["decision"] = "d_mail"
                continue
            if cls == "anchor":
                link["decision"] = "d_anchor"
                continue
            if cls in ("javascript", "image", "document", "other"):
                link["decision"] = "d_skip_type"
                continue
            if cls == "social":
                link["decision"] = "d_skip_social"
                continue
            if cls == "pagination":
                link["decision"] = "d_listed"
                continue
            key = url_key(url)
            if key and (key in rc.visited or key == own_key):
                link["decision"] = "d_skip_dup"
                link["where"] = rc.visited.get(key, "")
                continue
            if key:
                rc.visited.setdefault(key, url)

            if cls == "pdf":
                if pdf_left > 0:
                    pdf_left -= 1
                    child = await self._read_pdf(url, page, link)
                    page.children.append(child)
                    self._record(link, child, "d_pdf")
                else:
                    link["decision"] = "d_skip_budget"
                continue

            if cls == "external":
                if not run.follow_external:
                    link["decision"] = "d_skip_external"
                    continue
                browse_mode = "light"
            elif cls in ("ats_board", "ats_job") and not run.follow_ats:
                link["decision"] = "d_skip_external"
                continue
            else:
                browse_mode = "full" if cls == "ats_board" else "light"

            if cls in ("ats_board", "ats_job", "job_internal", "external") and browser_left > 0 and rc.budget.can_read_page()[0]:
                browser_left -= 1
                self.log(t(self.lang, "reading", url=url))
                child = await read_page(rc, url, depth=page.depth + 1, mode=browse_mode,
                                        relation=cls, link_text=link["text"] or link["context"][:60])
                _mark_visited(rc, child)
                if browse_mode == "light":
                    _trim(child, run.followed_text_chars)
                self._record(link, child, "d_read_full" if browse_mode == "full" else "d_read_light")
                page.children.append(child)
                self._enqueue_retry(child, page, link)
                await self.expand_result(child)
                await asyncio.sleep(run.delay_between_pages_s)
                continue

            if cls in ("ats_board", "ats_job", "job_internal", "external"):
                link["decision"] = "d_skip_budget"   # превышен лимит открытий: пробуем быстрой проверкой
            probe_candidates.append(link)

        await self._probe_many(page, probe_candidates)

    # ------------------------------------------------------------ быстрые проверки
    async def _probe_many(self, page: PageResult, links: list[dict[str, Any]]) -> None:
        run = self.settings.run
        rc = self.rc
        allowed = links[: run.max_probes_per_page]
        for link in links[run.max_probes_per_page:]:
            link["decision"] = link.get("decision") or "d_not_probed"
        semaphore = asyncio.Semaphore(5)

        async def one(link: dict[str, Any]) -> None:
            if not rc.budget.can_probe():
                link["decision"] = link.get("decision") or "d_not_probed"
                return
            rc.budget.use_probe()
            async with semaphore:
                info = await asyncio.to_thread(probe_link, link["url"], self.settings.safety)
            link["probe"] = info
            link["decision"] = "d_probe"
            if info["ok"]:
                label = info["title"] or info["h1"] or info["content_type"]
                link["where"] = f"{info['final_url']} — {label}" if info["redirected"] or label else info["final_url"]
            else:
                link["where"] = f"{info['final_url']} — {info['error'] or info['status']}"

        await asyncio.gather(*(one(l) for l in allowed))

    async def _read_pdf(self, url: str, parent: PageResult, link: dict[str, Any]) -> PageResult:
        limits = self.settings.limits
        info = await asyncio.to_thread(read_pdf, url, self.settings.safety,
                                       max_bytes=limits.max_pdf_bytes, max_pages=limits.max_pdf_pages)
        res = PageResult(url=url, depth=parent.depth + 1, mode="light", relation="pdf",
                         link_text=link["text"], final_url=info["final_url"], http_status=info["status"])
        res.kind = "pdf"
        res.pdf = info
        res.title = link["text"] or url.rsplit("/", 1)[-1]
        if info["ok"]:
            res.status = "ok" if info["text"] else "partial"
            if not info["text"]:
                res.warnings.append({"code": "w_empty", "severity": "warn"})
        else:
            res.status = "error"
            res.error = info["error"]
        return res

    def _record(self, link: dict[str, Any], child: PageResult, decision: str) -> None:
        if child.status == "skipped":
            reason = child.skip_reason or ""
            link["decision"] = "d_skip_blocked" if reason.startswith("forbidden") else "d_skip_budget"
            link["where"] = reason
            return
        link["decision"] = decision
        label = child.title or child.h1 or ""
        link["where"] = f"{child.final_url or child.url} — {label} [{child.kind}]"


async def run_urls(items: list[InputItem], settings: Any, out_dir: Path, lang: str,
                   log: Callable[[str], None] = print, input_problems: list[str] | None = None) -> RunSummary:
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = RunSummary(run_id=run_id, run_dir=run_dir, input_problems=list(input_problems or []),
                         settings_warnings=list(settings.warnings), started=now.isoformat(timespec="seconds"))
    run = settings.run
    budget = RunBudget(run.max_pages_per_run, run.max_run_seconds, run.max_probes_per_run)
    summary.pages_budget = run.max_pages_per_run
    log(t(lang, "run_start", run_id=run_id, n=len(items)))

    async with async_playwright() as playwright:
        browser, label = await launch_browser(playwright, settings)
        summary.browser_label = label
        try:
            rc = RunContext(settings=settings, browser=browser, budget=budget,
                            context_options=context_options(settings, browser), run_dir=run_dir, lang=lang)
            runner = Runner(rc, log)
            for index, item in enumerate(items, start=1):
                log(t(lang, "reading", url=item.url))
                site = settings.for_url(item.url)
                rc.settings = runner.settings = site
                budget.start_item(site.run.max_pages_per_start)
                runner.start_index = index
                res = await runner.process(item)
                res.site_label = site.site_label
                _mark_limits_for_index(res, site)
                summary.results.append(res)
                summary.stems.append(write_group(run_dir, index, res, settings, lang))  # сразу на диск
                write_index(run_dir, _partial(summary, budget), settings, lang)
                await asyncio.sleep(run.delay_between_pages_s)
                if index < len(items):
                    await runner.run_retries(index, final=False)     # вернуться к отказанным страницам через несколько адресов
            await runner.run_retries(len(items), final=True)           # адресов больше нет: повторы с паузами
            _rewrite_reports(summary, run_dir, settings, lang)
            _apply_roles(summary, settings)
        finally:
            await browser.close()

    final = _partial(summary, budget)
    final.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_index(run_dir, final, settings, lang)
    (Path(out_dir) / "LATEST.txt").write_text(run_id + "\n", encoding="utf-8")
    prune_runs(Path(out_dir), settings.output.keep_runs)
    log(t(lang, "run_done", path=str(run_dir / "index.md")))
    return summary


def _apply_roles(summary: RunSummary, settings: Any) -> None:
    """Отбор вакансий по словарю ролей. Нет файла словаря: предупреждение, остальное работает."""
    if not settings.roles.enabled:
        return
    from .roles import collect_matches, load_rules
    path = Path(settings.roles.file)
    if not path.exists():
        summary.settings_warnings.append(f"словарь ролей {path} не найден, отбор по ролям пропущен")
        return
    try:
        rules = load_rules(path)
    except (OSError, ValueError, KeyError) as exc:
        summary.settings_warnings.append(f"словарь ролей {path} не прочитан: {exc}")
        return
    summary.role_matches = collect_matches(summary.results, rules)
    summary.roles_source = str(path)


def _rewrite_reports(summary: RunSummary, run_dir: Path, settings: Any, lang: str) -> None:
    """После повторов результаты могли измениться на месте: переписываем отчёты по адресам."""
    for index, res in enumerate(summary.results, start=1):
        _mark_limits_for_index(res, settings.for_url(res.url))
        summary.stems[index - 1] = write_group(run_dir, index, res, settings, lang)


def _mark_limits_for_index(res: PageResult, site: Any) -> None:
    """Записывает на стартовую страницу пределы запуска, которые оборвали чтение сайта (для раздела отчёта «упёрлись в лимит»)."""
    kids = []
    stack = list(res.children)
    while stack:
        c = stack.pop()
        kids.append(c)
        stack.extend(c.children)
    if any(k.skip_reason == "item_page_budget" for k in kids):
        if not any(h["limit"] == "max_pages_per_start" for h in res.limits_hit):
            res.limits_hit.append({"limit": "max_pages_per_start", "value": site.run.max_pages_per_start})
        if res.status == "ok":
            res.status = "partial"


def _partial(summary: RunSummary, budget: RunBudget) -> RunSummary:
    summary.pages_used = budget.pages_used
    if not summary.finished:
        summary.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return summary


def prune_runs(out_dir: Path, keep: int) -> None:
    """Удаляет самые старые папки запусков, оставляя keep последних."""
    runs = sorted(p for p in out_dir.iterdir() if p.is_dir() and _RUN_DIR_RE.match(p.name))
    for old in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
