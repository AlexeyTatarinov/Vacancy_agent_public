"""Накопление прочитанного: текст и ссылки собираются снимками и объединяются.

Зачем снимки, а не один взгляд в конце: вкладки и карусели прячут прежнее
содержимое, а длинные списки удаляют верхние строки из DOM при прокрутке.
Поэтому после каждого действия делается снимок, и в отчёт попадает объединение.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .classify import looks_like_job_url
from .dom import LINKS_JS, TEXT_JS
from .safety import normalize_url

MAX_LINKS = 1500


class Harvester:
    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.sections: list[dict[str, Any]] = []
        self.total_chars = 0
        self.truncated = False
        self.links: dict[tuple[str, str], dict[str, Any]] = {}
        self.h1 = ""
        self.last_error: str | None = None   # ошибка последнего снимка текста (например, фрейм недоступен)
        self._seen: dict[str, Counter] = {}

    async def snapshot_text(self, root: Any, label: str, source: str) -> int:
        """Снимок текста. В отчёт попадают только строки, которых раньше не было
        (с учётом числа повторов: «Apply now» на каждой карточке не теряется)."""
        try:
            data = await root.evaluate(TEXT_JS, {"maxChars": self.max_chars, "includeHidden": False})
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            return 0
        if not self.h1 and data.get("h1"):
            self.h1 = data["h1"]
        lines = data["text"].split("\n") if data.get("text") else []
        seen = self._seen.setdefault(source, Counter())
        current: Counter = Counter()
        fresh: list[str] = []
        for line in lines:
            current[line] += 1
            if current[line] > seen[line]:
                fresh.append(line)
        for line, count in current.items():
            if count > seen[line]:
                seen[line] = count
        if data.get("truncated"):
            self.truncated = True
        kept: list[str] = []
        for line in fresh:
            if self.total_chars + len(line) + 1 > self.max_chars:
                self.truncated = True
                break
            kept.append(line)
            self.total_chars += len(line) + 1
        if kept:
            self.sections.append({"label": label, "source": source, "text": "\n".join(kept)})
        return len(kept)

    async def snapshot_links(self, root: Any, source: str) -> int:
        try:
            found = await root.evaluate(LINKS_JS, {"max": MAX_LINKS})
        except Exception:
            return 0
        added = 0
        for link in found:
            key = (link["href"], link["text"])
            if key in self.links or len(self.links) >= MAX_LINKS:
                continue
            link["source"] = source
            self.links[key] = link
            added += 1
        return added

    async def snapshot(self, root: Any, label: str, source: str) -> tuple[int, int]:
        """Снимок текста и ссылок. Возвращает (новых строк, новых ссылок)."""
        lines = await self.snapshot_text(root, label, source)
        links = await self.snapshot_links(root, source)
        return lines, links

    def job_like_count(self, page_url: str, ats_domains: list[str]) -> int:
        """Сколько разных адресов среди собранных ссылок похожи на вакансии."""
        urls: set[str] = set()
        for link in self.links.values():
            norm = normalize_url(link["href"], page_url)
            if norm and looks_like_job_url(norm, ats_domains):
                urls.add(norm)
        return len(urls)
