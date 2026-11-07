"""Бюджеты времени и страниц: защита от бесконечного чтения."""
from __future__ import annotations

import time


class Deadline:
    """Срок в секундах: remaining() показывает остаток, expired() — вышел ли срок."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self._end

    def elapsed(self) -> float:
        return self.seconds - self.remaining()


class RunBudget:
    """Общий бюджет запуска: число страниц, время, число быстрых проверок."""

    def __init__(self, max_pages: int, max_seconds: float, max_probes: int):
        self.max_pages = max_pages
        self.pages_used = 0
        self.deadline = Deadline(max_seconds)
        self.max_probes = max_probes
        self.probes_used = 0
        self.item_limit: int | None = None     # потолок pages_used для текущего стартового адреса

    def start_item(self, per_item: int) -> None:
        """Один стартовый адрес не может съесть весь бюджет запуска: ему выделяется своя доля."""
        self.item_limit = self.pages_used + max(1, per_item)

    def pages_left(self) -> int:
        return max(0, self.max_pages - self.pages_used)

    def can_read_page(self) -> tuple[bool, str]:
        if self.deadline.expired():
            return False, "run_time_budget"
        if self.pages_used >= self.max_pages:
            return False, "run_page_budget"
        if self.item_limit is not None and self.pages_used >= self.item_limit:
            return False, "item_page_budget"
        return True, "ok"

    def use_page(self) -> None:
        self.pages_used += 1

    def can_probe(self) -> bool:
        return self.probes_used < self.max_probes and not self.deadline.expired()

    def use_probe(self) -> None:
        self.probes_used += 1
