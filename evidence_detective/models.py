from dataclasses import asdict, dataclass, field


@dataclass
class Claim:
    id: str
    text: str
    entity: str = ""
    time_scope: str = "未知"
    conditions: str = "未知"
    verdict: str = "调查中"
    conclusion: str = ""
    evidence_ids: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    next_action: str = ""
    strength: str = "待核验"
    original_question: str = ""


@dataclass
class Source:
    id: str
    title: str
    url: str
    domain: str
    text: str
    passage: str
    date: str
    retrieved_at: str
    site: str = ""
    request_id: str = ""
    query_ids: list = field(default_factory=list)
    publisher_group: str = ""
    independence_group: str = ""
    is_repost: bool = False
    independence_note: str = "仅按域名与内容相似度分组，不保证真正独立"
    source_type: str = "公开网页 · 身份待核验"
    primary: bool = False
    digest: str = ""
    level: str = "搜索摘要/内容 · 未核验原文"
    url_status: str = "未检查"
    http_status: int = 0
    page_text: str = ""
    fetched_at: str = ""
    page_digest: str = ""
    final_url: str = ""
    search_observations: list = field(default_factory=list)


@dataclass
class Evidence:
    id: str
    claim_id: str
    source_id: str
    quote: str
    relation: str
    reason: str
    quote_verified: bool = True
    page_verified: bool = False
    support_degree: str = "UNKNOWN"
    quote_origin: str = "search_content"
    quote_start: int = 0
    quote_end: int = 0
    content_digest: str = ""


@dataclass
class Event:
    sequence: int
    kind: str
    message: str
    at: str
    elapsed_ms: int
    data: dict = field(default_factory=dict)


@dataclass
class Report:
    id: str
    question: str
    started_at: str
    verdict: str = "调查中"
    summary: str = ""
    claims: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    source_reviews: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    events: list = field(default_factory=list)
    queries: list = field(default_factory=list)
    limitations: list = field(default_factory=list)
    next_action: str = ""
    stop_reason: str = ""
    stop_code: str = ""
    schema_version: str = "2.0"
    scope: str = "公开、非敏感、可检索信息；不包含私人数据或后台实时库存。"
    independent_source_count: int = 0
    elapsed_ms: int = 0
    first_result_ms: int = 0
    analysis_mode: str = "规则降级 · 不作语义事实判断"
    question_type: str = "verification"
    answer_items: list = field(default_factory=list)
    service_failures: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)
