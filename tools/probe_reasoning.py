"""最小真实推理连通性探测：只发一次规划请求，不搜索、不写调查报告。

用法：
  python tools/probe_reasoning.py ["问题"] [--transport curl|python]

默认使用项目自身的 curl 通道（与线上请求链一致）。仅当当前进程拿不到系统 TLS
凭证（如受限令牌下出现 curl 35 / SEC_E_NO_CREDENTIALS）时，才用 --transport python
区分“服务不可用”与“本机通道不可用”；记录中明确写出实际使用的通道。

输出：artifacts/repair/reasoning-probe-<时间>.json，不含任何密钥。
失败同样落盘并返回非零退出码；未执行不能写成通过。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidence_detective import config  # noqa: E402
from evidence_detective.reasoning import PLAN_SYSTEM, Reasoner  # noqa: E402
from evidence_detective.transport import ServiceError, post_json  # noqa: E402

OUTPUT_DIR = ROOT / "artifacts" / "repair"


def plan_via_python(base_url, model, api_key, question, timeout=75):
    body = {"model": model, "messages": [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": json.dumps({"question": question, "as_of": None},
                                               ensure_ascii=False)}],
        "response_format": {"type": "json_object"}, "temperature": 0, "max_tokens": 3200,
        "stream": False}
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": "Bearer " + api_key},
        method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(json.loads(response.read().decode("utf-8"))["choices"][0]
                          ["message"]["content"])


def main():
    arguments = [a for a in sys.argv[1:] if not a.startswith("--")]
    transport = "curl"
    for index, value in enumerate(sys.argv[1:]):
        if value == "--transport" and index + 2 <= len(sys.argv[1:]):
            transport = sys.argv[1:][index + 1]
        elif value.startswith("--transport="):
            transport = value.split("=", 1)[1]
    question = arguments[0] if arguments else "张飞是谁"

    config.load_env()
    configuration = config.reasoning_config()
    started = time.monotonic()
    record = {
        "test": "reasoning plan request with the locally configured service",
        "question": question,
        "transport": transport,
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "search_invoked": False,
    }
    if not configuration:
        record.update({"status": "BLOCKED", "reason": "推理未启用或配置不完整"})
    else:
        record["model"] = configuration["model"]
        record["base_url_host"] = urlsplit(configuration["base_url"]).hostname
        try:
            if transport == "python":
                plan = plan_via_python(configuration["base_url"], configuration["model"],
                                       configuration["api_key"], question)
            else:
                plan = Reasoner(configuration["api_key"]).plan(question)
            claims, queries = plan.get("claims") or [], plan.get("queries") or []
            record.update({
                "status": "PASS",
                "question_type": plan.get("question_type"),
                "claim_count": len(claims),
                "query_count": len(queries),
                "query_purposes": sorted({q.get("purpose", "") for q in queries if isinstance(q, dict)}),
                "claims": claims,
                "queries": queries,
            })
        except ServiceError as exc:
            record.update({"status": "FAIL", "error": str(exc)})
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            record.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"reasoning-probe-{stamp}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k not in ("claims", "queries")},
                     ensure_ascii=False, indent=2))
    print(f"saved: {path}")
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
