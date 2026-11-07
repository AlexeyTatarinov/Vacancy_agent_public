"""Fetch vacancies: ATS JSON/XML APIs first, generic HTML link scan as fallback."""
import html as htmllib
import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup

from .http import BudgetExceeded, browser_enabled, get, page_get, render_steps
from .models import CLOSED_RE, TITLE_RE, Job

SKIP_SLUGS = {"www", "app", "api", "career", "careers", "jobs", "embed"}


def text_of(markup: str) -> str:
    return " ".join(BeautifulSoup(htmllib.unescape(markup or ""), "html.parser").get_text(" ").split())


def _json(url):
    r = get(url)
    r.raise_for_status()
    return r.json()


def greenhouse(slug, co):
    data = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    return [
        Job(co, j["title"], j["absolute_url"], (j.get("location") or {}).get("name", ""),
            text_of(j.get("content", ""))[:3000], "greenhouse")
        for j in data.get("jobs", [])
    ]


def lever(slug, co):
    for host in ("api.lever.co", "api.eu.lever.co"):
        r = get(f"https://{host}/v0/postings/{slug}?mode=json")
        if r.status_code == 200:
            return [
                Job(co, j["text"], j["hostedUrl"],
                    " / ".join(filter(None, [(j.get("categories") or {}).get("location", ""), j.get("workplaceType", "")])),
                    (j.get("descriptionPlain") or "")[:3000], "lever")
                for j in r.json()
            ]
    raise RuntimeError("lever: not found")


def ashby(slug, co):
    data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(unquote(slug))}")
    return [
        Job(co, j["title"], j.get("jobUrl", ""),
            " / ".join(filter(None, [j.get("location", ""), "Remote" if j.get("isRemote") else ""])),
            (j.get("descriptionPlain") or "")[:3000], "ashby")
        for j in data.get("jobs", [])
    ]


def workable(slug, co):
    data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    return [
        Job(co, j["title"], j.get("url") or j.get("shortlink", ""),
            ", ".join(filter(None, [j.get("city", ""), j.get("country", ""), "Remote" if j.get("telecommuting") else ""])),
            text_of(j.get("description", ""))[:3000], "workable")
        for j in data.get("jobs", [])
    ]


def smartrecruiters(slug, co):
    data = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(filter(None, [loc.get("city", ""), loc.get("country", "").upper(), "Remote" if loc.get("remote") else ""]))
        out.append(Job(co, j["name"], f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", where, "", "smartrecruiters"))
    return out


def recruitee(slug, co):
    data = _json(f"https://{slug}.recruitee.com/api/offers/")
    return [
        Job(co, j["title"], j["careers_url"], ", ".join(filter(None, [j.get("location", ""), j.get("country", "")])),
            text_of(j.get("description", ""))[:3000], "recruitee")
        for j in data.get("offers", [])
    ]


def teamtailor(slug, co):
    r = get(f"https://{slug}.teamtailor.com/jobs.rss")
    r.raise_for_status()
    root = ET.fromstring(r.content)
    return [
        Job(co, (i.findtext("title") or "").strip(), (i.findtext("link") or "").strip(), "",
            text_of(i.findtext("description") or "")[:3000], "teamtailor")
        for i in root.iter("item")
    ]


def personio(slug, co):
    for tld in ("de", "com"):
        r = get(f"https://{slug}.jobs.personio.{tld}/xml")
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            out = []
            for p in root.iter("position"):
                desc = " ".join(text_of(v.text or "") for v in p.iter("value"))[:3000]
                out.append(Job(co, (p.findtext("name") or "").strip(),
                               f"https://{slug}.jobs.personio.{tld}/job/{p.findtext('id')}",
                               p.findtext("office") or "", desc, "personio"))
            return out
    raise RuntimeError("personio: not found")


def pinpoint(slug, co):
    data = _json(f"https://{slug}.pinpointhq.com/postings.json")
    out = []
    for j in data.get("data", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(Job(co, j["title"], j.get("url") or f"https://{slug}.pinpointhq.com/", loc, text_of(j.get("description", ""))[:3000], "pinpoint"))
    return out


def rippling(slug, co):
    data = _json(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs")
    return [
        Job(co, j["name"], j["url"], (j.get("workLocation") or {}).get("label", ""), "", "rippling")
        for j in (data if isinstance(data, list) else data.get("jobs", []))
    ]


def bamboohr(slug, co):
    r = get(f"https://{slug}.bamboohr.com/careers/list", headers={"Accept": "application/json"})
    r.raise_for_status()
    out = []
    for j in r.json().get("result", []):
        loc = j.get("location") or {}
        where = ", ".join(filter(None, [loc.get("city", ""), loc.get("state", "")]))
        out.append(Job(co, j["jobOpeningName"], f"https://{slug}.bamboohr.com/careers/{j['id']}", where, "", "bamboohr"))
    return out


def breezy(slug, co):
    data = _json(f"https://{slug}.breezy.hr/json")
    return [
        Job(co, j["name"], j["url"], (j.get("location") or {}).get("name", ""), "", "breezy")
        for j in data
    ]


# (name, regex with slug group, fetcher); order matters: first match that works wins.
ATS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I), greenhouse),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)", re.I), greenhouse),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w-]+)", re.I), lever),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I), ashby),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I), workable),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)", re.I), smartrecruiters),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.I), recruitee),
    ("teamtailor", re.compile(r"([\w-]+)\.teamtailor\.com", re.I), teamtailor),
    ("personio", re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)", re.I), personio),
    ("pinpoint", re.compile(r"([\w-]+)\.pinpointhq\.com", re.I), pinpoint),
    ("rippling", re.compile(r"ats\.rippling\.com/([\w-]+)", re.I), rippling),
    ("bamboohr", re.compile(r"([\w-]+)\.bamboohr\.com", re.I), bamboohr),
    ("breezy", re.compile(r"([\w-]+)\.breezy\.hr", re.I), breezy),
]


