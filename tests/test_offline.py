import json
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import ats, companies, main  # noqa: E402
from agent.companies import parse_md  # noqa: E402
from agent.models import BAD_COUNTRY_RE, Job, TITLE_RE  # noqa: E402


def _copy_cfg(tmp):
    """Settings for temporary projects: rule-based verdicts off (tests drive the judge), roles dictionary present."""
    st = json.loads((ROOT / "config" / "settings.json").read_text())
    st["rules_autofit"] = False
    (tmp / "config" / "settings.json").write_text(json.dumps(st))
    shutil.copy(ROOT / "config" / "roles.json", tmp / "config")


def test_parse_md():
    cs = {c["name"]: c for c in parse_md(ROOT / "config" / "companies.md")}
    assert len(cs) == 63
    assert cs["Resolution Games"]["url"] == "https://jobs.resolutiongames.com"
    assert cs["Meta Reality Labs"]["url"] == "https://metacareers.com"
    assert "Pingle Studio" not in cs and "Survios" not in cs
    assert sum(1 for c in parse_md(ROOT / "config" / "companies.md") if c["name"] == "NipsApp Game Studios") == 1
    assert cs["Coatsink"]["name"] == "Coatsink"


def test_detect_ats():
    html = '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>'
    assert [(n, s) for n, s, _ in ats.detect_ats("https://x.com/jobs", html)][0] == ("greenhouse", "acme")
    assert [(n, s) for n, s, _ in ats.detect_ats("https://jobs.lever.co/foo", "")][0] == ("lever", "foo")
    assert list(ats.detect_ats("https://example.com", "<a href='/x'>hi</a>")) == []


def test_title_filter():
    for t in ["QA Tester", "Senior LQA (Russian)", "Compliance Manager", "Platform Certification Lead", "Game Tester - Dublin"]:
        assert TITLE_RE.search(t), t
    for t in ["Gameplay Programmer", "3D Artist", "Marketing Lead"]:
        assert not TITLE_RE.search(t), t
    assert BAD_COUNTRY_RE.search("Moscow, Russia")


def test_generic_page_and_jsonld():
    html = """<a href="/jobs/1">QA Tester (Prague)</a><a href="/jobs/2">3D Artist</a>
    <script type="application/ld+json">{"@type":"JobPosting","title":"LQA Ukrainian","url":"https://x.com/j/3",
    "description":"<p>Test</p>","jobLocation":{"address":{"addressLocality":"Warsaw","addressCountry":"PL"}}}</script>"""
    jobs = ats.generic("https://x.com/careers", html, "X")
    assert {j.title for j in jobs} == {"QA Tester (Prague)", "LQA Ukrainian"}
    assert [j for j in jobs if j.title.startswith("LQA")][0].location == "Warsaw, PL"


def test_greenhouse_parse(monkeypatch=None):
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"jobs": [{"title": "QA", "absolute_url": "https://g/1", "location": {"name": "Madrid"}, "content": "&lt;p&gt;Hello&lt;/p&gt;"}]}
    orig = ats.get
    ats.get = lambda url, **kw: R()
    try:
        j = ats.greenhouse("acme", "Acme")[0]
    finally:
        ats.get = orig
    assert (j.title, j.location, j.desc) == ("QA", "Madrid", "Hello")


