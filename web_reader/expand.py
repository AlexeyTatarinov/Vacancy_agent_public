"""Раскрытие содержимого: прокрутка, «Показать ещё», вкладки, аккордеоны.

Работает с главной страницей и с встроенными окнами (iframe). Каждый цикл
ограничен: числом прокруток и раскрытий, правилом «нет нового три раза подряд»,
лимитом вакансий, лимитом сетевых запросов и общим сроком.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .budget import Deadline
from .dom import FIND_JS, LOADMORE_RE, SCROLL_JS, SIGNATURE_JS, SKIP_RE
from .harvest import Harvester
from .network import Watcher
from .popups import click_marked, dismiss_popups


class NavigatedAway(Exception):
    """Клик увёл браузер со страницы."""


@dataclass
class ExploreStats:
    scrolls: int = 0
    expansions: int = 0
    clicks: list[str] = field(default_factory=list)
    stop_reason: str = ""
    limits_hit: list[dict[str, Any]] = field(default_factory=list)
    new_tabs: list[str] = field(default_factory=list)
    navigated_away: bool = False
    popups_closed: list[str] = field(default_factory=list)
    obstructions: list[str] = field(default_factory=list)

    def hit(self, name: str, **detail: Any) -> None:
        if not any(h["limit"] == name for h in self.limits_hit):
            self.limits_hit.append({"limit": name, **detail})


def _same_page(a: str, b: str) -> bool:
    return a.split("#")[0] == b.split("#")[0]


async def _find(root: Any, kind: str, pattern: str, skip: str, limit: int = 8) -> list[dict[str, Any]]:
    try:
        return await root.evaluate(FIND_JS, {"kind": kind, "re": pattern, "skip": skip, "limit": limit})
    except Exception:
        return []


async def explore(page: Any, root: Any, source: str, harvester: Harvester, watcher: Watcher,
                  limits: Any, ats_domains: list[str], deadline: Deadline, stats: ExploreStats,
                  *, allow_navigation_check: bool = True) -> None:
    """Раскрывает содержимое root (страницы или фрейма) и добавляет увиденное в harvester."""
    no_new = 0
    handled_toggles: set[str] = set()
    futile_loadmore: set[str] = set()
    start_url = page.url
    last_round_progress = True

    while True:
        if deadline.expired():
            stats.stop_reason = "timeout"
            stats.hit("timeout", value=deadline.seconds)
            return
        if watcher.cap_hit:
            stats.stop_reason = "max_requests_per_page"
            stats.hit("max_requests_per_page", value=watcher.max_requests)
            return
        if harvester.job_like_count(page.url, ats_domains) >= limits.max_jobs_per_page:
            stats.stop_reason = "max_jobs_per_page"
            stats.hit("max_jobs_per_page", value=limits.max_jobs_per_page)
            return
        if harvester.truncated:
            stats.stop_reason = "max_text_chars"
            stats.hit("max_text_chars", value=limits.max_text_chars)
            return

        did_action = False
        progressed = False

        # 1. прокрутка вниз (включая внутренние прокручиваемые блоки)
        if stats.scrolls < limits.max_scrolls:
            stats.scrolls += 1
            did_action = True
            try:
                await root.evaluate(SCROLL_JS)
            except Exception:
                pass
            await watcher.settle(deadline.remaining())
            lines, links = await harvester.snapshot(root, f"scroll {stats.scrolls}", source)
            progressed |= (lines + links) > 0

        # 2. кнопки «Показать ещё»
        if stats.expansions < limits.max_expansions and not deadline.expired():
            for cand in await _find(root, "loadmore", LOADMORE_RE, SKIP_RE, 5):
                if cand["sig"] in futile_loadmore:
                    continue
                before = (await _signature(root))
                if not await click_marked(root, cand["id"]):
                    continue
                stats.expansions += 1
                stats.clicks.append(f"load more: {cand['text']}")
                did_action = True
                await _after_click(page, watcher, deadline, stats, start_url, allow_navigation_check)
                lines, links = await harvester.snapshot(root, f"after click: {cand['text']}", source)
                changed = (lines + links) > 0 or before != await _signature(root)
                progressed |= changed
                if not changed:
                    futile_loadmore.add(cand["sig"])  # кнопка ничего не дала, больше не жмём
                break

        # 3. вкладки, аккордеоны, закрытые блоки (до трёх за раунд)
        for _ in range(3):
            if stats.expansions >= limits.max_expansions or deadline.expired():
                break
            cands = [c for c in await _find(root, "toggle", ".*", SKIP_RE, 12) if c["sig"] not in handled_toggles]
            if not cands:
                break
            cand = cands[0]
            handled_toggles.add(cand["sig"])
            if not await click_marked(root, cand["id"]):
                continue
            stats.expansions += 1
            stats.clicks.append(f"expand: {cand['text'] or cand['tag']}")
            did_action = True
            await _after_click(page, watcher, deadline, stats, start_url, allow_navigation_check)
            lines, links = await harvester.snapshot(root, f"expanded: {cand['text'] or cand['tag']}", source)
            progressed |= (lines + links) > 0

        # окна могут появиться с задержкой (рассылка, опрос): закрываем
        if did_action and not deadline.expired():
            result = await dismiss_popups(page, deadline.remaining(), max_rounds=1)
            stats.popups_closed.extend(result["dismissed"])
            stats.obstructions = result["obstructions"]

        previous_progress = last_round_progress
        last_round_progress = progressed
        no_new = 0 if progressed else no_new + 1
        if no_new >= limits.no_new_rounds_to_stop:
            stats.stop_reason = "no_new_content"
            return
        if not did_action:
            # Нечего делать: прокрутки исчерпаны, раскрывать нечего.
            # Лимит «сработал», только если до этого содержимое ещё прибывало.
            if stats.scrolls >= limits.max_scrolls and previous_progress:
                stats.stop_reason = "max_scrolls"
                stats.hit("max_scrolls", value=limits.max_scrolls)
            else:
                stats.stop_reason = "nothing_more_to_do"
            return


async def note_remaining_expandables(root: Any, limits: Any, stats: ExploreStats) -> None:
    """Если упёрлись в лимит раскрытий, проверяет, остались ли нераскрытые элементы."""
    if stats.expansions < limits.max_expansions:
        return
    left = await _find(root, "toggle", ".*", SKIP_RE, 5) + await _find(root, "loadmore", LOADMORE_RE, SKIP_RE, 5)
    if left:
        stats.hit("max_expansions", value=limits.max_expansions, remaining=[c["text"] for c in left[:5]])


async def _signature(root: Any) -> list[int]:
    try:
        return await root.evaluate(SIGNATURE_JS)
    except Exception:
        return []


async def _after_click(page: Any, watcher: Watcher, deadline: Deadline, stats: ExploreStats,
                       start_url: str, check_navigation: bool) -> None:
    await watcher.settle(deadline.remaining(), pause=0.3, idle=1.2)
    if not check_navigation:
        return
    if not _same_page(page.url, start_url):
        stats.navigated_away = True
        try:
            await page.go_back(timeout=min(5000, int(deadline.remaining() * 1000) + 500))
        except Exception:
            pass
        if not _same_page(page.url, start_url):
            stats.hit("navigated_away", url=page.url)
            raise NavigatedAway(page.url)
        stats.clicks.append("click navigated away; returned to the page")
