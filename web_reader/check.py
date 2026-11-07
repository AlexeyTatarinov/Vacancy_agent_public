"""Отдельная проверка соединений. Правило проекта: прежде чем программа обратится к
внешним сервисам, запускается эта проверка, и только после её успеха стартует основной запуск."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from .browser import context_options, launch_browser
from .dom import TEXT_JS
from .harvest import Harvester
from .i18n import t

_RENDER_PAGE = """<!doctype html><html><head><title>web_reader check</title></head><body>
<h1>Static heading</h1><div id="slot"></div>
<div id="host"></div>
<iframe srcdoc="<p>Text inside iframe</p>"></iframe>
<script>
  setTimeout(() => { document.getElementById('slot').textContent = 'Text added by JavaScript'; }, 150);
  const root = document.getElementById('host').attachShadow({mode: 'open'});
  root.innerHTML = '<p>Text inside shadow DOM</p>';
</script></body></html>"""


@dataclass
class CheckResult:
    key: str
    ok: bool
    detail: str = ""


async def run_checks(settings: Any, lang: str, out_dir: Path | None = None,
                     log: Any = print, check_internet: bool = True) -> tuple[bool, list[CheckResult]]:
    """Возвращает (все ли проверки пройдены, список результатов)."""
    results: list[CheckResult] = []

    def record(key: str, ok: bool, detail: str = "") -> None:
        results.append(CheckResult(key, ok, detail))
        status = t(lang, "check_ok") if ok else t(lang, "check_fail")
        log(f"  [{status}] {t(lang, key)}" + (f": {detail}" if detail else ""))

    log(t(lang, "preflight_title"))
    record("check_settings", True, "; ".join(settings.warnings) if settings.warnings else "")

    try:
        async with async_playwright() as playwright:
            try:
                browser, label = await launch_browser(playwright, settings)
            except Exception as exc:
                record("check_browser", False, str(exc)[:300])
                return _finish(results, out_dir, lang, log)
            record("check_browser", True, f"{label}, v{browser.version}")
            try:
                context = await browser.new_context(**context_options(settings, browser))
                page = await context.new_page()
                await page.set_content(_RENDER_PAGE)
                await page.wait_for_timeout(500)
                harvester = Harvester(100_000)
                await harvester.snapshot(page, "check", "page")
                for frame in page.frames[1:]:
                    await harvester.snapshot(frame, "check-frame", f"frame:{frame.url}")
                text = "\n".join(s["text"] for s in harvester.sections)
                missing = [w for w in ("Static heading", "Text added by JavaScript",
                                       "Text inside shadow DOM", "Text inside iframe") if w not in text]
                record("check_render", not missing, ("не прочитано: " + ", ".join(missing)) if missing else "")
                if check_internet:
                    probe = settings.preflight.probe_url
                    started = time.monotonic()
                    try:
                        response = await page.goto(probe, wait_until="domcontentloaded", timeout=15000)
                        status = response.status if response else None
                        record("check_network", bool(status and status < 400),
                               f"{probe} → HTTP {status}, {time.monotonic() - started:.1f} с")
                    except Exception as exc:
                        record("check_network", False, f"{probe}: {str(exc).splitlines()[0][:160]}")
                await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        record("check_browser", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    return _finish(results, out_dir, lang, log)


def _finish(results: list[CheckResult], out_dir: Path | None, lang: str, log: Any) -> tuple[bool, list[CheckResult]]:
    ok = all(r.ok for r in results)
    log(t(lang, "preflight_passed" if ok else "preflight_failed"))
    if out_dir is not None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "preflight.json").write_text(
                json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return ok, results
