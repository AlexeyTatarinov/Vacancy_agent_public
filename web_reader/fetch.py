"""Быстрое чтение без браузера: проверка ссылок и скачивание PDF.

Каждая переадресация проверяется на безопасность, размер ограничен,
скачанные файлы никогда не запускаются.
"""
from __future__ import annotations

import html
import io
import re
import urllib.error
import urllib.request
from typing import Any

from .safety import check_url_sync

USER_AGENT = "web_reader/0.1 (personal page reader)"
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DESC_RE = re.compile(
    r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*content=[\"']([^\"']*)[\"']", re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, safety: Any):
        self._safety = safety

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        ok, why = check_url_sync(newurl, self._safety)
        if not ok:
            raise urllib.error.URLError(f"redirect blocked: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_get(url: str, safety: Any, *, max_bytes: int, timeout: float = 8.0,
             range_bytes: int | None = None) -> dict[str, Any]:
    """Скачивает не более max_bytes. Никогда не выбрасывает исключений: ошибка уходит в поле error."""
    out: dict[str, Any] = {"ok": False, "status": None, "final_url": url, "content_type": "",
                           "body": b"", "truncated": False, "error": None, "redirected": False}
    ok, why = check_url_sync(url, safety)
    if not ok:
        out["error"] = f"blocked: {why}"
        return out
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if range_bytes:
        headers["Range"] = f"bytes=0-{range_bytes - 1}"
    opener = urllib.request.build_opener(_SafeRedirect(safety))
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            out["status"] = resp.status
            out["final_url"] = resp.geturl()
            out["redirected"] = out["final_url"] != url
            out["content_type"] = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            limit = range_bytes or max_bytes
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes and not range_bytes:
                out["error"] = f"file too large ({declared} bytes)"
                out["truncated"] = True
                return out
            body = resp.read(limit + 1)
            if len(body) > limit:
                out["truncated"] = True
                body = body[:limit]
            out["body"] = body
            out["ok"] = 200 <= resp.status < 300 or resp.status == 206
            charset_match = re.search(r"charset=([\w-]+)", resp.headers.get("Content-Type") or "", re.I)
            out["charset"] = charset_match.group(1) if charset_match else None
    except urllib.error.HTTPError as exc:
        out["status"] = exc.code
        out["error"] = f"HTTP {exc.code}"
    except Exception as exc:  # сеть, таймаут, запрещённая переадресация
        out["error"] = str(exc)[:200]
    return out


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def probe_link(url: str, safety: Any, timeout: float = 6.0) -> dict[str, Any]:
    """Быстрая проверка ссылки без браузера: куда ведёт, какой заголовок (по статичному HTML)."""
    res = safe_get(url, safety, max_bytes=65536, timeout=timeout, range_bytes=65536)
    info: dict[str, Any] = {
        "ok": res["ok"], "status": res["status"], "final_url": res["final_url"],
        "redirected": res["redirected"], "content_type": res["content_type"],
        "title": "", "h1": "", "description": "", "error": res["error"],
    }
    if res["ok"] and ("html" in res["content_type"] or not res["content_type"]):
        enc = res.get("charset") or "utf-8"
        try:
            text = res["body"].decode(enc, errors="replace")
        except LookupError:
            text = res["body"].decode("utf-8", errors="replace")
        m = _TITLE_RE.search(text)
        info["title"] = _clean(m.group(1))[:200] if m else ""
        m = _H1_RE.search(text)
        info["h1"] = _clean(m.group(1))[:200] if m else ""
        m = _DESC_RE.search(text)
        info["description"] = html.unescape(m.group(1)).strip()[:300] if m else ""
    return info


def read_pdf(url: str, safety: Any, *, max_bytes: int, max_pages: int, timeout: float = 15.0) -> dict[str, Any]:
    """Скачивает PDF и извлекает текст. Возвращает словарь с text, pages, notes."""
    res = safe_get(url, safety, max_bytes=max_bytes, timeout=timeout)
    info: dict[str, Any] = {
        "ok": False, "final_url": res["final_url"], "status": res["status"],
        "pages": 0, "pages_read": 0, "text": "", "notes": [], "error": res["error"],
    }
    if not res["ok"]:
        return info
    body: bytes = res["body"]
    if res["truncated"]:
        info["notes"].append("file larger than limit, only the beginning was downloaded")
    if not body.lstrip()[:5].startswith(b"%PDF"):
        info["error"] = "not a PDF file"
        return info
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                info["error"] = "PDF is password-protected"
                return info
        info["pages"] = len(reader.pages)
        chunks: list[str] = []
        for index, page in enumerate(reader.pages):
            if index >= max_pages:
                info["notes"].append(f"only the first {max_pages} pages were read")
                break
            chunks.append(page.extract_text() or "")
            info["pages_read"] += 1
        info["text"] = "\n\n".join(c.strip() for c in chunks if c.strip())
        if not info["text"]:
            info["notes"].append("no text layer found (scanned or image-only PDF); text recognition was not performed")
        info["ok"] = True
    except Exception as exc:
        info["error"] = f"PDF parse error: {str(exc)[:150]}"
    return info
