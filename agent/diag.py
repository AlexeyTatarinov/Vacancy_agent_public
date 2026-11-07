"""Diagnose how one page is read: python -m agent.diag URL
Prints what the browser reader saw (status, warnings, vacancies found, page text, links, detected systems, e-mails)."""
import json
import pathlib
import sys

from bs4 import BeautifulSoup

from . import ats

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(url):
    from web_reader.api import fetch
    res = fetch(url, config=str(ROOT / "config" / "reader.json"))
    print(f"URL: {url}\nfinal: {res.final_url}\nstatus: {res.status} kind: {res.kind} http: {res.status_code}")
    print("warnings:", "; ".join(res.warnings[:10]) or "-", "| error:", res.error or "-")
    print(f"jobs from reader: {len(res.jobs)}")
    for j in res.jobs[:30]:
        print("  -", j.get("title"), "|", j.get("location", ""), "|", j.get("url"))
    html = res.html or ""
    print(f"html: {len(html)} chars; ats detected:", [(n, s) for n, s, _ in ats.detect_ats(res.final_url or url, html)][:6])
    print("emails:", ats.find_contacts(html))
    print("generic jobs:", [(j.title, j.url) for j in ats.generic(res.final_url or url, html, "diag")][:20])
    print("--- visible text (first 3500) ---")
    print(ats.visible_text(html)[:3500])
    print("--- links (first 150) ---")
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True)[:150]:
        print(" ", " ".join(a.get_text(" ").split())[:70], "->", a["href"][:130])


if __name__ == "__main__":
    main(sys.argv[1])
