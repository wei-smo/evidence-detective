# 调查事件协议 v2.0

后端入口：`POST /api/investigations`，JSON 为 `{"question":"待核查问题"}`；返回 HTTP 202 与 `{"id":"调查编号"}`。
随后使用 `GET /api/investigations/<id>/events` 接收 SSE。前端现有五张工作流卡片已接入这些字段。

## 公共字段与 SSE

`event: progress` 的 `data` 是以下对象：

```json
{
  "sequence": 7,
  "kind": "source_review",
  "message": "来源适用性审查已返回；未知项保留缺口。",
  "at": "2026-10-25T04:00:00+00:00",
  "elapsed_ms": 13000,
  "data": {
    "schema_version": "2.0",
    "investigation_id": "0123456789abcdef",
    "round": 1,
    "status": "completed",
    "reviews": []
  }
}
```

- `sequence`：同一调查递增的整数；用于排序或去重。
- `kind`：事件名称；`message` 仅用于显示，不要靠它判断阶段。
- `at`：带时区 ISO 时间；`elapsed_ms`：从调查开始计时的真实毫秒数。
- `data.schema_version` 与 `data.investigation_id`：协议版本和所属调查。
- `round`：直接检索为 1、反证检索为 2，缺口补查从 3 递增。
- `status=completed|degraded`：审查是否取得有效结构；completed 不代表结论正确或证据充分，unknown 行仍可存在。

终止时先发送 `progress/stopped`，然后 `event: complete`，其 `data` 为 `{"report":完整报告}`。
异常退出可能为 `{"report":null}`；必须显示失败，不能显示调查通过。空行分隔事件，注释行是 keepalive。
当前服务每次连接从第 1 条重放，无 Last-Event-ID 增量恢复；重连需按 sequence 去重。带访问口令时使用 fetch 读取流并发送 Authorization。

## 事件及业务字段

| kind | data 中的业务字段 | 前端用途 |
| --- | --- | --- |
| started | 公共字段 | 建档 |
| plan | claims[], scope | 渲染主张、对象、时间、条件 |
| search_start | id, query, purpose, reason, claim_ids[], round, status | 显示实际查询；purpose 为 direct/counter/followup |
| search_result | 同查询字段、result_count, new_sources, sources[] | 合并真实来源，更新查询计数；同一查询计数一次 |
| counter | adaptive | 进入反证轮；来自模型规划时 adaptive=true；不代表反证请求已成功 |
| verification | round | 读取已检索到的网页 |
| source_check | source_id, http_status, url_status, page_digest, final_url, fetched_at | 更新链接与正文快照状态；可访问不代表内容真实 |
| source_review_start | round, source_ids[] | 显示正在审查来源 |
| source_review | round, status, reviews[] | 以 claim_id + source_id 为键覆盖对应适用性判断；其余行保留 |
| evidence_review_start | round, source_ids[] | 显示正在判断支持度 |
| evidence_review | round, status, evidence[], claims[], rejected | 用该快照替换当前证据列表及主张状态，不要累加 |
| validation | round, rejected | 被程序拒绝的错误、虚构、非连续或重复引用数量 |
| followup | round, gaps[], conflicts[], queries[] | 显示补查针对的主张、缺失条件和拟执行查询；实际发起以 search_start 为准 |
| error | 推理错误：stage, diagnostic；搜索错误：查询字段、error | 呈现局部失败，等待 stopped；不把局部错误当成调查已完成 |
| stopped | stop_code, verdict, gaps[], search_count, elapsed_ms | 显示结论与为什么停止；范围拒绝分支没有搜索计数 |

五张卡片可以反复进入：核验发现缺口后回到补查，再进入核验。不要假设阶段只向前推进。

### 本轮增量字段（旧记录可缺省）

- Report.question_type：`verification|open_question`；Report.answer_items：`[{claim_id,text,evidence_ids[],source_ids[]}]`，仅收录通过原有门槛的事实，不能代表整个调查闭合。
- Claim.original_question：绑定用户原问题，不能因改写增加未要求的条件。
- Report.service_failures：`[{stage,round_elapsed_ms,diagnostic,resolved}]`；阶段失败与材料不足分开。后续同阶段成功将 resolved 标为 true。
- `candidate_claims_start` 表示开放问答开始整理候选；`candidate_claims` 带 `round,status,accepted_count,claims[],question_type`。候选仅通过来源片段存在性检查，尚须两种独立审查，不等同答案。
- `plan.data.question_type` 区分检索目标与待验证断言；`validation.data.stage=plan` 表示已拦截已知的范围扩张，回到原问题核验，不宣称能规则识别所有语义漂移。
- `stop_code=service_incomplete`：已有事实可以展示，但推理服务仍有未完成步骤。历史记录无新字段时按原协议回放。
- `search_start/search_result` 按 purpose 路由：counter→反证，direct/followup→寻找证据。禁止按轮号归类所有后续查询。

## 关键结构

