"""Запуск браузера и параметры окружения страницы."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_FALLBACK_BROWSERS = ("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser")


async def launch_browser(playwright: Any, settings: Any) -> tuple[Any, str]:
    """Запускает Chromium без окна. Порядок: путь из настроек, переменная WEB_READER_BROWSER,
    встроенный Chromium Playwright, затем системный Chrome/Chromium. Возвращает (браузер, откуда)."""
    args = ["--disable-dev-shm-usage"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # под root (контейнеры) песочница Chromium не запускается
    candidates: list[str | None] = []
    explicit = settings.browser.executable_path or os.environ.get("WEB_READER_BROWSER")
    if explicit:
        candidates.append(explicit)
    candidates.append(None)
    candidates.extend(p for p in _FALLBACK_BROWSERS if Path(p).exists())
    errors: list[str] = []
    for candidate in candidates:
        kwargs: dict[str, Any] = {"headless": True, "args": args}
        if candidate:
            kwargs["executable_path"] = candidate
        try:
            browser = await playwright.chromium.launch(**kwargs)
            return browser, candidate or "playwright-chromium"
        except Exception as exc:
            errors.append(f"{candidate or 'playwright-chromium'}: {str(exc).splitlines()[0][:160]}")
    raise RuntimeError("Не удалось запустить браузер. Установите его командой "
                       "«python -m playwright install chromium». Попытки: " + " | ".join(errors))


def context_options(settings: Any, browser: Any) -> dict[str, Any]:
    """Параметры нового окружения: размер окна, язык, запрет загрузки файлов и служебных скриптов."""
    options: dict[str, Any] = {
        "viewport": {"width": settings.browser.viewport_width, "height": settings.browser.viewport_height},
        "locale": settings.browser.locale,
        "accept_downloads": False,
        "service_workers": "block",
        "ignore_https_errors": False,
        "java_script_enabled": True,
    }
    user_agent = settings.browser.user_agent
    if not user_agent:
        # Обычная строка браузера без слова HeadlessChrome. Это не маскировка под человека,
        # а общепринятая нейтральная строка; защиту от ботов программа не обходит.
        match = re.match(r"(\d+)", getattr(browser, "version", "") or "")
        major = match.group(1) if match else "120"
        user_agent = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
    options["user_agent"] = user_agent
    return options
