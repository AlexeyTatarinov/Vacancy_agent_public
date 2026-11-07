"""Role filter (dictionary from config/roles.json) and rule-based verdicts for obvious cases (saves Gemini requests).

- is_candidate(title): STRONG match from the dictionary (QA, LQA, compliance, certification in EN/UK/RU).
  Weak-only words (release, platform, localization, translator...) do not send a vacancy to the judge.
- rule_verdict(job): FIT without Gemini only when the role is clearly QA/LQA (or platform compliance/certification)
  and the place is clearly acceptable: Spain/EU/EEA/UK, or remote open to Europe/worldwide. Anything uncertain
  (no location, restricted remote, visa wording in the text) still goes to Gemini.
"""
import pathlib
import re

from .models import TITLE_RE

ROOT = pathlib.Path(__file__).resolve().parent.parent

EUROPE = (
    "spain|españa|valencia|madrid|barcelona|sevilla|seville|málaga|malaga|bilbao|zaragoza|"
    "portugal|lisbon|lisboa|porto|france|paris|lyon|montpellier|germany|berlin|munich|hamburg|cologne|"
    "italy|milan|rome|turin|netherlands|amsterdam|utrecht|belgium|brussels|luxembourg|austria|vienna|"
    "ireland|dublin|cork|united kingdom|uk\\b|london|manchester|leamington|guildford|sheffield|dundee|glasgow|edinburgh|"
    "poland|warsaw|krakow|kraków|wroclaw|wrocław|gdansk|czech|prague|brno|slovakia|bratislava|hungary|budapest|"
    "romania|bucharest|cluj|iasi|bulgaria|sofia|croatia|zagreb|slovenia|ljubljana|serbia|belgrade|greece|athens|"
    "denmark|copenhagen|sweden|stockholm|malmö|malmo|norway|oslo|finland|helsinki|tampere|estonia|tallinn|"
    "latvia|riga|lithuania|vilnius|malta|cyprus|iceland|switzerland|zurich|ukraine|kyiv|kiev|lviv|"
    "europe|european|\\beu\\b|emea|eea"
)
EUROPE_RE = re.compile(rf"(?<!\w)(?:{EUROPE})", re.I)
REMOTE_RE = re.compile(r"remote|work from home|anywhere|worldwide|distributed|teletrabajo|télétravail", re.I)
OPEN_REMOTE_RE = re.compile(r"worldwide|anywhere|global|europe|european|\beu\b|emea|eea|spain|españa", re.I)
RESTRICTED_RE = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|north america|americas|latam|apac|asia|india|australia|nz|brazil|mexico)\b"
    r"[^.\n]{0,25}\b(only|based|residents?|citizens?)\b|"
    r"\b(must|need to|required to)\b[^.\n]{0,40}\b(authori[sz]ed|eligible|right) to work in the (us|u\.s\.|united states|canada)\b|"
    r"no (visa )?sponsorship|(cannot|can'?t|unable to|do(es)? not|don't|won't|will not) (offer|provide|support|sponsor)[^.\n]{0,30}(visa|sponsorship|relocation)|"
    r"not (able|willing) to sponsor|without (visa )?sponsorship|security clearance|"
    r"(us|u\.s\.) (citizen|citizenship|persons?)",
    re.I,
)
# Compliance / certification titles are ambiguous (banks, HR...): auto-FIT only with a games/platform marker.
PLATFORM_MARK_RE = re.compile(
    r"\b(trc|tcr|xr|xrs|vrc|lotcheck|lot check|console|playstation|xbox|nintendo|switch|steam|quest|oculus|meta|"
    r"platform|certification|submissions?|first[- ]party|game|games|gaming|vr)\b", re.I)

_cache = {}


def load(root=ROOT):
    key = str(root)
    if key not in _cache:
        try:
            from web_reader.roles import load_rules
            _cache[key] = load_rules(str(pathlib.Path(root) / "config" / "roles.json"))
        except Exception as e:  # dictionary missing: old behavior
            print(f"Словарь ролей недоступен ({type(e).__name__}): используется TITLE_RE")
            _cache[key] = None
    return _cache[key]


def matches(title, root=ROOT):
    """STRONG matches: list of {group, label, terms}; falls back to TITLE_RE (one pseudo-group)."""
    rules = load(root)
    if rules is None:
        return [{"group": "qa", "label": "TITLE_RE", "terms": [m.group(0)]} for m in [TITLE_RE.search(title)] if m]
    return rules.match(title, "strong")


def is_candidate(title, root=ROOT):
    return bool(matches(title, root))


def rule_verdict(job, root=ROOT):
    """Return a verdict dict for an obvious FIT, else None (leave to Gemini)."""
    ms = matches(job.title, root)
    if not ms:
        return None
    groups = {m["group"] for m in ms}
    clear_role = bool(groups & {"qa", "lqa"}) or bool(PLATFORM_MARK_RE.search(job.title))
    if "qa" in groups and not groups & {"lqa"} and re.search(r"\b(data|financial|food|laboratory|lab|clinical|pharma\w*|manufactur\w*)\b", job.title, re.I):
        clear_role = False
    if not clear_role:
        return None
    loc = job.location or ""
    text = f"{loc} {job.title} {job.desc[:2500]}"
    if RESTRICTED_RE.search(text):
        return None
    term = ms[0]["terms"][-1]
    if EUROPE_RE.search(loc) and not (REMOTE_RE.search(loc) and not OPEN_REMOTE_RE.search(loc)):
        return {"fit": True, "relocation": "remote" if REMOTE_RE.search(loc) else "yes",
                "reason": f"Правило: роль «{term}», место «{loc[:60]}» подходит (без ИИ)."}
    if REMOTE_RE.search(loc) and OPEN_REMOTE_RE.search(loc):
        return {"fit": True, "relocation": "remote",
                "reason": f"Правило: роль «{term}», удалённо и открыто для Европы (без ИИ)."}
    return None


def split(jobs, root=ROOT):
    """(auto_verdicts {url: verdict}, rest [jobs for Gemini])."""
    auto, rest = {}, []
    for j in jobs:
        v = rule_verdict(j, root)
        if v:
            auto[j.url] = v
        else:
            rest.append(j)
    return auto, rest