主张 Claim：`id, text, entity, time_scope, conditions, verdict, conclusion, evidence_ids[], missing[], next_action, strength`。
判定枚举为 `支持|反驳|部分成立|证据不足|未知`，运行中可为 `调查中`。
当前保守策略不会用单个 PARTIAL 片段把原子主张判为支持；总体报告允许多个不同主张形成“部分成立”。

来源适用性 SourceReview：

```json
{
  "source_id": "S1", "claim_id": "C1",
  "applicability": "applicable",
  "entity_match": "match", "time_match": "match", "conditions_match": "match",
  "is_primary": true, "reason": "来源身份与对象、版本适用范围的解释", "limitations": [],
  "source_digest": "搜索文字SHA256", "page_digest": "网页文字SHA256", "reviewed_at": "ISO时间"
}
```

`applicability=applicable|inapplicable|unknown`；三个 match 字段为 `match|mismatch|unknown`。有 mismatch 时程序强制不适用；有 unknown 时不能提升为适用。
`is_primary` 是模型根据所给材料作出的身份判断，不是签名身份认证。来源域名启发式 `Source.primary` 只是候选排序信号，不能覆盖不适用判断。
来源判断与支持度判断使用两次独立模型调用，后者只能使用前者的适用性结果。
支持度描述文字蕴含，不因来源未知而自动改成 UNKNOWN；最终结论由程序另行结合来源适用性筛选。

证据 Evidence：

```json
{
  "id":"E1", "claim_id":"C1", "source_id":"S1",
  "quote":"本次提供给模型的真实连续片段",
  "relation":"support", "support_degree":"FULL", "reason":"片段如何覆盖主张及约束",
  "quote_verified":true, "page_verified":true,
  "quote_origin":"page_text", "quote_start":12, "quote_end":32,
  "content_digest":"对应完整规范化文本的SHA256"
}
```

| support_degree | 含义 | relation |
| --- | --- | --- |
| FULL | 完整支持原子主张及其约束 | support |
| PARTIAL | 只支持一部分，关键条件缺失 | support |
| CONTRADICT | 直接矛盾或反例 | counter |
| NONE | 相关背景但无蕴含 | context |
| UNKNOWN | 支持关系无法判定 | context |

`quote_origin=page_text` 对应 Source.page_text；`search_content` 对应 Source.text。
位置为 Python Unicode 码点、左闭右开；JS 定位请用 `Array.from(content).slice(start,end).join('')`，不能直接按 UTF-16 偏移截取。
`content_digest` 针对完整规范化文本，不是 HTML 文件；Source.digest 针对搜索文字，Source.page_digest 针对本次读取的正文文字。
`page_verified` 只表示同一片段在读取的正文复现，不代表逻辑正确。
程序同时检查来源/主张存在、片段在提供给模型的上下文中，以及逐字匹配与位置。
搜索摘要允许保留为有限证据；确定结论还须至少有原文匹配的完整支持/反例与适用性门槛。

Claim/Source 的 ID 在调查内稳定；同一来源、主张、引用通道与连续片段的 Evidence ID 在各轮稳定。旧证据从当前快照移除时 ID 不复用，出现编号间隔是正常情况。
`gaps[]` 为 `{"claim_id":"C1","missing":["缺失条件"],"verdict":"证据不足"}`。

## 停止码

| stop_code | 含义 |
| --- | --- |
| sufficient | 直接与反证检索完成，所有主张达到门槛，无未关闭缺口或冲突 |
| search_budget | 达到默认 6 次搜索上限 |
| time_budget | 进入总时间预算尾部；默认调度预算 300 秒 |
| no_new_evidence | 缺口查询重复或未返回新来源；不表示主张已被证伪 |
| service_unavailable | 搜索请求均未完成 |
| cancelled | 用户取消，保留已得材料 |
| out_of_scope | 范围拒绝，未执行搜索 |
| incomplete | 其余尚未闭合状态 |

网页每轮最多读取 4 个、全程最多 8 个；模型每轮最多审查 4 个来源，搜索文字最多 2200 字符、网页文字最多 8000 字符。
这些是明确的上下文与预算边界，不是所有搜索结果已审查的承诺。进行中的网络请求不保证立即取消，DNS/重定向等可能使实际时间略超调度预算。
历史 v1 报告可能没有新字段；前端用空列表/未分级降级显示，不能为旧报告补造模型结果。

## 最终报告与审计

`GET /api/investigations/<id>` 返回 `schema_version, id, question, claims[], sources[], source_reviews[], evidence[], conflicts[], events[], queries[], limitations[], verdict, summary, stop_code, stop_reason` 等完整报告字段。
Markdown 导出：`GET /api/investigations/<id>/export`。历史：`GET /api/history`。取消：`POST /api/investigations/<id>/cancel`。
`GET /api/health` 的 `reasoning_model` 只代表配置模型，连通性以真实调用验收记录为准；该接口不返回密钥。
本地 `data/investigations/<id>/audit.jsonl` 保存元宝、两阶段模型与网页读取的原始记录，不是静态公开资源。
