"""Закрытие всплывающих окон и баннеров cookie.

Принцип приватности: на баннерах выбирается отказ от необязательных cookie.
Кнопки «Принять» программа не нажимает. Если баннер нельзя закрыть без согласия,
он записывается в отчёт как помеха, а страница читается как есть.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .dom import (CLOSE_RE, FIND_JS, HIDE_COOKIE_BANNER_JS, KNOWN_REJECT_SELECTORS, OBSTRUCTION_JS, REJECT_RE,
                  SAVE_RE, SETTINGS_RE, UNCHECK_OPTIONAL_JS)


async def click_marked(root: Any, wr_id: str, timeout_ms: int = 1500) -> bool:
    """Нажимает элемент, помеченный data-wr-id. Если обычный клик не проходит, кликает из скрипта."""
    locator = root.locator(f'[data-wr-id="{wr_id}"]').first
    try:
        await locator.click(timeout=timeout_ms)
        return True
    except Exception:
        pass
    try:
        await locator.evaluate("e => e.click()", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def dismiss_popups(page: Any, remaining: float, max_rounds: int = 3) -> dict[str, Any]:
    """Закрывает баннеры cookie и всплывающие окна. Возвращает {dismissed: [...], obstructions: [...]}."""
    dismissed: list[str] = []
    for _ in range(max_rounds):
        if remaining <= 0.5:
            break
        progress = False
        # 1. известные системы согласия
        for selector in KNOWN_REJECT_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible():
                    await loc.click(timeout=1500)
                    dismissed.append(f"cookie: {selector}")
                    progress = True
                    break
            except Exception:
                continue
        # 2. кнопки отказа по тексту
        if not progress:
            progress = await _click_first(page, "reject", REJECT_RE, dismissed, "cookie")
        # 3. закрытие рекламных и информационных окон
        if not progress:
            progress = await _click_first(page, "close", CLOSE_RE, dismissed, "popup")
        if not progress:
            break
        await asyncio.sleep(0.35)
        remaining -= 0.5
    obstructions = await _obstructions(page)
    if remaining > 1.0 and await _cookie_banner_texts(page, hide=False):
        # Баннер остался (в том числе небольшой): путь через настройки (необязательное выключается), затем скрытие.
        await _settings_path(page, dismissed)
        if await _cookie_banner_texts(page, hide=False):
            for text in await _cookie_banner_texts(page, hide=True):
                dismissed.append(f"cookie-hidden (consent not given): {text}")
        obstructions = await _obstructions(page)
    return {"dismissed": dismissed, "obstructions": obstructions}


async def _obstructions(page: Any) -> list[str]:
    try:
        return await page.evaluate(OBSTRUCTION_JS)
    except Exception:
        return []


async def _cookie_banner_texts(page: Any, hide: bool) -> list[str]:
    try:
        return await page.evaluate(HIDE_COOKIE_BANNER_JS, hide)
    except Exception:
        return []


async def _settings_path(page: Any, dismissed: list[str]) -> None:
    """«Настройки cookie» → выключить необязательные переключатели → «Сохранить». «Принять всё» не нажимается."""
    if not await _click_first(page, "settings", SETTINGS_RE, dismissed, "cookie-settings"):
        return
    await asyncio.sleep(0.5)
    # на панели настроек часто есть собственная кнопка отказа
    if await _click_first(page, "reject", REJECT_RE, dismissed, "cookie"):
        await asyncio.sleep(0.4)
        return
    try:
        switched, total = await page.evaluate(UNCHECK_OPTIONAL_JS)
    except Exception:
        switched, total = 0, 0
    if total == 0:
        return          # не видим переключателей: «Сохранить» могло бы принять значения по умолчанию, не нажимаем
    if switched:
        dismissed.append(f"cookie: optional switches off ({switched})")
    if await _click_first(page, "save", SAVE_RE, dismissed, "cookie-save"):
        await asyncio.sleep(0.4)


async def _click_first(page: Any, kind: str, pattern: str, dismissed: list[str], prefix: str) -> bool:
    try:
        candidates = await page.evaluate(FIND_JS, {"kind": kind, "re": pattern, "skip": "^$", "limit": 5})
    except Exception:
        return False
    for cand in candidates:
        if await click_marked(page, cand["id"]):
            dismissed.append(f"{prefix}: {cand['text']}")
            return True
    return False
