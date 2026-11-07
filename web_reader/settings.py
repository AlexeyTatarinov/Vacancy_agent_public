"""Загрузка и проверка настроек (защита от неверных и опасных значений).

Все числа лимитов лежат в config/settings.json. Если значение отсутствует,
берётся значение по умолчанию из DEFAULTS. Если значение выше жёсткого
потолка CEILINGS, оно урезается, а в список warnings добавляется пояснение.
Неизвестные ключи (опечатки) тоже попадают в warnings, а не игнорируются молча.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES = ("ru", "en", "uk")

DEFAULTS: dict[str, Any] = {
    "language": "en",
    # Метка авторства; выводится в подвале отчётов, если не пустая.
    "authorship_mark": "",
    "limits": {
        "page_timeout_s": 30,          # мягкий потолок времени на страницу
        "light_page_timeout_s": 15,    # то же для страниц, открытых по ссылке (без раскрытия)
        "hard_timeout_grace_s": 10,    # запас до принудительной остановки
        "max_scrolls": 15,
        "no_new_rounds_to_stop": 3,    # столько раундов подряд без нового — стоп
        "max_expansions": 25,          # клики «показать ещё», вкладки, аккордеоны
        "max_jobs_per_page": 100,
        "max_text_chars": 2_000_000,
        "max_requests_per_page": 400,
        "max_frames": 10,
        "max_json_responses": 30,
        "max_json_bytes": 2_000_000,
        "max_pdf_bytes": 10_000_000,
        "max_pdf_pages": 50,
    },
    "run": {
        "max_pages_per_run": 40,
        "max_pages_per_start": 15,
        "max_run_seconds": 900,
        "max_depth": 1,                # 0 = только указанные адреса
        "max_followed_per_page": 15,
        "max_pagination_pages": 5,
        "max_probes_per_page": 25,     # быстрые проверки остальных внутренних ссылок
        "max_probes_per_run": 200,
        "max_pdfs_per_page": 5,
        "follow_external": False,      # переходить по внешним сайтам (не рекомендуется)
        "follow_ats": True,            # переходить на системы найма (Greenhouse, Lever и т. д.)
        "delay_between_pages_s": 2,
        "retry_after_starts": 3,       # вернуться к отказанным страницам после стольких других стартовых адресов
        "max_retry_attempts": 2,       # сколько раз возвращаться к одной странице
        "retry_pause_s": 30,           # пауза перед повтором, если других адресов уже не осталось (умножается на номер попытки)
        "followed_text_chars": 6000,   # сколько текста сохранять с открытой по ссылке страницы
    },
    "browser": {
        "executable_path": None,       # None = встроенный Chromium Playwright
        "locale": "en-US",
        "viewport_width": 1366,
        "viewport_height": 900,
        "user_agent": None,
    },
    "safety": {
        "allow_private_hosts": False,  # localhost и локальные сети запрещены
        "blocked_domains": [
            "doubleclick.net", "googlesyndication.com", "googleadservices.com",
            "google-analytics.com", "googletagmanager.com", "facebook.net",
            "hotjar.com", "clarity.ms", "segment.io", "segment.com",
            "adsrvr.org", "adnxs.com", "criteo.com", "taboola.com", "outbrain.com",
            "scorecardresearch.com", "quantserve.com", "mixpanel.com",
        ],
        "block_resource_types": ["media", "font"],
        "ats_domains": [
            "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
            "smartrecruiters.com", "recruitee.com", "teamtailor.com",
            "personio.de", "personio.com", "breezy.hr", "bamboohr.com",
            "jobvite.com", "icims.com", "myworkdayjobs.com", "workdayjobs.com",
            "successfactors.com", "successfactors.eu", "taleo.net",
            "pinpointhq.com", "applytojob.com", "comeet.com", "join.com",
            "freshteam.com", "eightfold.ai", "avature.net", "phenompeople.com",
            "dayforcehcm.com", "homerun.co", "factorialhr.com", "hibob.com",
            "kenjo.io", "zohorecruit.com", "careers-page.com", "rippling.com",
        ],
    },
    "output": {
        "screenshots": "on_problem",   # never | on_problem | always
        "report_text_chars": 200_000,
        "keep_runs": 10,
    },
    "preflight": {
        "probe_url": "https://example.com",
    },
    "roles": {
        "enabled": True,
        "file": "config/roles.json",   # словарь ролей; если файла нет, отбор по ролям пропускается с предупреждением
    },
}

# Жёсткие потолки: больше этих значений программа не позволит, что бы ни стояло в файле.
CEILINGS: dict[str, float] = {
    "limits.page_timeout_s": 120,
    "limits.light_page_timeout_s": 60,
    "limits.hard_timeout_grace_s": 30,
    "limits.max_scrolls": 100,
    "limits.no_new_rounds_to_stop": 10,
    "limits.max_expansions": 100,
    "limits.max_jobs_per_page": 1000,
    "limits.max_text_chars": 10_000_000,
    "limits.max_requests_per_page": 2000,
    "limits.max_frames": 30,
    "limits.max_json_responses": 100,
    "limits.max_json_bytes": 10_000_000,
    "limits.max_pdf_bytes": 50_000_000,
    "limits.max_pdf_pages": 200,
    "run.max_pages_per_run": 300,
    "run.max_pages_per_start": 100,
    "run.retry_after_starts": 100,
    "run.max_retry_attempts": 5,
    "run.retry_pause_s": 600,
    "run.max_run_seconds": 7200,
    "run.max_depth": 3,
    "run.max_followed_per_page": 50,
    "run.max_pagination_pages": 20,
    "run.max_probes_per_page": 100,
    "run.max_probes_per_run": 1000,
    "run.max_pdfs_per_page": 20,
    "run.delay_between_pages_s": 60,
    "run.followed_text_chars": 100_000,
    "output.report_text_chars": 2_000_000,
    "output.keep_runs": 100,
    "browser.viewport_width": 3000,
    "browser.viewport_height": 3000,
}

# Минимумы для числовых значений (по умолчанию 1; здесь исключения).
MINIMUMS: dict[str, float] = {
    "run.max_depth": 0,
    "run.max_probes_per_page": 0,
    "run.max_probes_per_run": 0,
    "run.max_pdfs_per_page": 0,
    "run.max_pagination_pages": 0,
    "run.delay_between_pages_s": 0,
    "output.keep_runs": 1,
}

SCREENSHOT_MODES = ("never", "on_problem", "always")


class Section:
    """Словарь с доступом через точку: settings.limits.max_scrolls."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)


