"""Company list: parse the markdown, resolve a careers URL for each company."""
import datetime as dt
import json
import pathlib
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup

from .http import page_get

BAD_HOSTS = (
    "linkedin.", "indeed.", "glassdoor.", "facebook.", "wikipedia.", "youtube.",
    "twitter.", "x.com", "instagram.", "crunchbase.", "ziprecruiter.", "jooble.",
    "reddit.", "pitchbook.", "builtin.", "simplyhired.", "wellfound.", "startup.jobs", "angel.co", "womenontopp.", "zoominfo.", "owler.", "tiktok.", "gamesjobsdirect.",
)
URL_IN_CELL = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)]*)?)", re.I)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\(.*?\)", "", name.lower()))


def clean(name: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", name).strip()


def parse_md(path) -> list[dict]:
    """Read every table row; merge duplicates by normalized name."""
    found: dict[str, dict] = {}
    section = ""
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section.lower().startswith("не включено"):
            continue
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] == "Компания":
            continue
        key = norm(cells[0])
        m = URL_IN_CELL.search(cells[3])
        url = ("https://" + m.group(1)) if m else ""
        entry = found.setdefault(
            key,
            {"key": key, "name": clean(cells[0]), "country": cells[1], "area": section, "url": ""},
        )
        if url and not entry["url"]:
            entry["url"] = url
    return list(found.values())


URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def _bad(u: str) -> bool:
    return any(b in urlparse(u).netloc.lower() for b in BAD_HOSTS) or "vertexaisearch" in u


def _gemini(c: dict, settings: dict) -> str:
    from .judge import gemini_text

    text = gemini_text(
        f'Find the official careers / job openings page of the company "{c["name"]}" '
        f'({c["country"]}; {c["area"]}). Prefer the company\'s own careers page or its job board '
        "(Greenhouse, Lever, Teamtailor, Workable, etc.). Reply with exactly one URL and nothing else, or NONE.",
        settings, search=True)
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;")
        if not _bad(u):
            return u
    return ""


def _ddg(query: str) -> str:
    r = page_get("https://html.duckduckgo.com/html/?q=" + quote(query))
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
        if href.startswith("http") and not _bad(href):
            return href
    return ""


def load_companies(root: pathlib.Path, settings: dict, only: str = "") -> list[dict]:
    companies = parse_md(root / "config" / "companies.md")
    ov = json.loads((root / "config" / "overrides.json").read_text(encoding="utf-8"))
    skip = {norm(x) for x in ov.get("skip", [])}
    manual = {norm(k): v for k, v in ov.get("careers_url", {}).items()}
    feeds = {norm(k): v for k, v in ov.get("rss", {}).items()}
    no_open = {norm(x) for x in ov.get("no_openings", [])}
    needs_link = {norm(x) for x in ov.get("needs_link", [])}  # list/search gave a wrong site: wait for the owner

    cache_path = root / "state" / "careers.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    today = dt.date.today()
    out = []
    for c in companies:
        if c["key"] in skip:
            continue
        if only and only.lower() not in c["name"].lower():
            continue
        url = manual.get(c["key"]) or c["url"]
        c["rss"] = feeds.get(c["key"], "")
        c["no_openings"] = c["key"] in no_open
        c["open_app"] = c["key"] in {norm(clean(x)) for x in ov.get("open_applications", {})}
        c["needs_link"] = c["key"] in needs_link and not manual.get(c["key"]) and not c["rss"]
        if c["needs_link"]:
            c["url"] = ""
            out.append(c)
            continue
        if not url and not c["rss"]:
            hit = cache.get(c["key"])
            fresh = hit and (today - dt.date.fromisoformat(hit["checked"])).days < settings["rediscover_after_days"]
            if fresh:  # successes and failures are both remembered for a few days (saves scrape.do credits)
                url = hit["url"]
            else:
                finders = [lambda: _ddg(f'{c["name"]} careers jobs')]
                if settings.get("discovery_gemini_search"):
                    finders.insert(0, lambda: _gemini(c, settings))
                cache[c["key"]] = {"url": "", "checked": today.isoformat()}
                for finder in finders:
                    try:
                        url = finder()
                    except Exception as e:
                        print(f'Поиск карьерной страницы {c["name"]}: {type(e).__name__}')
                        url = ""
                    if url:
                        cache[c["key"]] = {"url": url, "checked": today.isoformat()}
                        break
        c["url"] = url
        out.append(c)
    cache_path.parent.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return out
