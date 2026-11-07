"""Free discovery of new companies to monitor.

Sources: Wikipedia categories (+ official site from Wikidata), Gemini suggestions (free key), own seed list.
Every candidate is VERIFIED: its public job board (ATS) must exist and, ideally, hold roles from the roles dictionary.
Nothing is added to config/companies.md automatically: results go to state/candidates.json and docs/candidates.md,
and `python -m agent.discover --promote "Name"` moves a reviewed candidate into the company list.

Run: python -m agent.discover [--max-new N] [--only TEXT] [--gemini] [--no-wikipedia]
"""
import argparse
import time
import datetime as dt
import json
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import ats
from .companies import BAD_HOSTS, clean, norm, parse_md
from .http import get
from .models import BAD_COUNTRY_RE, TITLE_RE

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIKI = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "VacancyAgentDiscovery/1.0 (https://github.com/AlexeyTatarinov/Vacancy_agent; personal job-search tool)"}
SUFFIX_RE = re.compile(r"\b(studios?|games?|gaming|interactive|entertainment|inc|ltd|llc|gmbh|sa|sl|s\.l\.|ab|oy|bv|group|software|digital|vr|xr|the)\b\.?", re.I)
CAREER_LINK_RE = re.compile(r"career|jobs?\b|join|work-with|hiring|vacanc|empleo|trabaj|karriere|stellen|offres", re.I)
PROBE_ORDER = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "teamtailor", "personio",
               "pinpoint", "breezy", "bamboohr")


# ---------- roles ----------
def load_role_matcher(root=ROOT):
    """Return f(title)->bool using the roles dictionary; fall back to the bot's TITLE_RE."""
    try:
        from web_reader.roles import load_rules
        rules = load_rules(str(root / "config" / "roles.json"))
        return lambda title: bool(rules.match(title, "strong"))
    except Exception:
        return lambda title: bool(TITLE_RE.search(title))


# ---------- sources ----------
def _wiki_json(url, params):
    """Wikimedia API call with a descriptive User-Agent, a pause between calls and one retry on 429/5xx."""
    for attempt in range(3):
        time.sleep(1.0)
        r = get(url, params=params, headers=WIKI_HEADERS)
        if r.status_code in (429, 500, 502, 503):
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.json()
    raise RuntimeError("HTTP 429/5xx")


def wikipedia_candidates(categories, limit):
    out = []
    for cat in categories:
        try:
            data = _wiki_json(WIKI, {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{cat}",
                                     "cmlimit": min(limit, 500), "cmtype": "page", "format": "json"})
            members = data.get("query", {}).get("categorymembers", [])
        except Exception as e:
            print(f"Википедия {cat}: {type(e).__name__} {e}")
            continue
        titles = [m["title"] for m in members if not m["title"].lower().startswith(("list of", "category:"))]
        print(f"Википедия, категория «{cat}»: {len(titles)}")
        sites = wikidata_sites(titles)
        for t in titles:
            out.append({"name": clean(t), "website": sites.get(t, ""), "source": f"wikipedia:{cat}"})
    return out


