"""Сетевой слой страницы: фильтр запросов, счётчик (защита от бесконечных запросов),
ожидание «тишины» и перехват JSON-ответов, из которых страница строит свой вид."""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from .jobs import extract_job_items
from .safety import host_matches, private_reason


class Watcher:
    """Считает запросы страницы и режет лишние: рекламу, трекеры, медиа, шрифты,
    локальные адреса и всё сверх лимита числа запросов."""

    def __init__(self, settings: Any):
        self.max_requests = settings.limits.max_requests_per_page
        self.block_types = set(settings.safety.block_resource_types)
        self.blocked_domains = settings.safety.blocked_domains
        self.allow_private = settings.safety.allow_private_hosts
        self.requests = 0
        self.pending = 0
        self.cap_hit = False
        self.blocked: Counter = Counter()

    def attach(self, page: Any) -> None:
        page.on("request", lambda r: self._inc())
        page.on("requestfinished", lambda r: self._dec())
        page.on("requestfailed", lambda r: self._dec())

    def _inc(self) -> None:
        self.pending += 1

    def _dec(self) -> None:
        self.pending = max(0, self.pending - 1)

    async def route(self, route: Any) -> None:
        request = route.request
        try:
            parts = urlsplit(request.url)
            if parts.scheme in ("data", "blob", "about"):
                await route.continue_()
                return
            if self.requests >= self.max_requests:
                self.cap_hit = True
                await route.abort()
                return
            host = parts.hostname or ""
            if request.resource_type in self.block_types:
                self.blocked["type:" + request.resource_type] += 1
                await route.abort()
                return
            if host_matches(host, self.blocked_domains):
                self.blocked["ads_trackers"] += 1
                await route.abort()
                return
            if not self.allow_private and private_reason(host):
                self.blocked["private_address"] += 1
                await route.abort()
                return
            self.requests += 1
            await route.continue_()
        except Exception:
            pass  # страница могла закрыться в момент обработки запроса

    async def settle(self, remaining: float, pause: float = 0.35, idle: float = 1.5) -> None:
        """Ждёт, пока новые запросы затихнут, но не дольше idle секунд и не дольше remaining."""
        budget = max(0.0, min(idle, remaining))
        await asyncio.sleep(min(pause, budget))
        end = time.monotonic() + max(0.0, budget - pause)
        while self.pending > 0 and time.monotonic() < end:
            await asyncio.sleep(0.1)


class JsonCapture:
    """Перехватывает JSON-ответы (xhr/fetch) и сразу выделяет из них списки вакансий."""

    def __init__(self, settings: Any, base_url: str):
        self.max_responses = settings.limits.max_json_responses
        self.max_bytes = settings.limits.max_json_bytes
        self.max_jobs = settings.limits.max_jobs_per_page
        self.base_url = base_url
        self.entries: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.dropped = 0
        self.unreadable = 0
        self._tasks: set[asyncio.Task] = set()

    def attach(self, page: Any) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        task = asyncio.ensure_future(self._handle(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, response: Any) -> None:
        try:
            request = response.request
            if request.resource_type not in ("xhr", "fetch"):
                return
            content_type = (response.headers.get("content-type") or "").lower()
            if "json" not in content_type or response.status >= 400:
                return
            if len(self.entries) >= self.max_responses:
                self.dropped += 1
                return
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                self.entries[(request.method, response.url, int(declared))] = {
                    "url": response.url, "method": request.method, "status": response.status,
                    "bytes": int(declared), "skipped": "too_big", "jobs": [], "job_total": 0, "repeat": 1,
                }
                return
            body = await asyncio.wait_for(response.body(), timeout=6)
            key = (request.method, response.url, len(body))
            if key in self.entries:
                self.entries[key]["repeat"] += 1
                return
            entry: dict[str, Any] = {
                "url": response.url, "method": request.method, "status": response.status,
                "bytes": len(body), "jobs": [], "job_total": 0, "excerpt": "", "repeat": 1,
            }
            if len(body) > self.max_bytes:
                entry["skipped"] = "too_big"
            else:
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    self.unreadable += 1
                    entry["skipped"] = "not_parsed"
                else:
                    jobs, total = extract_job_items(data, self.base_url, self.max_jobs)
                    entry["jobs"], entry["job_total"] = jobs, total
                    if not jobs:
                        entry["excerpt"] = json.dumps(data, ensure_ascii=False)[:500]
            self.entries[key] = entry
        except Exception:
            self.unreadable += 1

    async def drain(self, timeout: float = 3.0) -> None:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=max(0.1, timeout))
