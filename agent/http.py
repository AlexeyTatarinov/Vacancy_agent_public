import os
import threading
import time
from types import SimpleNamespace

import pathlib

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent

S = requests.Session()
S.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; VacancyAgent/1.0)",
        "Accept-Language": "en,es;q=0.8",
    }
)

CREDITS = {"spent": 0, "budget": None}
_lock = threading.Lock()


class BudgetExceeded(Exception):
    pass


def get(url, **kw):
    """Direct request (public JSON/XML APIs and feeds)."""
    kw.setdefault("timeout", 20)
    last = None
    for _ in range(2):
        try:
            return S.get(url, **kw)
        except requests.RequestException as e:
            last = e
    raise last


_browser_slots = threading.Semaphore(2)   # no more than two browsers at once (workers run in threads)


def reader_mode():
    """'browser' (default): own browser via web_reader, no paid service. 'scrapedo': legacy paid service (READER=scrapedo)."""
    return "scrapedo" if os.environ.get("READER", "").lower() == "scrapedo" else "browser"


def browser_enabled():
    if reader_mode() != "browser" or os.environ.get("READER_BROWSER") == "0":
        return False
    try:
        import web_reader.api  # noqa: F401
    except Exception:
        return False
    return True


def render_steps():
    """Escalation order for HTML pages: plain request first, then JavaScript rendering."""
    if reader_mode() == "scrapedo":
        return (False, True) if os.environ.get("SCRAPEDO_TOKEN") else (False,)
    return (False, True) if browser_enabled() else (False,)


def browser_get(url, light=False):
    """Read a page in a real browser (JavaScript, popups, widgets, iframes, list pagination) through web_reader.
    The result looks like a requests response: .text is the rendered HTML (all list pages joined); .jobs are
    vacancies found in links, in the page's own JSON requests and in JSON-LD."""
    from web_reader.api import fetch
    with _browser_slots:
        res = fetch(url, light=light, config=str(ROOT / "config" / "reader.json"))
    code = res.status_code or 0
    if res.status in ("blocked", "error", "skipped") or not res.html:
        code = code if code >= 400 else 599

    def raise_for_status():
        if code >= 400:
            why = res.error or ", ".join(res.warnings[:2]) or res.status
            raise RuntimeError(f"browser: {res.status} (HTTP {res.status_code or '-'}) для {url}: {why[:160]}")

    return SimpleNamespace(status_code=code, text=res.html, content=res.html.encode("utf-8", "ignore"),
                           url=res.final_url or url, raise_for_status=raise_for_status, jobs=res.jobs, reader=res)


def page_get(url, render=False):
    """HTML page. render=False: plain request (free). render=True: real browser (web_reader); with READER=scrapedo the
    legacy scrape.do path is used. render='light': quick single-page browser read (vacancy page)."""
    if reader_mode() == "browser":
        if render:
            return browser_get(url, light=(render == "light"))
        return get(url)
    token = os.environ.get("SCRAPEDO_TOKEN")
    if not token:
        return get(url)
    cost = 5 if render else 1
    with _lock:
        if CREDITS["budget"] is not None and CREDITS["spent"] + cost > CREDITS["budget"]:
            raise BudgetExceeded("дневной лимит кредитов scrape.do исчерпан")
        CREDITS["spent"] += cost  # reserve; refunded below on failure
    params = {"token": token, "url": url}
    if render:
        params["render"] = "true"
    try:
        for attempt in range(3):  # free plan allows 5 concurrent requests: 429 means "try again"
            r = S.get("https://api.scrape.do/", params=params, timeout=90)
            if r.status_code not in (429, 503):
                break
            time.sleep(4 * (attempt + 1))
    except requests.RequestException:
        with _lock:
            CREDITS["spent"] -= cost
        raise RuntimeError("scrape.do: ошибка соединения")  # no URL: it contains the token
    if not (200 <= r.status_code < 300 or r.status_code in (400, 404, 410)):
        with _lock:
            CREDITS["spent"] -= cost

    def raise_for_status():
        if r.status_code >= 400:
            raise RuntimeError(f"scrape.do: HTTP {r.status_code} для {url}")

    return SimpleNamespace(status_code=r.status_code, text=r.text, content=r.content, url=url,
                           raise_for_status=raise_for_status)