def wikidata_sites(titles):
    """title -> official website (Wikidata P856), in batches of 50."""
    sites = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        try:
            data = _wiki_json(WIKI, {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                     "titles": "|".join(batch), "redirects": 1, "format": "json"})
            pages = data.get("query", {}).get("pages", {})
            ids = {p["pageprops"]["wikibase_item"]: p["title"] for p in pages.values() if "pageprops" in p}
            if not ids:
                continue
            data = _wiki_json(WIKIDATA, {"action": "wbgetentities", "ids": "|".join(ids), "props": "claims", "format": "json"})
            for qid, ent in data.get("entities", {}).items():
                for cl in (ent.get("claims") or {}).get("P856") or []:
                    url = ((cl.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                    if isinstance(url, str):
                        sites[ids[qid]] = url
                        break
        except Exception as e:
            print(f"Wikidata: {type(e).__name__} {e}")
    return sites


def wikipedia_search_candidates(queries, limit=20):
    """Fallback for countries without good categories: Wikipedia search; only pages with an official site in Wikidata count."""
    out = []
    for q in queries:
        try:
            data = _wiki_json(WIKI, {"action": "query", "list": "search", "srsearch": q, "srlimit": limit, "format": "json"})
            titles = [h["title"] for h in data.get("query", {}).get("search", [])
                      if not h["title"].lower().startswith(("list of", "category:", "video game industry", "economy of"))]
        except Exception as e:
            print(f"Поиск Википедии «{q}»: {type(e).__name__} {e}")
            continue
        sites = wikidata_sites(titles)
        got = [t for t in titles if sites.get(t)]
        print(f"Поиск Википедии «{q}»: {len(titles)} страниц, с сайтом {len(got)}")
        out += [{"name": clean(t), "website": sites[t], "source": f"wikipedia-search:{q}"} for t in got]
    return out


def gemini_candidates(queries, settings):
    from .judge import gemini_call, _models
    import os
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY не задан: подсказки Gemini пропущены.")
        return []
    out = []
    for q in queries:
        prompt = (f"List up to 25 REAL companies: {q}. Exclude Russia and Belarus. Return ONLY a JSON array of objects "
                  '{"name": str, "country": str, "website": str}. Use an empty website if you are not sure. Do not invent companies.')
        res = gemini_call(prompt, _models(settings), key)
        if isinstance(res, dict):
            res = next((v for v in res.values() if isinstance(v, list)), [])
        for o in res or []:
            if isinstance(o, dict) and o.get("name"):
                out.append({"name": clean(str(o["name"])), "website": str(o.get("website") or ""),
                            "country": str(o.get("country") or ""), "source": "gemini"})
    return out


# ---------- verification ----------
def slug_variants(name):
    base = clean(name)
    short = SUFFIX_RE.sub(" ", base)
    forms = []
    for s in (base, short):
        s = re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()
        if not s:
            continue
        forms += [s.replace(" ", ""), s.replace(" ", "-")]
    seen, out = set(), []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out[:4]


def _fetch_html(url):
    try:
        r = get(url, timeout=15)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.url, r.text
    except Exception:
        pass
    return url, ""


def board_from_website(website, name):
    """Open the site, follow up to 3 careers-like links, detect an ATS. Return (jobs, ats, slug, careers_url) or None."""
    if not website or any(b in urlparse(website).netloc.lower() for b in BAD_HOSTS):
        return None
    if not website.startswith("http"):
        website = "https://" + website
    final, html = _fetch_html(website)
    if not html:
        return None
    pages = [(final, html)]
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if CAREER_LINK_RE.search(a["href"]) or CAREER_LINK_RE.search(txt):
            u = urljoin(final, a["href"]).split("#")[0]
            if u not in links and u != final:
                links.append(u)
    for u in links[:3]:
        pages.append(_fetch_html(u))
    for url, h in pages:
        for aname, slug, fn in ats.detect_ats(url, h):
            try:
                return fn(slug, name), aname, slug, url
            except Exception:
                continue
    return ("careers-only", None, None, links[0]) if links else None


def board_by_probe(name):
    """No website: try plausible slugs on public ATS APIs (low confidence: a slug may belong to another company)."""
    fetchers = {n: fn for n, _, fn in ats.ATS}
    for slug in slug_variants(name):
        for aname in PROBE_ORDER:
            try:
                jobs = fetchers[aname](slug, name)
            except Exception:
                continue
            if jobs:
                return jobs, aname, slug
    return None


def verify(cand, is_role):
    name = cand["name"]
    rec = {"name": name, "country": cand.get("country", ""), "source": cand.get("source", ""),
           "website": cand.get("website", ""), "checked": dt.date.today().isoformat(),
           "status": "none", "ats": "", "slug": "", "careers_url": "", "jobs": 0, "role_titles": []}
    found = board_from_website(cand.get("website", ""), name)
    confidence = "high"
    if found is None and not cand.get("website"):
        p = board_by_probe(name)
        if p:
            found, confidence = (p[0], p[1], p[2], ""), "low"
    if not found:
        return rec
    jobs, aname, slug, url = found
    if jobs == "careers-only":
        rec.update(status="careers_page", careers_url=url)
        return rec
    titles = [j.title for j in jobs]
    hits = sorted({t for t in titles if is_role(t)})
    rec.update(status="board" if confidence == "high" else "board_guess", ats=aname, slug=slug, careers_url=url,
               jobs=len(jobs), role_titles=hits[:15], role_hits=len(hits))
    return rec


# ---------- pipeline ----------
def collect(cfg, settings, use_wiki=True, use_gemini=True, gemini_set="gemini_queries"):
    cands = list(cfg.get("seed", []))
    for c in cands:
        c["source"] = "seed"
    if use_wiki:
        cands += wikipedia_candidates(cfg["wikipedia_categories"], cfg.get("wikipedia_limit_per_category", 120))
    if use_wiki and cfg.get("wikipedia_search"):
        cands += wikipedia_search_candidates(cfg["wikipedia_search"])
    if use_gemini:
        cands += gemini_candidates(cfg[gemini_set], settings)
    merged = {}
    for c in cands:
        if BAD_COUNTRY_RE.search(c["name"] + " " + c.get("country", "")):
            continue
        if any(x.lower() in c["name"].lower() for x in cfg.get("exclude_names", [])):
            continue
        k = norm(c["name"])
        if len(k) < 3:
            continue
        cur = merged.setdefault(k, c)
        if not cur.get("website") and c.get("website"):
            cur["website"] = c["website"]
    return merged


def known_keys(root):
    keys = {c["key"] for c in parse_md(root / "config" / "companies.md")}
    ov = json.loads((root / "config" / "overrides.json").read_text(encoding="utf-8"))
    keys |= {norm(x) for x in ov.get("skip", [])}
    return keys


def _interleave(items):
    """Round-robin by source (seed / wikipedia / gemini) so a cap on the run does not starve any source."""
    groups = {}
    for k, c in items:
        groups.setdefault(c.get("source", "").split(":")[0], []).append((k, c))
    out = []
    while any(groups.values()):
        for g in groups.values():
            if g:
                out.append(g.pop(0))
    return out


def run(root=ROOT, max_new=None, only="", use_wiki=True, use_gemini=True, verify_fn=verify, gemini_set="gemini_queries"):
    cfg = json.loads((root / "config" / "discovery.json").read_text(encoding="utf-8"))
    settings = json.loads((root / "config" / "settings.json").read_text())
    path = root / "state" / "candidates.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    today = dt.date.today()
    known = known_keys(root)
    merged = collect(cfg, settings, use_wiki, use_gemini, gemini_set)
    todo = []
    for k, c in merged.items():
        if k in known or (only and only.lower() not in c["name"].lower()):
            continue
        old = state.get(k)
        if old and (today - dt.date.fromisoformat(old["checked"])).days < cfg.get("recheck_after_days", 30):
            continue
        todo.append((k, c))
    todo = _interleave(todo)[: max_new or cfg.get("max_new_per_run", 60)]
    is_role = load_role_matcher(root)
    print(f"Кандидатов: {len(merged)}, к проверке: {len(todo)}")
    with ThreadPoolExecutor(cfg.get("workers", 6)) as ex:
        for (k, c), rec in zip(todo, ex.map(lambda kc: verify_fn(kc[1], is_role), todo)):
            state[k] = rec
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    write_report(root, state)
    return state


ORDER = {"board": 0, "board_guess": 1, "careers_page": 2, "none": 3}


def write_report(root, state):
    rows = sorted(state.values(), key=lambda r: (ORDER.get(r["status"], 9), -r.get("role_hits", 0), r["name"].lower()))
    good = [r for r in rows if r["status"] in ("board", "board_guess")]
    lines = ["# Кандидаты на добавление (автопоиск)", "",
             f"Обновлено: {dt.date.today().isoformat()}. Всего проверено: {len(rows)}; с доской вакансий: {len(good)}.", "",
             "Статусы: **board** — доска вакансий найдена на сайте компании; **board_guess** — доска подобрана по названию "
             "(возможна ошибка: проверьте, что это та же компания); **careers_page** — есть страница вакансий без распознанной системы; "
             "**none** — ничего не найдено.", "",
             "Добавить проверенную компанию: `python -m agent.discover --promote \"Название\"`.", "",
             "| Компания | Статус | Система | Вакансий | Подходящих ролей | Примеры ролей | Источник |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] == "none":
            continue
        ex = "; ".join(r.get("role_titles", [])[:3]).replace("|", "/")
        lines.append(f"| {r['name']} | {r['status']} | {r['ats'] or '-'} | {r['jobs']} | {r.get('role_hits', 0)} | {ex} | {r['source'].split(':')[0]} |")
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "candidates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def promote(root, name):
    path = root / "state" / "candidates.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = norm(name)
    rec = state.get(key) or next((r for k, r in state.items() if key in k), None)
    if not rec or rec["status"] == "none":
        raise SystemExit(f"Кандидат «{name}» не найден или без доски вакансий.")
    md = root / "config" / "companies.md"
    text = md.read_text(encoding="utf-8")
    head = "## 9. Автопоиск (добавлено после проверки)"
    row = f"| {rec['name']} | {rec['country'] or '-'} | автопоиск | {rec['careers_url'] or rec['website'] or '-'} | из автопоиска, доска: {rec['ats'] or '-'} |"
    if head not in text:
        text = text.rstrip("\n") + f"\n\n{head}\n\n| Компания | Страна/город | Сфера | Где смотреть вакансии | Статус |\n|---|---|---|---|---|\n"
    md.write_text(text.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    print(f"Добавлено: {rec['name']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--gemini", action="store_true", help="also ask Gemini for candidate names (off by default: saves free quota)")
    ap.add_argument("--no-wikipedia", action="store_true")
    ap.add_argument("--gemini-set", default="gemini_queries", help="key of the query list in config/discovery.json")
    ap.add_argument("--promote", default="")
    a = ap.parse_args()
    if a.promote:
        return promote(ROOT, a.promote)
    run(ROOT, a.max_new or None, a.only, not a.no_wikipedia, a.gemini, gemini_set=a.gemini_set)


if __name__ == "__main__":
    main()