def test_feeds():
    rss = b"""<?xml version="1.0"?><rss version="2.0" xmlns:t="http://x/t"><channel>
    <item><title>QA Tester</title><link>https://x/1</link><description>&lt;p&gt;Hi&lt;/p&gt;</description><t:location>Madrid</t:location></item></channel></rss>"""
    j = ats.parse_feed(rss, "X")
    assert (j[0].title, j[0].url, j[0].location, j[0].source) == ("QA Tester", "https://x/1", "Madrid", "rss")
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>LQA</title><link href="https://x/2"/><summary>s</summary></entry></feed>"""
    assert ats.parse_feed(atom, "X")[0].url == "https://x/2"
    assert ats.parse_feed(b"<html><body/></html>", "X") is None
    html = '<link rel="alternate" type="application/rss+xml" href="/jobs.xml">'
    assert ats.discover_feeds(html, "https://x.com/careers")[0] == "https://x.com/jobs.xml"


def test_blog_feed_rejected_and_listing_detected():
    blog = [Job("X", "Our new game trailer", "https://x.com/2026/10/trailer", "", "", "rss")]
    jobs = [Job("X", "QA", "https://x.com/jobs/1", "", "", "rss")]
    assert not ats.jobby_feed("https://x.com/feed/", blog)
    assert ats.jobby_feed("https://x.com/feed/", jobs) and ats.jobby_feed("https://n-ix.com/jobs/feed", blog)
    html = "".join(f'<a href="/careers/role-{i}">Role {i}</a>' for i in range(3))
    assert ats.listing_links(html, "https://x.com/careers") == 3
    assert "myworkdayjobs" in ats.diag("<title>Careers</title><a href='https://x.wd1.myworkdayjobs.com/a'>x</a> workday")
    # news link with a keyword is not a job
    assert ats.generic("https://x.com/", '<a href="/news/ukrainian-army">Ukrainian army deal</a>', "X") == []


def test_unjudged_not_announced_and_retried():
    tmp = pathlib.Path(tempfile.mkdtemp())
    for d in ("config", "state", "reports", "docs"):
        (tmp / d).mkdir()
    _copy_cfg(tmp)
    shutil.copy(ROOT / "config" / "overrides.json", tmp / "config")
    (tmp / "config" / "companies.md").write_text(
        "## 1. T\n\n| Компания | Страна/город | Сфера | Где смотреть вакансии | Статус |\n|---|---|---|---|---|\n| Alpha | ES | x | alpha.example | n |\n",
        encoding="utf-8")
    sent = []
    main.telegram.send = lambda m: sent.extend(m)
    rec = {"t": "ats", "a": "greenhouse", "slug": "alpha"}
    scr = lambda c: ([Job("Alpha", "QA Tester", "https://a/1", "Valencia", "d", "greenhouse")], "ats", rec)
    out = main.run(force=True, root=tmp, scrape_fn=scr, judge_fn=lambda j, s: {})
    assert out == [] and any("ИИ не оценил" in m for m in sent)
    sent.clear()
    out = main.run(force=True, root=tmp, scrape_fn=scr, judge_fn=lambda j, s: {j[0].url: {"fit": True, "reason": "ок", "relocation": "yes"}})
    assert [j["url"] for j in out] == ["https://a/1"] and any("QA Tester" in m for m in sent)


def test_full_run_and_state():
    tmp = pathlib.Path(tempfile.mkdtemp())
    for d in ("config", "state", "reports", "docs"):
        (tmp / d).mkdir()
    _copy_cfg(tmp)
    shutil.copy(ROOT / "config" / "overrides.json", tmp / "config")
    (tmp / "config" / "companies.md").write_text(
        "## 1. Test\n\n| Компания | Страна/город | Сфера | Где смотреть вакансии | Статус |\n|---|---|---|---|---|\n"
        "| Alpha | ES | x | alpha.example | n |\n| Beta | PT | x | beta.example | n |\n| Gamma | FR | x | не проверено | n |\n",
        encoding="utf-8",
    )
    (tmp / "state").joinpath("careers.json").write_text(json.dumps({"gamma": {"url": "", "checked": "2999-01-01"}}))

    def fake_scrape(c):
        if c["name"] == "Beta":
            raise RuntimeError("boom")
        return [Job("Alpha", "QA Tester", "https://a/1", "Valencia", "d", "greenhouse"),
                Job("Alpha", "Artist", "https://a/2", "", "", "greenhouse"),
                Job("Alpha", "QA Analyst", "https://a/3", "Moscow, Russia", "", "greenhouse")], "ats", {"t": "ats", "a": "greenhouse", "slug": "alpha"}

    def fake_judge(jobs, settings):
        return {j.url: {"fit": True, "reason": "ок", "relocation": "yes"} for j in jobs}

    companies._gemini = lambda c, s: ""
    companies._ddg = lambda q: ""
    sent = []
    main.telegram.send = lambda m: sent.extend(m)
    out = main.run(force=True, root=tmp, scrape_fn=fake_scrape, judge_fn=fake_judge)
    assert any("QA Tester" in m and "Alpha" in m for m in sent)
    assert json.loads((tmp / "state" / "state.json").read_text())["methods"]["alpha"]["slug"] == "alpha"
    assert [j["url"] for j in out] == ["https://a/1"]
    md = (tmp / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "QA Tester" in md and "Moscow" not in md and "Beta: RuntimeError" in md and "Gamma" in md
    assert "Покрытие по компаниям" in md and "| Alpha | ats | 3 | 2 |" in md
    assert (tmp / "docs" / "index.html").exists()

    # Second run: job disappears from a successfully read company -> closed.
    out2 = main.run(force=True, root=tmp, scrape_fn=lambda c: ([], "ats", {"t": "ats", "a": "greenhouse", "slug": "alpha"}) if c["name"] == "Alpha" else (_ for _ in ()).throw(RuntimeError("x")), judge_fn=fake_judge)
    assert out2 == []


def test_scrapedo_and_recipe():
    import os
    from agent import http
    calls = []

    class R:
        status_code = 200
        text = "<a href='/j/1'>QA Tester</a>"
        content = b"x"
    http.S.get = lambda url, **kw: (calls.append((url, kw)), R())[1]
    os.environ["SCRAPEDO_TOKEN"] = "SECRET"
    os.environ["READER"] = "scrapedo"      # legacy path (rollback); the default is the own browser
    http.CREDITS.update(spent=0, budget=6)
    r = http.page_get("https://x.com/careers", render=True)
    assert calls[0][0] == "https://api.scrape.do/" and calls[0][1]["params"]["render"] == "true"
    assert r.url == "https://x.com/careers" and http.CREDITS["spent"] == 5
    try:
        http.page_get("https://x.com/b", render=True)
        assert False
    except http.BudgetExceeded:
        pass
    # cached page recipe re-used without discovery
    ats.page_get = http.page_get
    jobs = ats.run_recipe({"t": "page", "url": "https://x.com/careers", "render": False}, "X")
    assert jobs[0].title == "QA Tester" and http.CREDITS["spent"] == 6
    del os.environ["SCRAPEDO_TOKEN"]
    del os.environ["READER"]


def test_browser_reader_is_default_and_feeds_the_page_flow():
    import os
    import types
    from agent import http
    assert http.reader_mode() == "browser"
    seen = {}

    def fake_fetch(url, light=False, config=None):
        seen.update(url=url, light=light, config=config)
        html = "<html><body>JS-built list</body></html>"
        return types.SimpleNamespace(
            status_code=200, status="ok", html=html, final_url=url, error=None, warnings=[],
            jobs=[{"title": "LQA Ukrainian", "url": "https://x.com/jobs/lqa-1", "location": "Warsaw"},
                  {"title": "Broken", "url": "/relative-only"}])
    import web_reader.api as api
    orig = api.fetch
    api.fetch = fake_fetch
    try:
        r = http.page_get("https://x.com/careers", render=True)
        assert seen["light"] is False and seen["config"].endswith("config/reader.json")
        assert "JS-built list" in r.text
        jobs = ats.page_jobs(r, "X")                       # vacancies found by the reader itself, not only anchors in HTML
        assert [j.title for j in jobs] == ["LQA Ukrainian"] and jobs[0].location == "Warsaw"
        http.page_get("https://x.com/job/1", render="light")
        assert seen["light"] is True
    finally:
        api.fetch = orig


def test_browser_reader_blocked_page_raises_not_silently_empty():
    import types
    from agent import http
    import web_reader.api as api
    orig = api.fetch
    api.fetch = lambda url, light=False, config=None: types.SimpleNamespace(
        status_code=403, status="blocked", html="", final_url=url, error=None, warnings=["w_antibot access_denied"], jobs=[])
    try:
        r = http.page_get("https://x.com/careers", render=True)
        try:
            r.raise_for_status()
            assert False
        except RuntimeError as e:
            assert "blocked" in str(e)
    finally:
        api.fetch = orig


def test_chat_id_normalisation():
    from agent.telegram import norm_chat
    assert norm_chat("Vacancy_info_ot") == "@Vacancy_info_ot"
    assert norm_chat(" @Vacancy_info_ot ") == "@Vacancy_info_ot"
    assert norm_chat("-1001234567890") == "-1001234567890" and norm_chat("123456") == "123456"


def test_backoff_and_page_cadence():
    from agent.ats import Unreadable
    tmp = pathlib.Path(tempfile.mkdtemp())
    for d in ("config", "state", "reports", "docs"):
        (tmp / d).mkdir()
    _copy_cfg(tmp)
    shutil.copy(ROOT / "config" / "overrides.json", tmp / "config")
    (tmp / "config" / "companies.md").write_text(
        "## 1. T\n\n| Компания | Страна/город | Сфера | Где смотреть вакансии | Статус |\n|---|---|---|---|---|\n"
        "| Alpha | ES | x | alpha.example | n |\n| Beta | ES | x | beta.example | n |\n", encoding="utf-8")
    main.telegram.send = lambda m: None
    calls = []

    def scr(c):
        calls.append(c["name"])
        if c["name"] == "Alpha":
            raise Unreadable("js page")
        return [], "page", {"t": "page", "url": "https://beta.example", "render": False, "listing": True}

    main.run(force=True, root=tmp, scrape_fn=scr, judge_fn=lambda j, s: {})
    assert sorted(calls) == ["Alpha", "Beta"]
    calls.clear()
    main.run(force=True, root=tmp, scrape_fn=scr, judge_fn=lambda j, s: {})
    assert calls == []  # Alpha in backoff, Beta (HTML page) checked less than 3 days ago
    md = (tmp / "reports" / "latest.md").read_text(encoding="utf-8")
    assert "повтор через" in md and "пропущено" in md


def test_empty_listing_is_readable():
    class R:
        status_code = 200
        url = "https://x.com/jobs"
        content = b"<html>"
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass
    orig = ats.page_get
    try:
        ats.page_get = lambda u, render=False: R("<html><title>Jobs</title><h2>No matching jobs</h2><script>var a='x'</script></html>")
        jobs, rec, _ = ats._read_page("https://x.com/jobs", "X")
        assert jobs == [] and rec["empty"]
        ats.page_get = lambda u, render=False: R("<html><body><h1>Careers</h1><script>No open positions template</script></body></html>")
        assert ats._read_page("https://x.com/jobs", "X")[0] is None  # script text is ignored
        ats.page_get = lambda u, render=False: R('<a href="/jobs/123-product-manager">Product Manager</a>')
        jobs, rec, _ = ats._read_page("https://x.com/jobs", "X")
        assert jobs == [] and rec["listing"]
    finally:
        ats.page_get = orig


def test_new_ats_patterns():
    names = lambda url: [n for n, _, _ in ats.detect_ats(url, "")]
    assert names("https://altavr.pinpointhq.com/") == ["pinpoint"]
    assert names("https://ats.rippling.com/xrhealth-inc/jobs") == ["rippling"]


def test_telegram_message():
    from agent import telegram
    fit = [{"company": "A<b>", "title": "QA & Co", "url": "https://x/1?a=1&b=2", "location": "Madrid", "relocation": "yes", "reason": "ок"}]
    msgs = telegram.build_messages("2026-11-06", fit, {"https://x/1?a=1&b=2"}, [("Z", "err")], 7, "me/repo")
    text = "\n".join(msgs)
    assert "A&lt;b&gt;" in text and "QA &amp; Co" in text and "Не прочитано компаний: 1" in text and "me/repo" in text
    assert all(len(m) < 4096 for m in msgs)
    g = telegram.build_messages("d", fit, set(), [], 0, "", 60, 0, {"req": 3, "in": 12345, "out": 678})
    assert "запросов 3, токенов вход 12 345 / выход 678" in "\n".join(g)
    many = [dict(fit[0], url=f"https://x/{i}", reason="r" * 200) for i in range(80)]
    assert all(len(m) < 4096 for m in telegram.build_messages("d", many, {j["url"] for j in many}, [], 0))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_plain_request_refused_escalates_to_browser():
    import types
    from agent import http
    calls = []

    class Refused:
        status_code = 403
        text = ""
        content = b""
        url = "https://x.com/careers"
        def raise_for_status(self):
            raise RuntimeError("403 Forbidden")

    def fake_page_get(url, render=False):
        calls.append(render)
        if not render:
            return Refused()
        html = "<html><body>list</body></html>"
        return types.SimpleNamespace(status_code=200, text=html, content=html.encode(), url=url, raise_for_status=lambda: None,
                                     jobs=[{"title": "Game Tester", "url": "https://x.com/jobs/1", "location": "Madrid"}])
    orig_get, orig_steps = ats.page_get, ats.render_steps
    ats.page_get, ats.render_steps = fake_page_get, lambda: (False, True)
    try:
        jobs, mode, rec = ats.scrape({"name": "X", "url": "https://x.com/careers"})
    finally:
        ats.page_get, ats.render_steps = orig_get, orig_steps
    assert calls == [False, True] and [j.title for j in jobs] == ["Game Tester"] and rec["render"] is True


def test_roles_filter_and_rules():
    from agent import roles
    assert roles.is_candidate("Тестувальник ігор") and roles.is_candidate("Senior QA Engineer")
    assert not roles.is_candidate("Release Manager") and not roles.is_candidate("3D Artist")
    J = lambda t, loc, d="": Job("X", t, "u", loc, d)
    assert roles.rule_verdict(J("QA Tester", "Valencia, Spain"))["fit"]
    assert roles.rule_verdict(J("LQA Tester (Russian)", "Remote - EMEA"))["relocation"] == "remote"
    assert roles.rule_verdict(J("QA Tester", "Remote")) is None                      # open remote? unknown: Gemini
    assert roles.rule_verdict(J("QA Tester", "Remote, US only")) is None
    assert roles.rule_verdict(J("QA Tester", "Madrid", "We cannot offer visa sponsorship")) is None
    assert roles.rule_verdict(J("QA Tester", "")) is None
    assert roles.rule_verdict(J("Compliance Officer", "Madrid")) is None             # ambiguous: no platform marker
    assert roles.rule_verdict(J("Platform Compliance Specialist (TRC)", "Dublin, Ireland"))["fit"]
    assert roles.rule_verdict(J("Data QA Analyst", "Madrid")) is None
    auto, rest = roles.split([J("QA Tester", "Madrid"), J("QA Tester", "")])
    assert len(auto) == 1 and len(rest) == 1


def test_find_contacts_and_no_openings():
    html = ('<p>Write to <a href="mailto:Jobs@whiteelk.example?subject=cv">us</a>, press: press@whiteelk.example, '
            'or careers [at] whiteelk.example. Image logo@2x.png sales@whiteelk.example</p>')
    assert ats.find_contacts(html) == ["jobs@whiteelk.example", "careers@whiteelk.example"]
    assert ats.find_contacts("<p>info@x.com</p>") == []
    tmp = pathlib.Path(tempfile.mkdtemp())
    for d in ("config", "state", "reports", "docs"):
        (tmp / d).mkdir()
    _copy_cfg(tmp)
    ov = json.loads((ROOT / "config" / "overrides.json").read_text())
    ov["no_openings"] = ["Alpha"]
    ov["open_applications"] = {"Alpha": {"url": "https://a.example/apply", "note": "форма"}}
    (tmp / "config" / "overrides.json").write_text(json.dumps(ov))
    (tmp / "config" / "companies.md").write_text(
        "## 1. T\n\n| Компания | Страна/город | Сфера | Где смотреть вакансии | Статус |\n|---|---|---|---|---|\n| Alpha | ES | x | alpha.example | n |\n",
        encoding="utf-8")
    sent = []
    main.telegram.send = lambda m: sent.extend(m)

    def boom(c):
        ats.CONTACTS["Alpha"] = {"emails": ["jobs@alpha.example"], "url": "https://alpha.example/careers"}
        raise ats.Unreadable("nothing")
    main.run(force=True, root=tmp, scrape_fn=boom, judge_fn=lambda j, s: {})
    rep = (tmp / "reports" / "latest.md").read_text()
    assert "вакансий нет (по словам владельца)" in rep and "jobs@alpha.example" in rep and "Не удалось прочитать\n\nНет." in rep


def test_browser_result_wins_when_it_finds_more(monkeypatch):
    plain = [Job("X", "QA Tester", "https://x/1")]
    rendered = plain + [Job("X", "LQA Analyst", "https://x/2"), Job("X", "Compliance Lead", "https://x/3")]
    calls = []

    def fake_read(url, name, render=False):
        calls.append(render)
        return (rendered if render else plain), {"t": "page", "url": url, "render": render}, SimpleNamespace(text="", url=url)
    monkeypatch.setattr(ats, "_read_page", fake_read)
    monkeypatch.setattr(ats, "render_steps", lambda: (False, True))
    jobs, mode, rec = ats.scrape({"name": "X", "url": "https://x/careers"})
    assert calls == [False, True] and len(jobs) == 3 and rec["render"] is True
    monkeypatch.setattr(ats, "render_steps", lambda: (False,))
    jobs, mode, rec = ats.scrape({"name": "X", "url": "https://x/careers"})
    assert len(jobs) == 1