# Что можно менять для отдельного сайта (остальные пределы общие и поднимаются только в основных настройках).
SITE_OVERRIDE_KEYS = {
    "run": {"max_pagination_pages", "max_pages_per_start", "max_followed_per_page"},
    "limits": {"max_scrolls", "max_expansions", "max_jobs_per_page", "page_timeout_s"},
}


class Settings:
    def __init__(self, data: dict[str, Any], warnings: list[str]):
        self._data = data
        self.site_overrides: dict[str, dict[str, Any]] = data.get("site_overrides", {})
        self.warnings = warnings
        self.language: str = data["language"]
        self.authorship_mark: str = data["authorship_mark"]
        self.limits = Section(data["limits"])
        self.run = Section(data["run"])
        self.browser = Section(data["browser"])
        self.safety = Section(data["safety"])
        self.output = Section(data["output"])
        self.preflight = Section(data["preflight"])
        self.roles = Section(data["roles"])

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def for_url(self, url: str) -> "Settings":
        """Настройки для конкретного сайта: общие значения плюс поднятые пределы из site_overrides."""
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        best = None
        for domain in self.site_overrides:
            if host == domain or host.endswith("." + domain):
                if best is None or len(domain) > len(best):
                    best = domain
        if best is None:
            return self
        data = copy.deepcopy(self._data)
        for section, values in self.site_overrides[best].items():
            data[section].update(values)
        derived = Settings(data, self.warnings)
        derived.site_label = best
        return derived

    site_label: str | None = None


