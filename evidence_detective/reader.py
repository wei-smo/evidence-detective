"""Bounded GET of discovered URLs only. Pin validated DNS, revalidate redirects."""
import concurrent.futures
import http.client
import hashlib
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin

from .search import clean_text, public_url, now


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_public(url, timeout=3, redirects=0):
    if not public_url(url) or redirects > 2:
        return {"url_status": "安全策略拒绝", "http_status": 0, "page_text": ""}
    try:
        parts = urlsplit(url)
        addresses = {a[4][0] for a in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            return {"url_status": "安全策略拒绝", "http_status": 0, "page_text": ""}
        address = sorted(addresses)[0]
        if parts.scheme == "https":
            conn = PinnedHTTPS(parts.hostname, address, timeout)
        else:
            conn = http.client.HTTPConnection(address, parts.port or 80, timeout=timeout)
        try:
            conn.request("GET", (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                         headers={"Host": parts.hostname, "User-Agent": "EvidenceDetective/1.0 (source-verification)", "Accept": "text/html,text/plain"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                return fetch_public(urljoin(url, response.getheader("Location", "")), timeout, redirects + 1)
            data = response.read(400000)
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = clean_text(data.decode(charset, errors="replace"))
            except LookupError:
                text = clean_text(data.decode("utf-8", errors="replace"))
            is_text = "text/" in response.getheader("Content-Type", "")
            return {"url_status": "可访问" if 200 <= response.status < 300 else "访问受限/失效",
                    "http_status": response.status, "page_text": text if is_text and response.status == 200 else "",
                    "final_url": url}
        finally:
            conn.close()
    except Exception:
        return {"url_status": "连接失败/超时 · 不代表网页不存在", "http_status": 0, "page_text": ""}


def verify_sources(sources, emit=None, *, limit=4, timeout=3, audit=None):
    candidates = sorted((s for s in sources if not s.fetched_at), key=lambda s: not s.primary)[:limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_public, s.url, timeout): s for s in candidates}
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            result = future.result()
            for key, value in result.items():
                setattr(source, key, value)
            source.fetched_at = now()
            source.page_digest = hashlib.sha256(source.page_text.encode()).hexdigest()
            if source.page_text:
                source.level = "已读取网页 · 引用是否匹配另行检查"
            if emit:
                emit("source_check", f"{source.id} · {source.url_status}", {"source_id": source.id,
                    "http_status": source.http_status, "url_status": source.url_status, "page_digest": source.page_digest,
                    "final_url": source.final_url, "fetched_at": source.fetched_at})
            if audit:
                audit({"provider": "source_reader", "source_id": source.id, "url": source.url,
                       "final_url": source.final_url, "http_status": source.http_status, "fetched_at": source.fetched_at,
                       "page_text": source.page_text, "page_digest": source.page_digest})
