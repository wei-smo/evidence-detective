"""Small, deterministic benchmark scorer for recorded investigations.

The scorer deliberately measures retrieval, evidence use, answer verdict and
cost separately. It does not pretend that a search rank is a truth score.
"""
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from urllib.parse import urlsplit, urlunsplit


def _url(value):
    try:
        parts = urlsplit(str(value))
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
    except ValueError:
        return str(value)


def load_benchmark(path):
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"version": "1", "items": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("benchmark must contain an items list")
    return payload


def _mean(values):
    return round(mean(values), 4) if values else 0.0


def evaluate_report(item, report, *, model="unknown"):
    sources = report.get("sources") or []
    source_by_id = {str(s.get("id")): s for s in sources}
    gold = item.get("gold_sources") or []
    gold_urls = {_url(x.get("url")) for x in gold if x.get("url")}
    required_urls = {_url(x.get("url")) for x in gold if x.get("url") and x.get("required", True)}
    retrieved_urls = {_url(s.get("url")) for s in sources if s.get("url")}
    required_found = required_urls & retrieved_urls

    evidence = report.get("evidence") or []
    linked_gold = set()
    for entry in evidence:
        source = source_by_id.get(str(entry.get("source_id")))
        if source and _url(source.get("url")) in gold_urls:
            linked_gold.add(_url(source.get("url")))
    # A retrieved gold source can be cited through an evidence item even when
    # the report's claim-level evidence_ids list is incomplete.

    claims = report.get("claims") or []
    expected_claims = set(item.get("gold_claim_ids") or [])
    claim_ids = {str(c.get("id")) for c in claims}
    verdict_correct = float(bool(item.get("expected_verdict")) and report.get("verdict") == item.get("expected_verdict"))
    claim_coverage = len(expected_claims & claim_ids) / len(expected_claims) if expected_claims else 1.0
    search_calls = len(report.get("queries") or [])
    useful_calls = sum(1 for q in report.get("queries") or [] if q.get("status") == "ok")

    return {
        "task_id": item.get("task_id", ""),
        "model": model,
        "retrieval": {
            "required_source_recall": len(required_found) / len(required_urls) if required_urls else 1.0,
            "retrieved_source_precision": len(retrieved_urls & gold_urls) / len(retrieved_urls) if retrieved_urls else 0.0,
            "retrieved_sources": len(retrieved_urls),
            "required_sources": len(required_urls),
        },
        "evidence": {
            "gold_source_use_rate": len(linked_gold) / len(required_urls) if required_urls else 1.0,
            "linked_gold_sources": len(linked_gold),
        },
        "answer": {
            "verdict_correct": verdict_correct,
            "claim_coverage": claim_coverage,
        },
        "efficiency": {
            "search_calls": search_calls,
            "successful_search_calls": useful_calls,
            "useful_search_rate": useful_calls / search_calls if search_calls else 0.0,
            "elapsed_ms": report.get("elapsed_ms", 0) or 0,
        },
    }


def evaluate_suite(benchmark, runs):
    items = {str(x.get("task_id")): x for x in benchmark.get("items", [])}
    scores = []
    for run in runs:
        report = run.get("report") or {}
        task_id = run.get("task_id") or report.get("task_id")
        item = items.get(str(task_id)) if task_id else None
        if item is None and len(items) == len(runs) == 1:
            item = next(iter(items.values()))
        if item:
            scores.append(evaluate_report(item, report, model=run.get("model", "unknown")))

    by_model = defaultdict(list)
    for score in scores:
        by_model[score["model"]].append(score)

    def aggregate(rows):
        return {
            "runs": len(rows),
            "required_source_recall": _mean([x["retrieval"]["required_source_recall"] for x in rows]),
            "retrieved_source_precision": _mean([x["retrieval"]["retrieved_source_precision"] for x in rows]),
            "gold_source_use_rate": _mean([x["evidence"]["gold_source_use_rate"] for x in rows]),
            "verdict_accuracy": _mean([x["answer"]["verdict_correct"] for x in rows]),
            "claim_coverage": _mean([x["answer"]["claim_coverage"] for x in rows]),
            "avg_search_calls": _mean([x["efficiency"]["search_calls"] for x in rows]),
            "avg_elapsed_ms": _mean([x["efficiency"]["elapsed_ms"] for x in rows]),
        }

    return {"runs": len(scores), "by_model": {model: aggregate(rows) for model, rows in by_model.items()}, "details": scores}
