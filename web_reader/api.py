"""Программный интерфейс: прочитать один адрес и получить отрисованный HTML и вакансии.

Для внешних программ (например, бота вакансий), которым раньше нужен был платный сервис чтения страниц.
Вызов синхронный: внутри создаётся свой браузер, все пределы чтения действуют так же, как в командной строке.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from .browser import context_options, launch_browser
from .budget import RunBudget
from .reader import PageResult, RunContext
from .runner import InputItem, Runner
from .settings import load_settings


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int             # HTTP-код; 0 если страница не открылась
    html: str                    # отрисованный HTML стартовой страницы и страниц списка (пагинация), через разделитель
    status: str                  # ok | partial | blocked | error | empty | skipped
    kind: str
    jobs: list[dict[str, Any]] = field(default_factory=list)       # вакансии из ссылок, JSON страницы, JSON-LD
    pages: int = 1
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    result: PageResult | None = None


async def _fetch(url: str, settings: Any, light: bool) -> FetchResult:
    run = settings.run
    budget = RunBudget(run.max_pages_per_run, run.max_run_seconds, run.max_probes_per_run)
    budget.start_item(run.max_pages_per_start)
    async with async_playwright() as playwright:
        browser, _ = await launch_browser(playwright, settings)
        try:
            rc = RunContext(settings=settings, browser=browser, budget=budget,
                            context_options=context_options(settings, browser), keep_html=True)
            runner = Runner(rc, lambda _m: None)
            if light:
                from .reader import read_page
                res = await read_page(rc, url, depth=0, mode="light", relation="start")
            else:
                res = await runner.process(InputItem(url, ""))
                await runner.run_retries(1, final=True)
        finally:
            await browser.close()
    pages = [res] + [c for c in res.children if c.relation == "pagination"]
    jobs: list[dict[str, Any]] = []
    for page in pages:
        for src in ("links", "network", "jsonld", "embedded"):
            jobs += [dict(j, source=j.get("source", src)) for j in (page.jobs or {}).get(src, []) or []]
    html = "\n<!-- web_reader: next page -->\n".join(p._html for p in pages if p._html)
    status_code = res.http_status or (200 if res.status in ("ok", "partial") else 0)
    warnings = [w["code"] + (f" {w.get('detail')}" if w.get("detail") else "") for w in res.warnings]
    return FetchResult(url=url, final_url=res.final_url or url, status_code=status_code, html=html, status=res.status,
                       kind=res.kind, jobs=jobs, pages=len(pages), warnings=warnings, error=res.error, result=res)


def fetch(url: str, *, light: bool = False, config: str | Path | None = "config/settings.json",
          overrides: dict[str, Any] | None = None) -> FetchResult:
    """Читает страницу в браузере. light=True: быстрое чтение одной страницы без раскрытия (для страницы вакансии)."""
    base = {"run": {"max_depth": 0}, "output": {"screenshots": "never"}}
    if overrides:
        from .settings import _deep_update
        base = _deep_update(base, overrides)
    settings = load_settings(config, base).for_url(url)
    return asyncio.run(_fetch(url, settings, light))
