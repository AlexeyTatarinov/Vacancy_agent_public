"""Decide which vacancies fit the user (Gemini). Cached per URL by the caller."""
import json
import os
import time

import requests

USAGE = {"req": 0, "in": 0, "out": 0, "rules": 0}  # filled per run, shown in the Telegram footer


def _count(resp_json):
    u = resp_json.get("usageMetadata") or {}
    USAGE["req"] += 1
    USAGE["in"] += u.get("promptTokenCount", 0)
    USAGE["out"] += u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)


CRITERIA = """You screen job postings for one candidate. Decide for each posting whether it FITS.

Candidate: relocation-seeking QA specialist (Ukrainian citizen based in the EU). Native Ukrainian/Russian speaker, works in games/VR QA.
Target roles (FIT if the posting is one of these or clearly similar):
- QA / QA tester / game tester / QA engineer / test analyst / test automation (any level)
- LQA / localization QA / linguistic QA / language tester, especially when Russian or Ukrainian is required or welcome
- Compliance / platform certification / submission / TRC-TCR-XR requirements QA, compliance manager
- Similar quality / testing roles in games, VR/XR, or industrial/medical VR products
NOT a fit: pure programmers, artists, designers, marketing, sales, HR, finance, management outside QA/compliance,
translators with no QA/testing element, and any job located in Russia or Belarus.

Location/relocation rule: any country is acceptable IF the candidate could actually take it:
FIT when the job is in Spain/EU/EEA/UK/other country and onsite or hybrid (relocation is the candidate's plan),
or remote and open to Spain/EU, or relocation/visa support is mentioned.
NOT a fit when it is remote but restricted to a region that excludes Spain/EU (e.g. "US only"), or when it
explicitly says no visa sponsorship/work authorisation required for a country the candidate cannot work in.
If location is not stated, FIT is allowed (set relocation to "unknown").

Return ONLY a JSON array, one object per posting:
{"id": <int>, "fit": true|false, "reason": "<one short sentence in Russian>", "relocation": "yes|remote|onsite_no_info|unknown|no"}

Postings:
"""


def gemini_call(prompt: str, models: list[str], key: str):
    for model in models:
        for _ in range(2):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                    },
                    timeout=90,
                )
            except requests.RequestException:
                break
            if r.status_code == 200:
                try:
                    _count(r.json())
                    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                except Exception:
                    break
            print(f"Gemini {model}: HTTP {r.status_code} {r.text[:160]}")
            if r.status_code == 429:
                time.sleep(20)
                continue
            break
    return None


def _models(settings):
    env = os.environ.get("GEMINI_MODELS")
    return env.split(",") if env else settings["gemini_models"]


def gemini_text(prompt, settings, search=False):
    """Plain-text answer; search=True lets Gemini use Google Search grounding."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return ""
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}
    if search:
        body["tools"] = [{"google_search": {}}]
    for model in _models(settings):
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=90)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            try:
                _count(r.json())
                return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
            except Exception:
                continue
        print(f"Gemini {model} (search={search}): HTTP {r.status_code} {r.text[:160]}")
    return ""


def judge(jobs, settings):
    """Return {url: {fit, reason, relocation}} for the jobs the AI answered about."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    models = _models(settings)
    out = {}
    if not key:
        print("GEMINI_API_KEY не задан: вакансии не оцениваются.")
        return out
    n = settings["batch_size"]
    for i in range(0, len(jobs), n):
        batch = jobs[i : i + n]
        items = [
            {"id": k, "company": j.company, "title": j.title, "location": j.location, "description": j.desc[:1200]}
            for k, j in enumerate(batch)
        ]
        res = gemini_call(CRITERIA + json.dumps(items, ensure_ascii=False), models, key)
        if isinstance(res, dict):
            res = next((v for v in res.values() if isinstance(v, list)), [])
        for o in res or []:
            try:
                j = batch[int(o["id"])]
                out[j.url] = {
                    "fit": bool(o["fit"]),
                    "reason": str(o.get("reason", ""))[:300],
                    "relocation": str(o.get("relocation", "unknown")),
                }
            except Exception:
                continue
    return out
