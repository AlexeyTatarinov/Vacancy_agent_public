"""Daily vacancy agent. Run: python -m agent.main [--force] [--only NAME]"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import report, telegram
from .http import CREDITS, BudgetExceeded
from . import ats as ats_mod
from .ats import Unreadable, enrich, scrape
from .companies import load_companies
from .judge import USAGE, judge
from . import roles
from .models import BAD_COUNTRY_RE

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_state(root=ROOT):
    p = root / "state" / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"jobs": {}}


def save_state(st, root=ROOT):
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True))


def update_contacts(st, ov, names, today):
    """Applying without a vacancy list: e-mails found on career pages (job/career/hr...) plus links from overrides.
    Remembered in state so that pages skipped on a given day keep their contacts."""
    store = st.setdefault("contacts", {})
    for name, rec in ats_mod.CONTACTS.items():
        store[name] = {"emails": rec["emails"], "url": rec["url"], "seen": today}
    applied = {x.lower() for x in ov.get("applied", [])}
    rows = []
    for name in sorted(names):
        rec = store.get(name)
        manual = {k.lower(): v for k, v in ov.get("open_applications", {}).items()}.get(name.lower()) or \
            next((v for k, v in ov.get("open_applications", {}).items() if k.lower() in name.lower() or name.lower() in k.lower()), None)
        if not rec and not manual:
            continue
        rows.append({"name": name, "emails": (rec or {}).get("emails", []),
                     "url": (manual or {}).get("url") or (rec or {}).get("url", ""),
                     "note": (manual or {}).get("note", ""),
                     "applied": any(a in name.lower() or name.lower() in a for a in applied)})
    return rows


def run(force=False, only="", root=ROOT, scrape_fn=scrape, judge_fn=judge, enrich_fn=enrich):
    settings = json.loads((root / "config" / "settings.json").read_text())
    now = dt.datetime.now(ZoneInfo(settings["timezone"]))
    if not force and now.hour != settings["run_hour"]:
        print(f"Local hour is {now.hour}, expected {settings['run_hour']}; skipping.")
        return None
    today = now.date().isoformat()

    CREDITS["spent"], CREDITS["budget"] = 0, settings.get("scrapedo_daily_budget")
    USAGE.update(req=0, rules=0, **{"in": 0, "out": 0})
    companies = load_companies(root, settings, only)
    st = load_state(root)
    jobs_state = st["jobs"]
    methods = st.setdefault("methods", {})
    bad = st.setdefault("bad", {})          # unreadable companies: do not retry (and spend credits) every day
    checked = st.setdefault("checked", {})  # last successful read of HTML-only companies
    day0 = now.date()

    def age(date_str):
        return (day0 - dt.date.fromisoformat(date_str)).days

    def work(c):
        if not c["url"] and not c.get("rss"):
            why = ("адрес из списка/поиска ошибочный, нужна ссылка от владельца" if c.get("needs_link")
                   else "карьерная страница не найдена: добавьте ссылку в config/overrides.json")
            return c, None, "none", why
        key, rec0 = c["key"], methods.get(c["key"])
        sig = c["url"] + "|" + c.get("rss", "")
        b = bad.get(key)
        if b and b["sig"] == sig and age(b["date"]) < settings["retry_unreadable_days"] and not only:
            left = settings["retry_unreadable_days"] - age(b["date"])
            return c, None, "backoff", f'{b["reason"]} [повтор через {left} дн.]'
        if rec0 and rec0["t"] == "page" and key in checked and age(checked[key]) < settings["page_every_days"] and not only:
            return c, None, "skipped", ""
        c["method"] = rec0
        try:
            jobs, mode, rec = scrape_fn(c)
            if rec and (jobs or rec["t"] in ("ats", "feed") or rec.get("listing") or rec.get("empty")):
                methods[c["key"]] = rec
            checked[key] = today
            bad.pop(key, None)
            return c, jobs, mode, ""
        except BudgetExceeded as e:
            return c, None, "budget", str(e)
        except Unreadable as e:
            if c.get("no_openings") or c.get("open_app"):
                return c, [], "no_openings" if c.get("no_openings") else "contact", ""
            bad[key] = {"sig": sig, "date": today, "reason": str(e)[:300]}
            return c, None, "unreadable", str(e)
        except Exception as e:  # network/HTTP failure
            if c.get("no_openings") or c.get("open_app"):   # owner: no openings / general application only: unreadable is not an error
                return c, [], "no_openings" if c.get("no_openings") else "contact", ""
            return c, None, "error", f"{type(e).__name__}: {str(e)[:120]}"

    ats_mod.CONTACTS.clear()
    with ThreadPoolExecutor(settings["workers"]) as ex:
        results = list(ex.map(work, companies))
    ov = json.loads((root / "config" / "overrides.json").read_text(encoding="utf-8"))
    contacts = update_contacts(st, ov, {c["name"] for c in companies}, today)

    failed, html_only, ok, coverage = [], [], 0, []
    current: dict[str, object] = {}
    fetched_ok = set()
    for c, jobs, mode, err in results:
        if jobs is None and mode == "skipped":
            coverage.append((c["name"], f'пропущено (HTML раз в {settings["page_every_days"]} дн.)', 0, 0))
            continue
        if jobs is None:
            failed.append((c["name"], err))
            coverage.append((c["name"], "не прочитано", 0, 0))
            continue
        ok += 1
        fetched_ok.add(c["name"])
        if mode == "page":
            html_only.append(c["name"])
        coverage.append((c["name"], {"no_openings": "вакансий нет (по словам владельца)", "contact": "только почта для резюме"}.get(mode, mode), len(jobs), sum(1 for j in jobs if roles.is_candidate(j.title, root))))
        for j in jobs:
            if BAD_COUNTRY_RE.search(j.location) or BAD_COUNTRY_RE.search(j.title):
                continue
            if roles.is_candidate(j.title, root):
                current[j.url] = j

    # Companies that were read successfully: anything not seen now is closed.
    for url, rec in jobs_state.items():
        if rec["company"] in fetched_ok and url not in current:
            rec["open"] = False

    fresh = [j for u, j in current.items() if u not in jobs_state or not jobs_state[u].get("judged")]
    new_urls = {u for u, j in current.items() if u not in jobs_state}

    # HTML-scanned jobs need their page loaded (description + closed check).
    cap = settings["max_page_jobs_per_company"]
    per_company: dict[str, int] = {}
    ready = []
    for j in fresh:
        if j.source in ("page",):
            per_company[j.company] = per_company.get(j.company, 0) + 1
            if per_company[j.company] > cap:
                continue
            try:
                if not enrich_fn(j):
                    new_urls.discard(j.url)
                    continue
            except Exception:
                pass
        ready.append(j)

    auto, rest = roles.split(ready, root) if settings.get("rules_autofit", True) else ({}, ready)
    verdicts = {**judge_fn(rest, settings), **auto} if rest else dict(auto)
    unjudged = sum(1 for j in rest if j.url not in verdicts)
    print(f"Оценка: правилами {len(auto)}, через Gemini {len(rest)}")
    USAGE["rules"] = len(auto)
    announce = {j.url for j in ready if verdicts.get(j.url, {}).get("fit")}
    for j in ready:
        v = verdicts.get(j.url)
        rec = jobs_state.get(j.url, {"first_seen": today})
        rec.update(
            company=j.company, title=j.title, location=j.location, last_seen=today, open=True,
            judged=bool(v),
            fit=v["fit"] if v else False,  # no AI answer: not announced, re-judged next run
            reason=v["reason"] if v else "ожидает оценки ИИ",
            relocation=v["relocation"] if v else "unknown",
        )
        jobs_state[j.url] = rec
    for u in current:
        if u in jobs_state:
            jobs_state[u].update(last_seen=today, open=True)

    open_fit = [
        {**rec, "url": u} for u, rec in jobs_state.items() if rec.get("open") and rec.get("fit")
    ]
    open_fit.sort(key=lambda r: (r["company"].lower(), r["title"].lower()))
    # Forget jobs closed for over 30 days.
    cutoff = (now.date() - dt.timedelta(days=30)).isoformat()
    st["jobs"] = {u: r for u, r in jobs_state.items() if r.get("open") or r.get("last_seen", today) >= cutoff}

    md, page = report.build(
        today, open_fit, announce,
        failed, html_only, {"ok": ok, "total": len(companies)}, coverage, contacts,
    )
    report.write(root, md, page, today)
    save_state(st, root)
    if os.environ.get("NO_TELEGRAM"):
        print("NO_TELEGRAM set: Telegram message not sent.")
    else:
        telegram.send(telegram.build_messages(
            today, open_fit, announce, failed, CREDITS["spent"], os.environ.get("GITHUB_REPOSITORY", ""),
            settings.get("telegram_max_items", 60), unjudged, dict(USAGE)))
    print(f"Gemini: запросов {USAGE['req']}, токенов вход {USAGE['in']}, выход {USAGE['out']}")
    print(f"Companies ok {ok}/{len(companies)}; open fit {len(open_fit)}; failed {len(failed)}")
    return open_fit


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    run(force=a.force or bool(os.environ.get("FORCE")), only=a.only or os.environ.get("ONLY", ""))
    sys.exit(0)
