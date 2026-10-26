import argparse
import hmac
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import ROOT, load_env, search_key, model_key, reasoning_config
from .engine import EvidenceDetective
from .reasoning import Reasoner
from .search import YuanbaoSearch
from .storage import Store, DATA, markdown
from .reviews import add_review, review_conclusion

JOBS = {}
JOBS_LOCK = threading.Lock()
CAPACITY = threading.BoundedSemaphore(2)
ACCESS_TOKEN = ""
REVIEW_LOCK = threading.Lock()


class Job:
    def __init__(self, question):
        self.id = uuid.uuid4().hex[:16]
        self.question = question
        self.events = []
        self.report = None
        self.done = False
        self.cancelled = threading.Event()
        self.condition = threading.Condition()
        self.store = Store(self.id)

    def emit(self, event):
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def run(self):
        try:
            search = YuanbaoSearch(search_key(), self.store.audit)
            reasoner = Reasoner(model_key(), self.store.audit) if model_key() else None
            detective = EvidenceDetective(search, reasoner, emit=self.emit, verify=True, cancelled=self.cancelled)
            report = detective.investigate(self.question, investigation_id=self.id)
            self.report = asdict(report)
            self.store.save(self.report)
        except Exception as exc:
            self.emit({"kind": "error", "message": "调查异常中止，已保存本地诊断信息；请稍后重试。", "data": {"error_type": type(exc).__name__}})
            self.store.audit({"provider": "runtime", "error_type": type(exc).__name__})
        finally:
            with self.condition:
                self.done = True
                self.condition.notify_all()
            CAPACITY.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # No queries, bearer tokens or request bodies in the terminal log.
        pass

    def respond(self, body, status=200, content_type="application/json; charset=utf-8", filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        # Local-only instances must reject DNS-rebinding Host headers.
        if not ACCESS_TOKEN:
            try:
                host = urlsplit('http://' + self.headers.get('Host', '')).hostname
            except ValueError:
                host = None
            if host not in ('127.0.0.1', 'localhost', '::1'):
                self.respond({'error': '本地服务只接受回环主机名。'}, 403)
                return False
        if ACCESS_TOKEN and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + ACCESS_TOKEN):
            self.respond({"error": "请输入访问口令。"}, 401)
            return False
        return True

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.respond({"error": "不允许跨站发起调查。"}, 403)
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/") and not self.authorized():
            return
        if path == "/api/health":
            configuration = reasoning_config()
            return self.respond({"search_configured": bool(search_key()), "reasoning_configured": bool(model_key()),
                                 "reasoning_mode": "enabled" if model_key() else "disabled",
                                 "reasoning_model": configuration["model"] if configuration else None,
                                 "event_schema_version": "2.0", "investigation_time_budget_seconds": 300,
                                 "provider": "元宝 SearchPro", "version": "1.1.0"})
        if path == "/api/history":
            reports = []
            if DATA.exists():
                for file in sorted(DATA.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
                    try:
                        item = json.loads(file.read_text(encoding="utf-8"))
                        history_item = {k: item[k] for k in ("id", "question", "verdict", "started_at", "elapsed_ms")}
                        history_item.update({'stop_code': item.get('stop_code'),
                            'question_type': item.get('question_type'), 'answer_count': len(item.get('answer_items') or []),
                            'service_incomplete': item.get('stop_code') in {'service_incomplete', 'service_unavailable'}
                                or any(not f.get('resolved') for f in item.get('service_failures', []))})
                        reports.append(history_item)
                    except (ValueError, KeyError, OSError):
                        continue
            return self.respond(reports)
        match = re.fullmatch(r"/api/investigations/([a-f0-9]{16})(?:/(events|export))?", path)
        if match:
            identifier, action = match.groups()
            with JOBS_LOCK:
                job = JOBS.get(identifier)
            if action == "events" and job:
                return self.stream(job)
            report = job.report if job else None
            if not report:
                try:
                    report = json.loads((DATA / identifier / "report.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return self.respond({"error": "报告尚未完成或不存在。"}, 404)
            if action == "export":
                return self.respond(markdown(report), content_type="text/markdown; charset=utf-8", filename=f"evidence-{identifier}.md")
            return self.respond(report)
        assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
        role_files = {f'/assets/roles/{name}.png' for name in ('compass', 'blue', 'fox', 'gold', 'violet', 'ink')}
        if path in role_files:
            portrait = ROOT / 'static' / path.lstrip('/')
            if portrait.is_file():
                return self.respond(portrait.read_bytes(), content_type='image/png')
        font_path = '/assets/fonts/fusion-pixel-12px-proportional-zh_hans.otf.woff2'
        if path == font_path:
            font = ROOT / 'static' / font_path.lstrip('/')
            if font.is_file():
                return self.respond(font.read_bytes(), content_type='font/woff2')
        if path in assets:
            file = ROOT / "static" / assets[path]
            if file.exists():
                mime = mimetypes.guess_type(file.name)[0] or "text/plain"
                return self.respond(file.read_bytes(), content_type=mime + "; charset=utf-8")
        self.respond({"error": "未找到页面。"}, 404)

    def stream(self, job):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        cursor = 0
        try:
            while True:
                with job.condition:
                    if cursor == len(job.events) and not job.done:
                        job.condition.wait(timeout=5)
                    batch = list(job.events[cursor:])
                    done, report = job.done, job.report
                for event in batch:
                    self.wfile.write(("event: progress\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    cursor += 1
                if done:
                    self.wfile.write(("event: complete\ndata: " + json.dumps({"report": report}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    break
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def do_POST(self):
        if not self.authorized() or not self.valid_origin():
            return
        path = urlsplit(self.path).path
        review_match = re.fullmatch(r"/api/investigations/([a-f0-9]{16})/(reviews|conclusions)", path)
        if review_match:
            identifier, action = review_match.groups()
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16000:
                    raise ValueError('复核请求过大或为空。')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('复核内容必须为对象。')
                with REVIEW_LOCK:
                    file = DATA / identifier / 'report.json'
                    report = json.loads(file.read_text(encoding='utf-8'))
                    updated = (add_review if action == 'reviews' else review_conclusion)(report, payload)
                    store = Store(identifier)
                    store.audit({'provider': 'human_review', 'action': action,
                                 'entry': updated['reviews' if action == 'reviews' else 'manual_conclusions'][-1]})
                    store.save(updated)
                    with JOBS_LOCK:
                        job = JOBS.get(identifier)
                    if job:
                        job.report = updated
                return self.respond(updated)
            except FileNotFoundError:
                return self.respond({'error': '报告尚未完成或不存在。'}, 404)
            except (ValueError, TypeError, KeyError) as exc:
                return self.respond({'error': str(exc)}, 400)
        cancel = re.fullmatch(r"/api/investigations/([a-f0-9]{16})/cancel", path)
        if cancel:
            size = int(self.headers.get('Content-Length', '0'))
            if size:
                self.close_connection = True
            with JOBS_LOCK:
                job = JOBS.get(cancel.group(1))
            if job:
                job.cancelled.set()
            return self.respond({"cancelled": bool(job)})
        if path != "/api/investigations":
            return self.respond({"error": "未找到接口。"}, 404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 10000:
                raise ValueError()
            payload = json.loads(self.rfile.read(size))
            question = payload.get("question", "")
            if not isinstance(question, str) or not 4 <= len(question.strip()) <= 600:
                raise ValueError()
        except (ValueError, AttributeError):
            self.close_connection = True
            return self.respond({"error": "请输入 4～600 字的公开信息核查问题。"}, 400)
        if not search_key():
            return self.respond({"error": "元宝服务 API Key 未配置，请在服务端 .env.local 中设置。"}, 503)
        if not CAPACITY.acquire(blocking=False):
            return self.respond({"error": "已有两项调查正在进行，请稍后重试。"}, 429)
        job = Job(question.strip())
        with JOBS_LOCK:
            for key in list(JOBS):
                if len(JOBS) <= 30:
                    break
                if JOBS[key].done:
                    del JOBS[key]
            JOBS[job.id] = job
        threading.Thread(target=job.run, daemon=True).start()
        self.respond({"id": job.id}, 202)


def main():
    global ACCESS_TOKEN
    load_env()
    parser = argparse.ArgumentParser(description="Evidence Detective local web server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "")
    if args.host not in ("localhost", "127.0.0.1", "::1") and len(ACCESS_TOKEN) < 16:
        parser.error("对外监听需要设置至少 16 字符的 APP_ACCESS_TOKEN，并在反向代理启用 HTTPS。")
    print(f"Evidence Detective: http://{args.host}:{args.port}", flush=True)
    print(f"Yuanbao configured: {bool(search_key())}; reasoning configured: {bool(model_key())}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
