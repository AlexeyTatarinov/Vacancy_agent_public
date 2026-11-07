import json
import pathlib
import shutil
import sys
import tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import ats, discover  # noqa: E402
from agent.models import Job  # noqa: E402


def test_slug_variants():
    v = discover.slug_variants("Resolution Games Ltd")
    assert "resolutiongamesltd" in v or "resolutiongames" in v
    assert "resolution" in v or "resolution-games" in v
    assert len(v) <= 4


def test_role_matcher_uses_dictionary():
    f = discover.load_role_matcher(ROOT)
    assert f("Game Tester") and f("Лінгвістичний тестувальник") and f("TRC Compliance Specialist")
    assert not f("3D Artist") and not f("Qatar Sales Manager")


def test_verify_via_website(monkeypatch):
    page = SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, url="https://acme.test/",
                           text='<a href="/careers">Careers</a>')
    careers = SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, url="https://acme.test/careers",
                              text='<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>')
    monkeypatch.setattr(discover, "get", lambda url, **kw: careers if url.endswith("/careers") else page)
    monkeypatch.setattr(ats, "greenhouse", lambda slug, co: [Job(co, "QA Tester", "u1"), Job(co, "Artist", "u2")])
    monkeypatch.setattr(ats, "ATS", [(n, rx, ats.greenhouse if n == "greenhouse" else fn) for n, rx, fn in ats.ATS])
    rec = discover.verify({"name": "Acme Games", "website": "https://acme.test", "source": "seed"}, discover.load_role_matcher(ROOT))
    assert rec["status"] == "board" and rec["ats"] == "greenhouse" and rec["jobs"] == 2
    assert rec["role_titles"] == ["QA Tester"] and rec["role_hits"] == 1


def test_probe_is_low_confidence(monkeypatch):
    def lever(slug, co):
        if slug != "foo":
            raise RuntimeError("nope")
        return [Job(co, "QA Analyst", "u")]
    monkeypatch.setattr(ats, "ATS", [("lever", ats.ATS[2][1], lever)])
    rec = discover.verify({"name": "Foo", "source": "gemini"}, lambda t: True)
    assert rec["status"] == "board_guess" and rec["slug"] == "foo"


def test_run_skips_known_and_writes_report(monkeypatch):
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "config").mkdir()
    for f in ("companies.md", "overrides.json", "settings.json", "roles.json"):
        shutil.copy(ROOT / "config" / f, tmp / "config" / f)
    cfg = {"max_new_per_run": 10, "recheck_after_days": 30, "workers": 2, "wikipedia_categories": [], "gemini_queries": [],
           "seed": [{"name": "Resolution Games"}, {"name": "Brand New Studio"}, {"name": "Moscow Games Russia"}]}
    (tmp / "config" / "discovery.json").write_text(json.dumps(cfg))
    seen = []

    def fake(c, is_role):
        seen.append(c["name"])
        return {"name": c["name"], "country": "", "source": "seed", "website": "", "checked": "2099-01-01",
                "status": "board", "ats": "lever", "slug": "x", "careers_url": "https://x", "jobs": 3,
                "role_titles": ["QA"], "role_hits": 1}
    monkeypatch.setattr(discover, "verify", fake)
    st = discover.run(tmp, use_wiki=False, use_gemini=False, verify_fn=fake)
    assert seen == ["Brand New Studio"] and list(st) == ["brandnewstudio"]
    assert "Brand New Studio" in (tmp / "docs" / "candidates.md").read_text()
    discover.promote(tmp, "Brand New Studio")
    assert "Brand New Studio" in (tmp / "config" / "companies.md").read_text()
    from agent.companies import parse_md
    assert any(c["name"] == "Brand New Studio" for c in parse_md(tmp / "config" / "companies.md"))