def detect_ats(url: str, html: str):
    """Yield (ats_name, slug, fetcher) for every ATS reference found, in priority order."""
    blob = url + "\n" + html
    seen = set()
    for name, rx, fn in ATS:
        for m in rx.finditer(blob):
            slug = m.group(1)
            if slug.lower() in SKIP_SLUGS or (name, slug) in seen:
                continue
            seen.add((name, slug))
            yield name, slug, fn


def from_ats(url, html, co):
    """Return (jobs, recipe) if an ATS was found and worked, else None."""
    for name, slug, fn in detect_ats(url, html):
        try:
            return fn(slug, co), {"t": "ats", "a": name, "slug": slug}
        except Exception:
            continue
    return None


JOB_HREF = re.compile(
    r"job|career|position|vacanc|opening|apply|hiring|recruit|talent|posting|opportunit|empleo|trabaj|karriere|stellen", re.I)
JOB_DETAIL = re.compile(r"/(job\w*|career\w*|position\w*|opening\w*|vacanc\w*|posting\w*|opportunit\w*|role\w*)/[^/?#]{3,}", re.I)
EMPTY_RE = re.compile(
    r"no (matching |open |current |available )?(jobs|positions|vacancies|openings|roles)|no openings|no specific positions|"
    r"not (currently )?hiring|check back (later|in the future|soon)|nothing (available )?at the moment|"
    r"keine offenen stellen|sin vacantes|aucune offre|no jobs found", re.I)


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return " ".join(soup.get_text(" ").split())
ATS_HINT = re.compile(
    r"greenhouse|lever\.co|ashby|workable|smartrecruiters|teamtailor|recruitee|personio|bamboohr|myworkdayjobs|workday|"
    r"jobvite|icims|breezy|join\.com|comeet|rippling|dayforce|successfactors|taleo|oraclecloud|hirehive|pinpoint|"
    r"jazzhr|bullhorn|careerplug|paylocity|factorialhr|hibob|recruiterbox|homerun|pinpointhq|paradox|applytojob", re.I)


