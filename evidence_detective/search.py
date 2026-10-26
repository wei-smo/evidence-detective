import html
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .transport import post_json, ServiceError


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    value = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", " ", str(value or ""), flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def public_url(url):
    try:
        parts = urlsplit(str(url))
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        if parts.port not in (None, 80, 443):
            return ""
        host = parts.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host:
                return ""
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                           if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")])
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", query, ""))
    except (ValueError, TypeError):
        return ""


@dataclass
class SearchResult:
    title: str
    url: str
    passage: str = ""
    content: str = ""
    date: str = ""
    site: str = ""
    score: float = 0
    request_id: str = ""
    retrieved_at: str = ""


def parse_response(raw, retrieved_at=None):
    if not isinstance(raw, dict):
        raise ServiceError("搜索响应结构异常。")
    payload = raw.get("Response", raw)
    if not isinstance(payload, dict):
        raise ServiceError("搜索响应缺少有效 Response。")
    if payload.get("Error"):
        code = re.sub(r"[^A-Za-z0-9_.]", "", str(payload["Error"].get("Code", "Unknown")))[:80]
        raise ServiceError("元宝搜索返回错误：" + code)
    pages = payload.get("Pages", [])
    if not isinstance(pages, list):
        raise ServiceError("元宝搜索 Pages 不是列表。")
    results = []
    for page in pages:
        if isinstance(page, str):
            try:
                page = json.loads(page)
            except ValueError:
                continue
        if not isinstance(page, dict) or not public_url(page.get("url")):
            continue
        try:
            score = float(page.get("score") or 0)
        except (ValueError, TypeError):
            score = 0
        results.append(SearchResult(
            title=clean_text(page.get("title")), url=public_url(page["url"]),
            passage=clean_text(page.get("passage")), content=clean_text(page.get("content")),
            date=clean_text(page.get("date")), site=clean_text(page.get("site")), score=score,
            request_id=str(payload.get("RequestId") or ""), retrieved_at=retrieved_at or now()))
    return results


class YuanbaoSearch:
    ENDPOINT = "https://api.wsa.cloud.tencent.com/SearchPro"

    def __init__(self, api_key, audit=None, timeout=12):
        self.api_key, self.audit, self.timeout = api_key, audit, timeout

    def search(self, query, *, limit=10, site=None):
        body = {"Query": query}
        if site:
            body["Site"] = site
        started = time.monotonic()
        entry = {"provider": "yuanbao", "endpoint": self.ENDPOINT, "request": body, "started_at": now()}
        try:
            raw = post_json(self.ENDPOINT, body, self.api_key, self.timeout)
            entry["response"] = raw
            results = parse_response(raw, entry["started_at"])
            entry["parsed_count"] = len(results)
            return results[:max(1, min(limit, 10))]
        except Exception as exc:
            entry["error"] = str(exc) if isinstance(exc, ServiceError) else type(exc).__name__
            raise
        finally:
            entry["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            if self.audit:
                self.audit(entry)
