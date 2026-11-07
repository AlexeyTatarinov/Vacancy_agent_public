"""Классификация ссылок и встроенных окон: что это такое и куда ведёт."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .safety import host_matches, normalize_url

JOB_PATH_HINTS = (
    "/job", "/jobs", "/career", "/careers", "/position", "/positions", "/opening",
    "/openings", "/vacanc", "/posting", "/postings", "/requisition", "/employment",
    "/oferta", "/empleo", "/emploi", "/offre", "/stelle", "/karriere", "/vaga",
    "/vakans", "/vacante", "/trabajo", "/o/", "/j/",
)
JOB_QUERY_KEYS = {"gh_jid", "jobid", "job_id", "jid", "requisitionid", "reqid", "posting", "jobreqid", "jobopeningid",
                  "jobdescriptionid", "vacancyid", "vacancy_id", "positionid", "position_id", "openingid", "opening_id",
                  "jobpostingid", "postingid", "jobref", "jobcode"}

SOCIAL_DOMAINS = (
    "linkedin.com", "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "youtu.be", "tiktok.com", "t.me", "telegram.me", "discord.gg",
    "discord.com", "reddit.com", "pinterest.com", "threads.net", "whatsapp.com", "wa.me",
    "medium.com", "glassdoor.com",
)
DOCUMENT_EXTS = (".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".odp", ".ods", ".zip", ".rar", ".7z", ".key", ".pages")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif")
_SECOND_LEVEL = {"co.uk", "com.au", "co.jp", "com.br", "co.nz", "com.mx", "co.za", "com.es", "com.ua", "co.in", "com.tr", "com.ar"}

_PAGINATION_TEXT = re.compile(
    r"^\s*(next|next page|siguiente|suivant|weiter|nächste|nächste seite|próxima|próximo|avanti|"
    r"seguinte|далее|вперёд|вперед|следующая|следующая страница|наступна|наступна сторінка|далі|›|»|→|>|>>)\s*$",
    re.I,
)

_FRAME_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("video", ("youtube.com", "youtube-nocookie.com", "youtu.be", "vimeo.com", "wistia.com", "wistia.net", "loom.com", "dailymotion.com")),
    ("map", ("openstreetmap.org", "mapbox.com", "maps.google.com")),
    ("presentation", ("slideshare.net", "canva.com", "prezi.com", "speakerdeck.com", "pitch.com", "issuu.com", "genial.ly")),
    ("form", ("typeform.com", "jotform.com", "tally.so", "forms.gle", "airtable.com", "forms.office.com")),
    ("chat", ("intercom.io", "intercomcdn.com", "drift.com", "crisp.chat", "tawk.to", "zendesk.com", "zdassets.com", "hubspot.com", "livechatinc.com")),
    ("social", ("facebook.com", "twitter.com", "platform.twitter.com", "instagram.com", "linkedin.com")),
]
FRAME_READ_KINDS = {"inline", "ats", "form", "presentation", "other"}


def site_key(host: str) -> str:
    """Грубое определение «сайта» по имени хоста: последние два (иногда три) звена."""
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    tail2 = ".".join(labels[-2:])
    if tail2 in _SECOND_LEVEL and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail2


def same_site(url_a: str, url_b: str) -> bool:
    ha = urlsplit(url_a).hostname or ""
    hb = urlsplit(url_b).hostname or ""
    return site_key(ha) == site_key(hb)


def looks_like_job_url(url: str, ats_domains: list[str] | tuple[str, ...] = ()) -> bool:
    """Эвристика: похож ли адрес на страницу конкретной вакансии (а не на общий раздел)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    query = {k.lower() for k, _ in parse_qsl(parts.query)}
    if query & JOB_QUERY_KEYS:
        return True
    segs = [s for s in parts.path.lower().split("/") if s]
    if host_matches(host, ats_domains):
        return len(segs) >= 2
    for i, seg in enumerate(segs):
        hint = "/" + seg
        if any(hint == h or hint.startswith(h) for h in JOB_PATH_HINTS) and i < len(segs) - 1:
            return True
        if ("/" + seg + "/") in JOB_PATH_HINTS and i < len(segs) - 1:  # /o/ и /j/
            return True
    return False


def is_pagination_link(text: str, rel: str, aria: str, href: str, page_url: str) -> bool:
    """Ссылка «следующая страница» списка (остаётся на том же хосте)."""
    norm = normalize_url(href, page_url)
    if not norm:
        return False
    if (urlsplit(norm).hostname or "") != (urlsplit(page_url).hostname or ""):
        return False
    if "next" in rel.lower().split():
        return True
    return bool(_PAGINATION_TEXT.match(text or "") or _PAGINATION_TEXT.match(aria or ""))


def classify_link(href: str, page_url: str, settings: Any, text: str = "",
                  rel: str = "", aria: str = "") -> str:
    """Тип ссылки: anchor, mailto, tel, javascript, pdf, document, image, social,
    ats_job, ats_board, pagination, job_internal, internal, external, other."""
    h = (href or "").strip()
    low = h.lower()
    if low.startswith("javascript:"):
        return "javascript"
    if low.startswith("mailto:"):
        return "mailto"
    if low.startswith("tel:"):
        return "tel"
    norm = normalize_url(h, page_url)
    if not norm:
        return "other"
    page_norm = normalize_url(page_url) or page_url
    if "#" in h and norm.split("#")[0] == page_norm.split("#")[0]:
        return "anchor"
    parts = urlsplit(norm)
    host = parts.hostname or ""
    path = parts.path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(DOCUMENT_EXTS):
        return "document"
    if path.endswith(IMAGE_EXTS):
        return "image"
    if host_matches(host, SOCIAL_DOMAINS):
        return "social"
    ats = settings.safety.ats_domains
    if host_matches(host, ats):
        if is_pagination_link(text, rel, aria, h, page_url):
            return "pagination"
        return "ats_job" if looks_like_job_url(norm, ats) else "ats_board"
    if same_site(norm, page_url):
        if is_pagination_link(text, rel, aria, h, page_url):
            return "pagination"
        return "job_internal" if looks_like_job_url(norm, ats) else "internal"
    return "external"


def classify_frame(url: str, settings: Any) -> str:
    """Тип встроенного окна: inline, ats, ads, video, map, presentation, form, chat, social, other."""
    u = (url or "").strip().lower()
    if u in ("", "about:blank", "about:srcdoc"):
        return "inline"
    parts = urlsplit(u)
    host = parts.hostname or ""
    full = host + parts.path
    if host_matches(host, settings.safety.blocked_domains):
        return "ads"
    if host_matches(host, settings.safety.ats_domains):
        return "ats"
    if "docs.google.com/presentation" in full:
        return "presentation"
    if "docs.google.com/forms" in full:
        return "form"
    if host.startswith("www.google.") and parts.path.startswith("/maps") or "google.com/maps" in full:
        return "map"
    if "facebook.com/plugins" in full or "linkedin.com/embed" in full:
        return "social"
    for kind, domains in _FRAME_GROUPS:
        if host_matches(host, domains):
            return kind
    return "other"