CONTACTS = {}   # company name -> {"emails": [...], "url": page}; filled while pages are read, read by main
JOBY_LOCAL = re.compile(r"^(jobs?|careers?|recruit\w*|hr|hiring|talent|apply|applications?|resumes?|cv|vacanc\w*|work|workwithus|join\w*|people|staffing|opportunit\w*|empleo|trabaj\w*)([._+-].*)?$", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+(?:\s?\[at\]\s?|\s?\(at\)\s?|@)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NOISE_DOMAIN = re.compile(r"sentry|wixpress|example\.|domain\.|yourcompany|\.png|\.jpg|\.svg|\.webp", re.I)


def find_contacts(html, base=""):
    """Job-related e-mail addresses on a page (jobs@, careers@, hr@, ...): applying by e-mail."""
    soup = BeautifulSoup(html or "", "html.parser")
    blobs = [a["href"][7:].split("?")[0] for a in soup.find_all("a", href=True) if a["href"].lower().startswith("mailto:")]
    blobs.append(" ".join(soup.get_text(" ").split()))
    found = []
    for blob in blobs:
        for m in EMAIL_RE.finditer(blob):
            addr = re.sub(r"\s?(\[at\]|\(at\))\s?", "@", m.group(0)).lower()
            local, _, dom = addr.partition("@")
            if not dom or NOISE_DOMAIN.search(addr) or not JOBY_LOCAL.match(local):
                continue
            if addr not in found:
                found.append(addr)
    return found[:5]


def remember_contacts(name, r):
    try:
        emails = find_contacts(r.text, r.url)
    except Exception:
        return
    if emails:
        CONTACTS[name] = {"emails": emails, "url": r.url}


class Unreadable(Exception):
    """The careers page opened but nothing job-like could be extracted."""


def listing_links(html, base):
    """Number of distinct links that look like individual vacancy pages."""
    soup = BeautifulSoup(html, "html.parser")
    return len({urljoin(base, a["href"]) for a in soup.find_all("a", href=True) if JOB_DETAIL.search(a["href"])})


def diag(html):
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join((soup.title.string or "").split())[:60] if soup.title and soup.title.string else ""
    host_rx = re.compile(r"[\w.-]*(?:" + ATS_HINT.pattern + r")[\w./-]*", re.I)
    hints = sorted({m.group(0).lower()[:70] for m in host_rx.finditer(html)})[:4]
    return (f"страница открылась, вакансий не найдено; title='{title}'; HTML {len(html)} зн.; "
            f"ссылок {len(soup.find_all('a'))}; признаки систем: {', '.join(hints) or 'нет'}")


def jobby_feed(url, jobs):
    """A discovered feed counts only if it is about jobs (not a blog /feed)."""
    if JOB_HREF.search(url):
        return True
    return sum(1 for j in jobs if JOB_HREF.search(j.url)) >= max(1, 0.6 * len(jobs))


def generic(url, html, co):
    """Fallback: JSON-LD JobPosting blocks and links whose text looks like a QA-type title."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, Job] = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("title"):
                loc = item.get("jobLocation")
                loc = loc[0] if isinstance(loc, list) and loc else loc
                addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
                where = ", ".join(filter(None, [addr.get("addressLocality", ""), addr.get("addressCountry", "") if isinstance(addr.get("addressCountry"), str) else ""]))
                link = item.get("url") or url
                jobs[link + item["title"]] = Job(co, item["title"], link, where, text_of(item.get("description", ""))[:3000], "jsonld")
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(url, a["href"])
        if not href.startswith("http") or not (4 <= len(text) <= 160) or not TITLE_RE.search(text):
            continue
        if not (JOB_HREF.search(href) or JOB_HREF.search(url)):
            continue  # news/blog link that merely contains a keyword
        jobs.setdefault(href, Job(co, text, href, "", "", "page"))
    return list(jobs.values())


FEED_PATHS = ("/jobs.rss", "/jobs/feed", "/careers/feed", "/careers.rss", "/feed", "/rss", "/jobs.xml", "/careers/rss")


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(content, co):
    """Parse RSS 2.0 or Atom bytes into jobs. Returns None if it is not a feed."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    kind = _local(root.tag)
    if kind not in ("rss", "feed", "rdf"):
        return None
    jobs = []
    for it in root.iter():
        if _local(it.tag) not in ("item", "entry"):
            continue
        f = {}
        for ch in it:
            n = _local(ch.tag)
            if n == "link":
                f.setdefault("link", ch.get("href") or (ch.text or "").strip())
            elif n in ("title", "description", "summary", "content"):
                f.setdefault(n, ch.text or "")
            elif "location" in n or n in ("city", "country"):
                f["loc"] = ", ".join(filter(None, [f.get("loc", ""), " ".join((ch.text or "").split())]))
        title = " ".join((f.get("title") or "").split())
        if title and f.get("link"):
            desc = f.get("description") or f.get("content") or f.get("summary") or ""
            jobs.append(Job(co, title, f["link"], f.get("loc", ""), text_of(desc)[:3000], "rss"))
    return jobs


def fetch_feed(url, co):
    r = get(url)
    if r.status_code != 200:
        return None
    return parse_feed(r.content, co)


def discover_feeds(html, base):
    """Feed URLs advertised by the page (<link rel=alternate>) or linked from it, then common paths."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for l in soup.find_all("link", href=True):
        if "alternate" in (l.get("rel") or []) and ("rss" in (l.get("type") or "") or "atom" in (l.get("type") or "")):
            urls.append(urljoin(base, l["href"]))
    for a in soup.find_all("a", href=True):
        h = a["href"].lower()
        if h.endswith((".rss", ".xml")) or "/rss" in h or "/feed" in h:
            urls.append(urljoin(base, a["href"]))
    root = "/".join(base.split("/")[:3])
    urls += [root + p for p in FEED_PATHS] + [base.rstrip("/") + p for p in FEED_PATHS[:3]]
    seen = []
    for u in urls:
        if u.startswith("http") and u not in seen:
            seen.append(u)
    return seen[:12]


def from_feeds(html, base, co):
    """First discovered feed that parses and has items wins: (jobs, recipe) or None."""
    for u in discover_feeds(html, base):
        try:
            jobs = fetch_feed(u, co)
        except Exception:
            continue
        if jobs and jobby_feed(u, jobs):
            return jobs, {"t": "feed", "url": u}
    return None


CAREERS_LINK = re.compile(r"career|jobs|join|vacanc|hiring|work-with-us|work with us|empleo|trabaj|carri", re.I)


def find_careers_link(html, base):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if CAREERS_LINK.search(a["href"]) or CAREERS_LINK.search(a.get_text(" ", strip=True)):
            href = urljoin(base, a["href"])
            if href.startswith("http"):
                return href
    return ""


MODE = {"ats": "ats", "feed": "rss", "page": "page"}
ATS_FN = {name: fn for name, _, fn in ATS}


def reader_jobs(r, co):
    """Vacancies the browser reader found itself (page links, the page's own JSON requests, JSON-LD)."""
    out = {}
    for j in getattr(r, "jobs", None) or []:
        title, link = (j.get("title") or "").strip(), (j.get("url") or "").strip()
        if not title or not link.startswith("http"):
            continue
        out.setdefault(link, Job(co, title, link, j.get("location", "") or "", "", "page"))
    return list(out.values())


def page_jobs(r, co):
    """Vacancies from a fetched page: anchors/JSON-LD in the HTML plus whatever the browser reader extracted."""
    jobs = {j.url: j for j in generic(r.url, r.text, co)}
    for j in reader_jobs(r, co):
        jobs.setdefault(j.url, j)
    return list(jobs.values())


def _read_page(url, name, render=False):
    """One page: feed body, ATS, discovered feed, then HTML links. Returns (jobs, recipe, response)."""
    r = page_get(url, render)
    remember_contacts(name, r)
    r.raise_for_status()
    if r.content[:200].lstrip().startswith(b"<"):
        jobs = parse_feed(r.content, name)
        if jobs:
            return jobs, {"t": "feed", "url": url}, r
    hit = from_ats(r.url, r.text, name) or from_feeds(r.text, r.url, name)
    if hit:
        return hit[0], hit[1], r
    jobs = page_jobs(r, name)
    if jobs:
        return jobs, {"t": "page", "url": url, "render": render}, r
    if listing_links(r.text, r.url) >= 1:  # a real vacancy list that simply has no QA-type roles
        return [], {"t": "page", "url": url, "render": render, "listing": True}, r
    if EMPTY_RE.search(visible_text(r.text)):  # the page itself says there are no openings: readable, zero jobs
        return [], {"t": "page", "url": url, "render": render, "empty": True}, r
    return None, None, r


def run_recipe(rec, name):
    """Re-use a method that worked before (no discovery, no wasted scrape.do credits)."""
    if rec["t"] == "ats":
        return ATS_FN[rec["a"]](rec["slug"], name)
    if rec["t"] == "feed":
        jobs = fetch_feed(rec["url"], name)
        if jobs is None:
            raise RuntimeError("feed no longer parses")
        return jobs
    r = page_get(rec["url"], rec.get("render", False))
    r.raise_for_status()
    jobs = page_jobs(r, name)
    if not jobs:
        ok = (rec.get("listing") and listing_links(r.text, r.url) >= 1) or (rec.get("empty") and EMPTY_RE.search(visible_text(r.text)))
        if not ok:
            raise RuntimeError("page changed")
    return jobs


def scrape(company: dict):
    """Return (jobs, mode, recipe). Raises on network errors so the caller can mark the company failed.
    company["rss"]: manual feed URL (tried first). company["method"]: recipe cached from an earlier run."""
    name = company["name"]
    if company.get("rss"):
        jobs = fetch_feed(company["rss"], name)
        if jobs is not None:
            return jobs, "rss", {"t": "feed", "url": company["rss"]}
    rec = company.get("method")
    if rec:
        try:
            jobs = run_recipe(rec, name)
            if jobs or rec["t"] in ("ats", "feed") or rec.get("listing") or rec.get("empty"):
                return jobs, MODE[rec["t"]], rec
        except BudgetExceeded:
            raise
        except Exception:
            pass  # recipe broke: rediscover below
    url = company["url"]
    renders = render_steps()
    first = last = err = cand = None
    for render in renders:  # plain request first, the browser only if nothing was found (or the plain request was refused)
        try:
            jobs, rec, r = _read_page(url, name, render)
        except BudgetExceeded:
            raise
        except Exception as e:  # 403 / connection reset on the plain request: the browser may still get through
            err = e
            continue
        first = first or r
        last = r
        if jobs is not None:
            if rec["t"] == "page" and render is False and len(renders) > 1:
                cand = (jobs, rec)   # links scanned in raw HTML: the browser may see more (cards built by scripts); keep the larger
                continue
            if cand and len(cand[0]) >= len(jobs):
                return cand[0], MODE[cand[1]["t"]], cand[1]
            return jobs, MODE[rec["t"]], rec
    if cand:
        return cand[0], MODE[cand[1]["t"]], cand[1]
    if first is None:
        raise err
    link = find_careers_link(first.text, first.url)
    if link and link.rstrip("/") != url.rstrip("/"):
        for render in renders:
            try:
                jobs, rec, r = _read_page(link, name, render)
            except BudgetExceeded:
                raise
            except Exception as e:
                err = e
                continue
            last = r
            if jobs is not None:
                return jobs, MODE[rec["t"]], rec
    if CONTACTS.get(name):   # no vacancy list, but the page gives an e-mail for applications: readable, zero vacancies
        return [], "contact", None
    raise Unreadable(diag(last.text))


def enrich(job: Job) -> bool:
    """For HTML-scanned jobs: load the vacancy page, drop it if closed. Returns False if closed."""
    r = page_get(job.url)
    text = " ".join(BeautifulSoup(r.text, "html.parser").get_text(" ").split())
    if (r.status_code in (403, 429, 503) or len(text) < 400) and browser_enabled():
        try:  # JavaScript-built or protected vacancy page: quick browser read of this page only
            r = page_get(job.url, "light")
            text = " ".join(BeautifulSoup(r.text, "html.parser").get_text(" ").split())
        except Exception:
            pass
    if r.status_code in (404, 410):
        return False
    if CLOSED_RE.search(text[:5000]):
        return False
    job.desc = text[:3000]
    return True