def _merge(defaults: dict[str, Any], user: dict[str, Any], path: str,
           warnings: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in user.items():
        full = f"{path}.{key}" if path else key
        if key not in defaults:
            warnings.append(f"неизвестный параметр «{full}» проигнорирован")
            continue
        default = defaults[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                result[key] = _merge(default, value, full, warnings)
            else:
                warnings.append(f"«{full}» должен быть объектом, оставлено значение по умолчанию")
            continue
        if isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
            else:
                warnings.append(f"«{full}» должен быть true/false, оставлено значение по умолчанию")
            continue
        if isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                warnings.append(f"«{full}» должен быть числом, оставлено значение по умолчанию")
                continue
            minimum = MINIMUMS.get(full, 1)
            if value < minimum:
                warnings.append(f"«{full}» меньше допустимого минимума {minimum}, взято {minimum}")
                value = minimum
            ceiling = CEILINGS.get(full)
            if ceiling is not None and value > ceiling:
                warnings.append(f"«{full}» = {value} выше жёсткого потолка, урезано до {ceiling}")
                value = ceiling
            result[key] = type(default)(value) if isinstance(default, int) else value
            continue
        if isinstance(default, list):
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                result[key] = value
            else:
                warnings.append(f"«{full}» должен быть списком строк, оставлено значение по умолчанию")
            continue
        # строка или None
        if value is None or isinstance(value, str):
            result[key] = value
        else:
            warnings.append(f"«{full}» должен быть строкой, оставлено значение по умолчанию")
    return result


def load_settings(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    """Читает файл настроек (если он есть) и накладывает его на значения по умолчанию."""
    warnings: list[str] = []
    user: dict[str, Any] = {}
    if path:
        file = Path(path)
        if file.exists():
            try:
                loaded = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Не удалось прочитать настройки {file}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"Файл настроек {file} должен содержать объект JSON")
            user = loaded
        else:
            warnings.append(f"файл настроек {file} не найден, использованы значения по умолчанию")
    if overrides:
        user = _deep_update(user, overrides)
    site_raw = user.pop("site_overrides", {}) if isinstance(user, dict) else {}
    data = _merge(DEFAULTS, user, "", warnings)
    data["site_overrides"] = _parse_site_overrides(site_raw, data, warnings)

    if data["language"] not in SUPPORTED_LANGUAGES:
        warnings.append(f"язык «{data['language']}» не поддерживается, взят ru")
        data["language"] = "en"
    if data["output"]["screenshots"] not in SCREENSHOT_MODES:
        warnings.append("output.screenshots должен быть never, on_problem или always; взят on_problem")
        data["output"]["screenshots"] = "on_problem"
    return Settings(data, warnings)


def _parse_site_overrides(raw: Any, data: dict[str, Any], warnings: list[str]) -> dict[str, dict[str, Any]]:
    """site_overrides: {"домен": {"run": {...}, "limits": {...}}}. Значения проверяются так же, как основные (потолки действуют)."""
    result: dict[str, dict[str, Any]] = {}
    if not raw:
        return result
    if not isinstance(raw, dict):
        warnings.append("«site_overrides» должен быть объектом, проигнорирован")
        return result
    for domain, entry in raw.items():
        label = f"site_overrides.{domain}"
        if not isinstance(entry, dict):
            warnings.append(f"«{label}» должен быть объектом, проигнорирован")
            continue
        clean: dict[str, Any] = {}
        for section, values in entry.items():
            allowed = SITE_OVERRIDE_KEYS.get(section)
            if allowed is None or not isinstance(values, dict):
                warnings.append(f"«{label}.{section}» не поддерживается, проигнорирован")
                continue
            ok_values = {}
            for key, value in values.items():
                if key not in allowed:
                    warnings.append(f"«{label}.{section}.{key}» нельзя менять для отдельного сайта, проигнорирован")
                    continue
                ok_values[key] = value
            local: list[str] = []
            merged = _merge({section: data[section]}, {section: ok_values}, "", local)[section]   # путь без префикса: так находятся потолки
            warnings.extend(f"{label}: {w}" for w in local)
            clean[section] = {k: merged[k] for k in ok_values}
        if clean:
            result[domain.lower().strip()] = clean
    return result


def _deep_update(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = value
    return result
