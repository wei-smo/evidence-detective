"""Keep secrets in curl stdin, never in argv or log files.

curl is the primary channel because the key never reaches argv. Some Windows
environments (restricted service tokens, locked-down schannel policy) deny the
TLS credential handshake with ``curl: (35) ... SEC_E_NO_CREDENTIALS``; the same
request works from Python's own TLS stack. The fallback below keeps the request
byte-identical and only re-sends it when curl never got an HTTP response.
"""
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request


class ServiceError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ServiceError(f"服务返回 HTTP {code}；不跟随重定向。") from None


def _python_post(url, body, api_key, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + api_key,
                 "Content-Type": "application/json; charset=utf-8"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(5_000_001).decode("utf-8", errors="replace")
    except ServiceError:
        raise
    except urllib.error.HTTPError as exc:
        raise ServiceError(f"服务返回 HTTP {exc.code}；请检查额度、鉴权和可用性。") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceError(f"网络请求失败（{type(exc).__name__}）。") from None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ServiceError("服务返回非 JSON 内容。") from None


def post_json(url, body, api_key, timeout=20):
    started = time.monotonic()
    executable = shutil.which("curl.exe") or shutil.which("curl")
    if not executable:
        raise ServiceError("缺少 curl；请安装后重试。")
    if not api_key or any(c in api_key for c in "\r\n"):
        raise ServiceError("缺少有效的服务端密钥。")
    lines = ["url = " + json.dumps(url), 'request = "POST"',
             "header = " + json.dumps("Authorization: Bearer " + api_key),
             'header = "Content-Type: application/json; charset=utf-8"',
             "data = " + json.dumps(json.dumps(body, ensure_ascii=False), ensure_ascii=False)]
    try:
        response = subprocess.run(
            [executable, "--silent", "--show-error", "--max-time", str(timeout),
             "--connect-timeout", "8", "--max-filesize", "5000000", "--write-out",
             "\n__HTTP_STATUS__%{http_code}", "--config", "-"],
            input="\n".join(lines).encode("utf-8"), capture_output=True,
            timeout=timeout + 2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ServiceError("服务请求超时，未生成虚构结果。") from None
    raw, _, status = response.stdout.decode("utf-8", errors="replace").rpartition("\n__HTTP_STATUS__")
    if response.returncode:
        # Only a TLS handshake failure with no HTTP response can use the other
        # local TLS stack. A timeout/partial reply may already have been billed.
        if response.returncode != 35 or status not in ('', '000') or raw.strip():
            raise ServiceError(f"网络请求失败（curl {response.returncode}）；未自动重发请求。")
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise ServiceError('服务请求超时；未自动重发请求。')
        try:
            return _python_post(url, body, api_key, remaining)
        except ServiceError as fallback:
            raise ServiceError(
                f"网络请求失败（curl {response.returncode}；备用通道：{fallback}）。") from None
    if status != "200":
        raise ServiceError(f"服务返回 HTTP {status}；请检查额度、鉴权和可用性。")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ServiceError("服务返回非 JSON 内容。") from None
