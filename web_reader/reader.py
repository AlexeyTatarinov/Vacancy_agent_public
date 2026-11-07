"""Чтение одной страницы: открыть, закрыть помехи, раскрыть, прочитать фреймы,
перехватить данные, разобрать ссылки, оценить полноту.

Результат накапливается в PageResult по ходу работы, поэтому при принудительной
остановке по времени возвращается всё, что успели прочитать, с честной пометкой.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

from . import antibot
from .budget import Deadline, RunBudget
from .classify import FRAME_READ_KINDS, classify_frame, classify_link, looks_like_job_url
from .dom import JSON_BLOCKS_JS, TEXT_JS
from .expand import ExploreStats, NavigatedAway, explore, note_remaining_expandables
from .harvest import Harvester
from .jobs import dedupe_jobs, declared_counts, parse_embedded_json, parse_jsonld
from .network import JsonCapture, Watcher
from .popups import dismiss_popups
from .safety import check_url, normalize_url

_CLOSED_RE = re.compile(
    r"no longer (accepting|available)|(job|position|posting|role|vacancy) (is|has been|was) (closed|filled|expired|removed|no longer)|"
    r"position (has been )?filled|posting has expired|this job (is )?(closed|expired)|"
    r"vacante (cerrada|no disponible)|ya no est[aá] disponible|oferta (cerrada|caducada)|"
    r"offre (expir[ée]e|n'est plus disponible)|stelle (ist )?(besetzt|nicht mehr verf)|"
    r"вакансия (закрыта|неактуальна|в архиве)|вакансія (закрита|неактуальна|в архіві)", re.I)
_NOT_FOUND_RE = re.compile(r"\b404\b|not found|page not found|no encontrad|introuvable|nicht gefunden|не найден|не знайден", re.I)
_APPLY_RE = re.compile(r"\bapply\b|responsibilities|requirements|qualifications|what you.ll do|about the role|"
                       r"solicitar|requisitos|postuler|bewerben|обязанности|требования|вимоги|обов.язки", re.I)
_LOGIN_RE = re.compile(r"sign in to (continue|view)|log in to (continue|view)|iniciar sesi[oó]n para|"
                       r"connectez-vous pour|войдите, чтобы|увійдіть, щоб", re.I)
_CAREERS_HINT = re.compile(r"career|jobs?\b|vacanc|openings|empleo|trabajo|emploi|karriere|stellen|вакан|вакансі|робота|работа", re.I)


_GENERIC_LINK_TEXT = re.compile(
    r"^(more|details?|more details?|more info(rmation)?|read more|learn more|know more|see more|view( job| role| details| more| position)?|"
    r"open( job| position)?|apply( now| here)?|click here|go|see details|job details?|"
    r"ver más|más información|leer más|detalles|en savoir plus|voir( l'offre| plus)?|mehr( erfahren| lesen)?|details ansehen|"
    r"подробнее|детальніше|докладніше|дізнатися більше|докладно)[\s.»>→›…]*$", re.I)


_APPLY_TEXT = re.compile(r"^(apply( now| here| today| for this job)?|view job description|view( the)? (job|role|position)( description| details)?|"
                         r"job description|apply!|подати заявку|подать заявку|отправить резюме|aplicar|postuler|bewerben)[\s.!»>→›…]*$", re.I)


@dataclass
class PageResult:
    url: str
    depth: int = 0
    mode: str = "full"            # full | light
    relation: str = "start"       # start | job | ats_board | pagination | ...
    link_text: str = ""
    final_url: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    title: str = ""
    h1: str = ""
    http_status: int | None = None
    status: str = "error"         # ok | partial | blocked | error | empty | skipped
    kind: str = "other"
    seconds: float = 0.0
    antibot: str | None = None
    error: str | None = None
    skip_reason: str | None = None
    limits_hit: list[dict[str, Any]] = field(default_factory=list)
    site_label: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    text_chars: int = 0
    text_truncated: bool = False
    links: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    network: list[dict[str, Any]] = field(default_factory=list)
    jobs: dict[str, Any] = field(default_factory=dict)
    popups: dict[str, Any] = field(default_factory=dict)
    expansion: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    screenshot: str | None = None
    pdf: dict[str, Any] | None = None
    probe: dict[str, Any] | None = None
    children: list["PageResult"] = field(default_factory=list)
    _html: str = field(default="", repr=False)      # отрисованный HTML (только при RunContext.keep_html)
    _state: Any = field(default=None, repr=False)
    _finalized: bool = field(default=False, repr=False)

    def full_text(self) -> str:
        return "\n".join(s["text"] for s in self.sections)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key.startswith("_"):
                continue
            if key == "children":
                out[key] = [c.to_dict() for c in value]
            else:
                out[key] = value
        return out


@dataclass
class RunContext:
    settings: Any
    browser: Any
    budget: RunBudget
    context_options: dict[str, Any]
    run_dir: Path | None = None
    lang: str = "ru"
    visited: dict[str, str] = field(default_factory=dict)   # url_key -> относительный путь отчёта
    shot_counter: int = 0
    keep_html: bool = False        # сохранять отрисованный HTML страницы (для внешних программ, например бота вакансий)


class _State:
    """Рабочие объекты чтения страницы; живут, пока страница открыта."""

    def __init__(self, settings: Any, url: str):
        self.harvester = Harvester(settings.limits.max_text_chars)
        self.watcher = Watcher(settings)
        self.jsoncap = JsonCapture(settings, url)
        self.stats = ExploreStats()
        self.frames: list[dict[str, Any]] = []
        self.json_ld: list[str] = []
        self.json_app: list[str] = []
        self.dialogs: list[str] = []
        self.new_tabs: list[str] = []
        self.popups: dict[str, Any] = {"dismissed": [], "obstructions": []}


def skipped_result(url: str, reason: str, **kwargs: Any) -> PageResult:
    res = PageResult(url=url, status="skipped", skip_reason=reason, **kwargs)
    res.kind = "other"
    return res


# ---------------------------------------------------------------- публичная точка входа

async def read_page(rc: RunContext, url: str, *, depth: int = 0, mode: str = "full",
                    relation: str = "start", link_text: str = "") -> PageResult:
    """Читает страницу с соблюдением всех лимитов. Никогда не бросает исключений."""
    res = PageResult(url=url, depth=depth, mode=mode, relation=relation, link_text=link_text)
    can, why = rc.budget.can_read_page()
    if not can:
        res.status, res.skip_reason = "skipped", why
        return res
    ok, reason = await check_url(url, rc.settings.safety)
    if not ok:
        res.status, res.skip_reason = "skipped", f"forbidden_address: {reason}"
        return res
    rc.budget.use_page()

    limits = rc.settings.limits
    soft = limits.page_timeout_s if mode == "full" else limits.light_page_timeout_s
    hard = soft + limits.hard_timeout_grace_s
    started = time.monotonic()
    try:
        await asyncio.wait_for(_read_inner(rc, res, Deadline(soft)), timeout=hard)
    except asyncio.TimeoutError:
        res.limits_hit.append({"limit": "hard_timeout", "value": hard})
    except Exception as exc:  # неожиданная ошибка не должна ронять весь запуск
        res.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:250] if str(exc) else ''}"
    res.seconds = round(time.monotonic() - started, 1)
    if not res._finalized:
        finalize(res, rc.settings)
    res._state = None
    return res


# ---------------------------------------------------------------- чтение

async def _on_dialog(dialog: Any, st: _State) -> None:
    try:
        st.dialogs.append(f"{dialog.type}: {dialog.message[:120]}")
        await dialog.dismiss()
    except Exception:
        pass


async def _on_new_tab(page: Any, st: _State) -> None:
    try:
        await asyncio.sleep(0.3)
        st.new_tabs.append(page.url)
        await page.close()
    except Exception:
        pass


async def _safe_close(context: Any) -> None:
    try:
        await asyncio.wait_for(context.close(), timeout=5)
    except Exception:
        pass


async def _read_inner(rc: RunContext, res: PageResult, deadline: Deadline) -> None:
    st = _State(rc.settings, res.url)
    res._state = st
    context = await rc.browser.new_context(**rc.context_options)
    try:
        page = await _collect(rc, res, deadline, st, context)
        # оценка полноты делается, пока окно ещё открыто, чтобы решить о снимке экрана
        if not res._finalized:
            finalize(res, rc.settings)
        if page is not None and rc.keep_html:
            try:
                res._html = (await asyncio.wait_for(page.content(), timeout=5))[:3_000_000]
            except Exception:
                res._html = ""
        if page is not None and res.status != "error":
            await _maybe_screenshot(rc, res, page)
    finally:
        await _safe_close(context)


async def _collect(rc: RunContext, res: PageResult, deadline: Deadline, st: _State, context: Any) -> Any:
    """Открывает страницу и собирает всё прочитанное. Возвращает страницу (None, если открыть не удалось)."""
    settings = rc.settings
    limits = settings.limits
    page = await context.new_page()
    await page.route("**/*", st.watcher.route)
    st.watcher.attach(page)
    st.jsoncap.attach(page)
    page.on("dialog", lambda d: asyncio.ensure_future(_on_dialog(d, st)))
    context.on("page", lambda p: asyncio.ensure_future(_on_new_tab(p, st)))  # после создания основной страницы

    # --- открытие
    response = None
    try:
        response = await page.goto(res.url, wait_until="domcontentloaded",
                                   timeout=int(min(20.0, max(1.0, deadline.remaining())) * 1000))
    except PWTimeout:
        res.limits_hit.append({"limit": "navigation_timeout"})
    except PWError as exc:
        message = str(exc)
        if "Download is starting" in message:
            res.kind, res.status, res.skip_reason = "download", "skipped", "download_not_saved"
            res._finalized = True
            return None
        res.error = message.splitlines()[0][:300]
        return None
    headers: dict[str, str] = {}
    if response is not None:
        res.http_status = response.status
        try:
            headers = await response.all_headers()
        except Exception:
            headers = {}
        chain: list[str] = []
        req = response.request
        while req.redirected_from:
            req = req.redirected_from
            chain.append(req.url)
        res.redirect_chain = list(reversed(chain))
    res.final_url = page.url
    await st.watcher.settle(deadline.remaining(), idle=2.5)
    res.title = (await _safe_title(page))[:300]

    # --- защита от ботов: определяем, ждём немного, не обходим
    probe = await _probe_text(page)
    code = antibot.detect(res.title, probe, res.http_status, headers)
    if code:
        for _ in range(8):
            if deadline.remaining() < 3:
                break
            await asyncio.sleep(1)
            res.title = (await _safe_title(page))[:300]
            probe = await _probe_text(page)
            code = antibot.detect(res.title, probe, None, {})
            if not code:
                break
        if code:
            res.antibot = code
            return page
        st.harvester = Harvester(limits.max_text_chars)  # текст проверки отбрасываем
        res.final_url = page.url

    # --- попапы и баннеры
    st.popups = await dismiss_popups(page, deadline.remaining())

    # --- первый снимок
    await st.harvester.snapshot(page, "initial", "page")
    await _collect_json_blocks(page, st)

    # --- раскрытие (только в полном режиме)
    if res.mode == "full":
        try:
            await explore(page, page, "page", st.harvester, st.watcher, limits,
                          settings.safety.ats_domains, deadline, st.stats)
        except NavigatedAway:
            pass
        await note_remaining_expandables(page, limits, st.stats)
        st.popups["dismissed"].extend(st.stats.popups_closed)
        if st.stats.obstructions:
            st.popups["obstructions"] = st.stats.obstructions

    # --- встроенные окна
    await _read_frames(page, rc, res, st, deadline)

    # --- итоговые снимки
    await st.harvester.snapshot(page, "final", "page")
    await _collect_json_blocks(page, st)
    await st.jsoncap.drain(min(3.0, max(0.2, deadline.remaining())))
    res.final_url = page.url
    res.title = (await _safe_title(page))[:300] or res.title
    return page


async def _safe_title(page: Any) -> str:
    try:
        return await asyncio.wait_for(page.title(), timeout=3)
    except Exception:
        return ""


async def _probe_text(page: Any) -> str:
    try:
        data = await page.evaluate(TEXT_JS, {"maxChars": 4000, "includeHidden": False})
        return data.get("text", "")
    except Exception:
        return ""


async def _collect_json_blocks(page: Any, st: _State) -> None:
    try:
        blocks = await page.evaluate(JSON_BLOCKS_JS)
    except Exception:
        return
    for raw in blocks.get("ld", []):
        if raw not in st.json_ld:
            st.json_ld.append(raw)
    for raw in blocks.get("app", []):
        if raw not in st.json_app:
            st.json_app.append(raw)


async def _read_frames(page: Any, rc: RunContext, res: PageResult, st: _State, deadline: Deadline) -> None:
    settings = rc.settings
    limits = settings.limits
    frames = [f for f in page.frames if f != page.main_frame]
    read_count = 0
    for frame in frames:
        url = frame.url
        kind = classify_frame(url, settings)
        rec: dict[str, Any] = {"url": url, "kind": kind, "name": frame.name or "", "read": False, "reason": ""}
        st.frames.append(rec)
        if kind not in FRAME_READ_KINDS:
            rec["reason"] = "kind_not_read"
            continue
        if read_count >= limits.max_frames:
            rec["reason"] = "max_frames"
            st.stats.hit("max_frames", value=limits.max_frames)
            continue
        if deadline.expired():
            rec["reason"] = "timeout"
            st.stats.hit("timeout", value=deadline.seconds)
            continue
        if kind != "inline":
            ok, why = await check_url(url, settings.safety)
            if not ok:
                rec["reason"] = f"forbidden: {why}"
                continue
        source = f"frame:{url}"
        lines, links = await st.harvester.snapshot(frame, f"frame {url}", source)
        if st.harvester.last_error:
            rec["reason"] = f"unreadable: {st.harvester.last_error}"
            res.warnings.append({"code": "w_frame_unreadable", "severity": "warn", "url": url})
            continue
        rec["read"] = True
        rec["new_lines"] = lines
        rec["new_links"] = links
        read_count += 1
        if kind == "presentation":
            res.notes.append({"code": "n_presentation", "url": url})
        if res.mode == "full" and kind in ("ats", "form", "other") and not deadline.expired():
            fstats = ExploreStats()
            fstats.expansions = st.stats.expansions  # общий лимит раскрытий на страницу
            try:
                await explore(page, frame, source, st.harvester, st.watcher, limits,
                              settings.safety.ats_domains, deadline, fstats, allow_navigation_check=False)
            except NavigatedAway:
                pass
            rec["stop_reason"] = fstats.stop_reason
            st.stats.expansions = fstats.expansions
            st.stats.clicks.extend(f"[frame] {c}" for c in fstats.clicks)
            st.stats.scrolls += fstats.scrolls
            for hit in fstats.limits_hit:
                st.stats.hit(hit["limit"], **{k: v for k, v in hit.items() if k != "limit"})


async def _maybe_screenshot(rc: RunContext, res: PageResult, page: Any) -> None:
    mode = rc.settings.output.screenshots
    if rc.run_dir is None or mode == "never":
        return
    if mode == "on_problem" and res.status == "ok":
        return
    try:
        rc.shot_counter += 1
        folder = rc.run_dir / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{rc.shot_counter:03d}-{_slug(res.url)}.jpg"
        height = await page.evaluate("(document.scrollingElement || document.documentElement).scrollHeight")
        await page.screenshot(path=str(folder / name), type="jpeg", quality=55,
                              full_page=height <= 12000, timeout=6000)
        res.screenshot = f"screenshots/{name}"
    except Exception:
        pass


def _slug(url: str) -> str:
    parts = urlsplit(url)
    raw = (parts.hostname or "page") + parts.path
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-")[:60] or "page"


# ---------------------------------------------------------------- итог (чистый Python, без браузера)

def finalize(res: PageResult, settings: Any) -> None:
    """Собирает итог из накопленного: ссылки, вакансии, счётчики, статус. Вызывается один раз."""
    res._finalized = True
    st: _State | None = res._state
    limits = settings.limits
    if st is None:
        res.status = "skipped" if res.status == "skipped" else ("error" if res.error else res.status)
        if res.status == "error" and not res.error:
            res.error = "no data collected"
        return

    base = res.final_url or res.url
    ats = settings.safety.ats_domains
    harvester = st.harvester
    res.sections = harvester.sections
    res.text_chars = harvester.total_chars
    res.text_truncated = harvester.truncated
    res.h1 = harvester.h1
    text = res.full_text()

    # --- ссылки
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in harvester.links.values():
        href = raw["href"]
        cls = classify_link(href, base, settings, raw["text"], raw.get("rel", ""), raw.get("aria", ""))
        norm = href if cls in ("javascript", "mailto", "tel") else (normalize_url(href, base) or href)
        key = (norm, raw["text"])
        if key in seen:
            continue
        seen.add(key)
        links.append({"text": raw["text"], "url": norm, "class": cls, "rel": raw.get("rel", ""),
                      "context": raw.get("context", "") if raw.get("context", "") != raw["text"] else "",
                      "heading": raw.get("heading", "") or "",
                      "card_title": raw.get("cardTitle", "") or "",
                      "source": raw.get("source", "page"), "visible": raw.get("visible", True)})
    res.links = links

    # --- вакансии из трёх независимых источников
    job_links: dict[str, dict[str, Any]] = {}
    card_titles: set[str] = set()
    for link in links:
        generic = len(link["text"]) < 4 or bool(_GENERIC_LINK_TEXT.match(link["text"])) or bool(_APPLY_TEXT.match(link["text"]))
        # «Apply Now» / «View Job Description» без понятного адреса: вакансия, если у карточки есть своё название
        applyish = (link["class"] in ("internal", "external") and bool(_APPLY_TEXT.match(link["text"]))
                    and bool(link.get("card_title")) and not _GENERIC_LINK_TEXT.match(link["card_title"])
                    and not _APPLY_TEXT.match(link["card_title"]))
        if (link["class"] in ("job_internal", "ats_job") or applyish) and link["url"] not in job_links:
            title = link["text"] if len(link["text"]) >= 4 else (link["context"][:100] or link["text"])
            from_card = False
            heading = link.get("heading") or ""
            if heading.rstrip().endswith(":") or _APPLY_TEXT.match(heading) or _GENERIC_LINK_TEXT.match(heading):
                heading = ""                       # подпись поля («Department:») или кнопка, а не название
            if heading and generic:
                title = heading                    # «More Details», «Apply»: название вакансии берём из заголовка карточки
                from_card = True
            elif generic and link.get("card_title"):
                title, from_card = link["card_title"], True
            if from_card:
                if title.lower() in card_titles:   # две ссылки одной карточки (описание и подача): одна вакансия
                    continue
                card_titles.add(title.lower())
            job_links[link["url"]] = {"title": title, "url": link["url"], "location": "",
                                      "context": link["context"][:200], "source": "page"}
    network_jobs: list[dict[str, Any]] = []
    network_total = 0
    for entry in st.jsoncap.entries.values():
        for job in entry["jobs"]:
            network_jobs.append({**job, "source": "network"})
        network_total += entry["job_total"]
    network_jobs = dedupe_jobs(network_jobs)
    jsonld = parse_jsonld(st.json_ld, limits.max_jobs_per_page)
    embedded, embedded_total = parse_embedded_json(st.json_app, base, limits.max_jobs_per_page)

    counts = {"links": len(job_links), "network": len(network_jobs), "jsonld": len(jsonld), "embedded": len(embedded)}
    best = max(counts["links"], counts["network"], counts["jsonld"], counts["embedded"])
    capped = (len(job_links) > limits.max_jobs_per_page or network_total > limits.max_jobs_per_page
              or any(h["limit"] == "max_jobs_per_page" for h in st.stats.limits_hit))
    declared = declared_counts(text)
    res.jobs = {
        "links": list(job_links.values())[: limits.max_jobs_per_page],
        "network": network_jobs[: limits.max_jobs_per_page],
        "jsonld": jsonld[: limits.max_jobs_per_page],
        "embedded": embedded[: limits.max_jobs_per_page],
        "counts": counts, "best": best, "capped": capped, "declared": declared,
    }

    # --- сеть, фреймы, попапы, раскрытие
    res.network = [
        {k: v for k, v in e.items() if k != "jobs"} | {"jobs_found": len(e["jobs"])}
        for e in st.jsoncap.entries.values()
    ]
    res.frames = st.frames
    res.popups = {"dismissed": st.popups.get("dismissed", []), "obstructions": st.popups.get("obstructions", [])}
    res.expansion = {"scrolls": st.stats.scrolls, "expansions": st.stats.expansions,
                     "clicks": st.stats.clicks, "stop_reason": st.stats.stop_reason}
    res.counts = {
        "links_total": len(links), "frames_total": len(st.frames),
        "frames_read": sum(1 for f in st.frames if f["read"]),
        "requests": st.watcher.requests, "blocked_requests": dict(st.watcher.blocked),
        "json_responses": len(st.jsoncap.entries),
    }
    for hit in st.stats.limits_hit:
        if not any(h["limit"] == hit["limit"] for h in res.limits_hit):
            res.limits_hit.append(hit)
    if st.watcher.cap_hit and not any(h["limit"] == "max_requests_per_page" for h in res.limits_hit):
        res.limits_hit.append({"limit": "max_requests_per_page", "value": st.watcher.max_requests})
    if st.jsoncap.dropped:
        res.limits_hit.append({"limit": "max_json_responses", "value": limits.max_json_responses})

    # --- примечания
    for url in st.new_tabs:
        res.notes.append({"code": "n_new_tab", "url": url})
    for message in st.dialogs:
        res.notes.append({"code": "n_dialog", "text": message})
    blocked_total = sum(st.watcher.blocked.values())
    if blocked_total:
        res.notes.append({"code": "n_blocked_requests", "n": blocked_total})
    if st.jsoncap.unreadable:
        res.notes.append({"code": "n_bad_json", "n": st.jsoncap.unreadable})
    if res.text_chars < 400:
        res.notes.append({"code": "n_shadow_closed"})

    # --- предупреждения
    if res.antibot:
        res.warnings.append({"code": "w_antibot", "severity": "warn", "detail": res.antibot})
    if res.popups["obstructions"]:
        res.warnings.append({"code": "w_obstruction", "severity": "warn", "text": res.popups["obstructions"][0][:100]})
    if res.text_truncated:
        res.warnings.append({"code": "w_text_truncated", "severity": "warn"})
    if capped:
        res.warnings.append({"code": "w_capped_jobs", "severity": "warn", "limit": limits.max_jobs_per_page})
    if declared["value"]:
        if declared["value"] > best and best < declared["value"] * 0.9:
            code = "w_declared_gt_collected" if declared["confidence"] == "high" else "w_declared_low"
            severity = "warn" if declared["confidence"] == "high" else "info"
            res.warnings.append({"code": code, "severity": severity,
                                 "declared": declared["value"], "collected": best})
    careers_like = bool(_CAREERS_HINT.search(res.url + " " + res.title)) and res.relation in ("start", "ats_board", "pagination")
    if (res.mode == "full" and careers_like and best == 0 and not res.antibot
            and res.text_chars >= 200):
        res.warnings.append({"code": "w_no_jobs", "severity": "warn"})

    # --- тип страницы и статус
    head = (res.title + " " + res.h1 + " " + text[:3000])
    res.kind = _guess_kind(res, text, head, counts)
    if res.kind == "closed_posting":
        res.warnings.append({"code": "w_closed_posting", "severity": "info"})
    _compute_status(res)


def _compute_status(res: PageResult) -> None:
    """Статус по итогам: blocked, error, empty, partial (лимиты или предупреждения) либо ok."""
    has_warn = any(w["severity"] == "warn" for w in res.warnings)
    if res.antibot:
        res.status = "blocked"
    elif res.error and res.text_chars == 0:
        res.status = "error"
    elif res.text_chars < 200 and not res.links:
        res.status = "empty"
        if not any(w["code"] == "w_empty" for w in res.warnings):
            res.warnings.append({"code": "w_empty", "severity": "warn"})
    elif res.limits_hit or has_warn:
        res.status = "partial"
    else:
        res.status = "ok"


def apply_chain_totals(res: PageResult, chain: list[PageResult]) -> None:
    """Сверка заявленного числа вакансий с суммой по всем страницам списка (пагинация):
    первая страница сама по себе всегда «меньше заявленного»."""
    if not chain or not res.jobs:
        return
    total = res.jobs.get("best", 0) + sum(c.jobs.get("best", 0) for c in chain if c.jobs)
    res.jobs["chain_total"] = total
    declared = res.jobs.get("declared", {}) or {}
    value = declared.get("value")
    res.warnings = [w for w in res.warnings if w["code"] not in ("w_declared_gt_collected", "w_declared_low")]
    if value and total < value * 0.9:
        high = declared.get("confidence") == "high"
        res.warnings.append({"code": "w_declared_gt_collected" if high else "w_declared_low",
                             "severity": "warn" if high else "info", "declared": value, "collected": total})
    _compute_status(res)


def _guess_kind(res: PageResult, text: str, head: str, counts: dict[str, int]) -> str:
    if res.antibot:
        return "blocked"
    if res.error and res.text_chars == 0:
        return "error"
    if _CLOSED_RE.search(text[:1500]) or _CLOSED_RE.search(res.title):
        return "closed_posting"
    if res.http_status in (404, 410) or _NOT_FOUND_RE.search(res.title + " " + res.h1):
        return "not_found"
    if _LOGIN_RE.search(text[:1500]) and res.text_chars < 1500:
        return "login"
    best_list = max(counts["links"], counts["network"], counts["embedded"])
    if counts["jsonld"] == 1 and best_list < 3:
        return "job_posting"
    if best_list >= 3 or counts["jsonld"] >= 3:
        return "job_listing"
    if looks_like_job_url(res.final_url or res.url, ()) and _APPLY_RE.search(head):
        return "job_posting"
    return "other"
