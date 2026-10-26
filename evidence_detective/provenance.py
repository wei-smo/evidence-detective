import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .models import Source, Evidence
from .search import clean_text, now, public_url


def publisher(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    labels = host.split(".")
    suffix = ".".join(labels[-2:])
    group = ".".join(labels[-3:]) if suffix in {"com.cn", "org.cn", "gov.cn", "co.uk", "com.au"} else suffix
    if host == "github.com":
        group += "/" + parts.path.strip("/").split("/")[0].lower()
    return group


def primary_for(question, url):
    host = (urlsplit(url).hostname or "").lower()
    question = question.lower()
    owners = {"python": ("python.org",), "sqlite": ("sqlite.org",),
              "node": ("nodejs.org",), "electron": ("electronjs.org",),
              "deerflow": ("deerflow.tech",), "deer-flow": ("deerflow.tech",),
              "1024": ("1024.waytoagi.com",), "元宝": ("cloud.tencent.com", "cloud.tencent.cn")}
    return any(name in question and any(host == d or host.endswith("." + d) for d in domains)
               for name, domains in owners.items())


def normalize(results, sources, query_id, question):
    existing = {s.url: s for s in sources}
    added = []
    for result in results:
        url = public_url(result.url)
        if not url:
            continue
        text = clean_text(result.content or result.passage)
        passage = clean_text(result.passage)
        observation = {"query_id": query_id, "text": text, "passage": passage,
                       "title": clean_text(result.title), "date": result.date or "未知",
                       "observed_at": result.retrieved_at or now()}
        if url in existing:
            # Same URL seen by another query: keep the new passage as its own
            # observation instead of overwriting the first one (traceability).
            source = existing[url]
            if query_id not in source.query_ids:
                source.query_ids.append(query_id)
            if not any(o['query_id'] == query_id and o['text'] == text and o['passage'] == passage
                       for o in source.search_observations):
                source.search_observations.append(observation)
            for part in (text, passage):
                if part and part not in source.text:
                    source.text = (source.text + "\n" + part).strip()
            source.digest = hashlib.sha256(source.text.encode()).hexdigest()
            continue
        if passage and passage not in text:
            text = (text + '\n' + passage).strip()
        domain = urlsplit(url).hostname or ""
        source = Source(id=f"S{len(sources) + 1}", title=clean_text(result.title), url=url,
                        domain=domain, text=text, passage=passage,
                        date=result.date or "未知", retrieved_at=result.retrieved_at or now(),
                        site=result.site, request_id=result.request_id, query_ids=[query_id],
                        publisher_group=publisher(url), independence_group=publisher(url),
                        digest=hashlib.sha256(text.encode()).hexdigest(), primary=primary_for(question, url),
                        search_observations=[observation])
        if source.primary:
            source.source_type = "相关项目一手域名"
        elif any(x in domain for x in ("zhihu.com", "csdn.net", "reddit.com", "cnblogs.com")):
            source.source_type = "社区/个人发布"
        for previous in sources:
            a = re.sub(r"\W", "", text[:2000]).lower()
            b = re.sub(r"\W", "", previous.text[:2000]).lower()
            attribution = previous.domain in text and bool(re.search(r"来源|转载|转自|source|via", text, re.I))
            similar = min(len(a), len(b)) >= 15 and (a == b or SequenceMatcher(None, a, b).ratio() >= .85)
            if attribution or similar:
                source.is_repost = True
                source.independence_group = previous.independence_group
                source.independence_note = f"与 {previous.id} 存在出处标注或高度相似内容，保守合并计数"
                break
        sources.append(source)
        existing[url] = source
        added.append(source)
    return added


def regroup_sources(sources):
    """Two URLs resolving to one final URL, or serving identical body, count once."""
    anchors = {}
    parents = {s.independence_group: s.independence_group for s in sources}
    def root(group):
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group
    merged = []
    for source in sources:
        if not source.fetched_at:
            continue
        keys = []
        if source.final_url:
            keys.append("url:" + source.final_url)
        if source.page_text:
            digest = source.page_digest or hashlib.sha256(source.page_text.encode()).hexdigest()
            keys.append("page:" + digest)
        matches = [anchors[key] for key in keys if key in anchors]
        for match in matches:
            if match is source:
                continue
            group, target = root(source.independence_group), root(match.independence_group)
            if group != target:
                parents[group] = target
            source.is_repost = True
            source.independence_note = f"与 {match.id} 同一最终地址或同一正文，保守合并计数"
        for key in keys:
            anchors.setdefault(key, source)
    # Resolve all members after every link is known: a later page can connect
    # two groups that were previously separate (same URL and same body links).
    for source in sources:
        group = root(source.independence_group)
        if group != source.independence_group:
            merged.append(source.id)
            source.independence_group = group
            source.is_repost = True
            if not source.independence_note:
                source.independence_note = '与已有来源存在同地址/同正文的传递关联，保守合并计数'
    return merged


def bind_evidence(proposals, claims, sources):
    """Never let the model invent a source, a claim, or a quotation."""
    by_source, by_claim = {s.id: s for s in sources}, {c.id: c for c in claims}
    evidence, rejected, seen = [], 0, set()
    if not isinstance(proposals, list):
        return [], 1
    for item in proposals[:30]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        source = by_source.get(item.get("source_id"))
        claim = by_claim.get(item.get("claim_id"))
        quote = item.get("quote", "")
        relation = item.get("relation")
        degree = item.get("support_degree", "UNKNOWN")
        origin = item.get("quote_origin", "search_content")
        content = (source.page_text if origin == "page_text" else source.text) if source else ""
        key = (item.get("source_id"), item.get("claim_id"), quote if isinstance(quote, str) else "")
        if (not source or not claim or not isinstance(quote, str) or not 10 <= len(quote) <= 600
                or relation not in ("support", "counter", "context")
                or origin not in ("search_content", "page_text")
                or degree not in ("FULL", "PARTIAL", "CONTRADICT", "NONE", "UNKNOWN")
                or (degree in ("FULL", "PARTIAL") and relation != "support")
                or (degree == "CONTRADICT" and relation != "counter")
                or (degree in ("NONE", "UNKNOWN") and relation != "context" and "support_degree" in item)
                or quote not in content or key in seen):
            rejected += 1
            continue
        seen.add(key)
        evidence.append(Evidence(id=f"E{len(evidence)+1}", source_id=source.id, claim_id=claim.id,
                                 quote=quote, relation=relation, reason=clean_text(item.get("reason"))[:500],
                                 page_verified=bool(source.page_text and quote in source.page_text),
                                 support_degree=degree, quote_origin=origin,
                                 quote_start=content.index(quote), quote_end=content.index(quote) + len(quote),
                                 content_digest=hashlib.sha256(content.encode()).hexdigest()))
    return evidence, rejected


def bind_source_reviews(proposals, claims, sources):
    """One conservative applicability review per claim/source; missing rows stay unknown."""
    valid = {}
    pairs = {(c.id, s.id) for c in claims for s in sources}
    for item in proposals if isinstance(proposals, list) else []:
        if not isinstance(item, dict):
            continue
        key = (item.get("claim_id"), item.get("source_id"))
        if key not in pairs or key in valid:
            continue
        row = {"claim_id": key[0], "source_id": key[1]}
        for field in ("entity_match", "time_match", "conditions_match"):
            row[field] = item.get(field) if item.get(field) in ("match", "mismatch", "unknown") else "unknown"
        state = item.get("applicability", "unknown")
        row["applicability"] = state if state in ("applicable", "inapplicable", "unknown") else "unknown"
        if "mismatch" in (row[f] for f in ("entity_match", "time_match", "conditions_match")):
            row["applicability"] = "inapplicable"
        elif "unknown" in (row[f] for f in ("entity_match", "time_match", "conditions_match")):
            row["applicability"] = "unknown" if row["applicability"] == "applicable" else row["applicability"]
        row["is_primary"] = item.get("is_primary") is True
        row["reason"] = clean_text(item.get("reason"))[:700]
        limitations = item.get("limitations", [])
        row["limitations"] = [clean_text(x)[:300] for x in limitations if isinstance(x, str)][:5] if isinstance(limitations, list) else []
        valid[key] = row
    rows = [valid.get((c.id, s.id), {"claim_id": c.id, "source_id": s.id,
            "applicability": "unknown", "entity_match": "unknown", "time_match": "unknown",
            "conditions_match": "unknown", "is_primary": False, "reason": "未取得有效来源适用性判断", "limitations": []})
            for s in sources for c in claims]
    by_source = {s.id: s for s in sources}
    for row in rows:
        source = by_source[row["source_id"]]
        row.update({"source_digest": source.digest, "page_digest": source.page_digest, "reviewed_at": now()})
    return rows
