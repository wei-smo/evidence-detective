# 证据侦探 Evidence Detective

把问题查到有原文、有判断依据、有明确停止原因。

## 从 GitHub 部署

本仓库不提供任何服务密钥。请自行开通元宝搜索和 DeepSeek，填写你自己的两项 API Key；不要把 `.env.local` 提交或上传。

```powershell
git clone https://github.com/wei-smo/evidence-detective.git
cd evidence-detective
Copy-Item .env.example .env.local
# 在本机编辑 .env.local，填写 YUANBAO_SEARCH_API_KEY 和 REASONING_API_KEY
python -m evidence_detective.server --host 127.0.0.1 --port 8765
```

Linux/macOS 使用 `cp .env.example .env.local`，并按环境使用 `python3`。要求 Python 3.10+ 与系统 curl，运行时没有第三方 Python 依赖。浏览器打开 `http://127.0.0.1:8765/`。先确认页面配置状态，再发起真实查询；配置齐全不代表外部服务调用一定成功。

公开模板保留项目选用的 DeepSeek 地址与 `deepseek-flash` 模型，两个密钥留空。对外部署还需自行设置至少16字符的 `APP_ACCESS_TOKEN`，并按[部署说明](docs/DEPLOYMENT.md)配置 HTTPS 和访问控制。

研究方法、提示词与“训练”的真实边界见[方法与训练说明](docs/TRAINING_AND_METHODS.md)。经脱敏的用户/助手开发交流见[开发对话归档](docs/conversations/README.md)。当前项目没有执行模型权重微调或强化学习；对话属于设计、研究与工程调试材料。

发布范围和未上传的本机材料见[公开发布说明](docs/PUBLIC_RELEASE.md)。

## 当前版本

调查流程接入的推理接口由本地 `.env.local` 决定：本机实际为 `https://api.deepseek.com`（OpenAI 兼容 `/chat/completions`），配置模型 ID 为 `deepseek-flash`。代码只读环境变量，不硬编码供应商。接口兼容性和流程以实际验收记录为准；配置名称不能证明服务内部路由。

1. 将问题拆成保留对象、时间、版本和条件的可核查主张。
2. 通过腾讯元宝 SearchPro 执行直接检索和反证检索，读取选中的真实网页。
3. 第一次模型调用判断来源是否适用于主张，分别核对对象、时间、条件和一手身份。
4. 第二次模型调用判断原文支持度：FULL / PARTIAL / CONTRADICT / NONE / UNKNOWN。
5. 程序验证引用来自本次真实材料，保存原文通道、字符位置和 SHA-256；根据缺口自动补查。
6. 达到证据门槛或预算边界后输出结论、剩余缺口与停止码。

来源适用性和文字蕴含分别保存。不适用来源不能支持确定结论；部分支持也不能替代尚未补齐的条件。引用逐字匹配只证明出处一致，不保证语义正确。

只使用本地明确启用的推理接口，不自动更换供应商或模型，也不扫描其他服务密钥。模型没有额外联网工具，核心搜索只走元宝。

本轮执行三道真实验收题：明确事实、开放问答、无可靠证据。详情见 [验收矩阵](docs/ACCEPTANCE.md) 和 [独立审查](artifacts/review/independent-review.md)。后续已接入用户提供的六角色画像，由素材代理裁切、主代理验收。历史 `demos/case-*.json` 是模型关闭时的降级记录，不能当作新流程验收。

## 本机启动与配置

要求 Python 3.10+、系统 curl。运行时没有第三方 Python 包依赖。本机使用 Python 3.13.5 验证。

```powershell
cd E:\1024
python -m evidence_detective.server --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765`，默认仅本机可访问。

后端从项目根目录 `.env.local` 读取配置。保留已有元宝密钥，只在本地填写密钥，不要发到聊天里或前端代码中。当前用户认可的配置为：

```dotenv
YUANBAO_SEARCH_API_KEY=在本地填写
REASONING_ENABLED=1
REASONING_BASE_URL=https://api.deepseek.com
REASONING_MODEL=deepseek-flash
REASONING_API_KEY=在本地填写
```

修改后重启服务。`/api/health` 显示配置是否齐全及模型名，不返回密钥；配置齐全不等于服务一定可用。缺少配置时明确降级，不自动更换供应商。新环境可参考 `.env.example`；元宝密钥也支持 `TENCENTCLOUD_WSA_APIKEY`。

单次模型输出默认上限3200，截断不会自动重试或提高上限。官方 `api.deepseek.com` 上的 `deepseek-flash` / `deepseek-v4-pro` 默认发送 `reasoning_effort=none`，将固定预算用于返回JSON，来源适用性和原文支持度仍分别由同一配置模型审查；可用 `REASONING_EFFORT=none|low|high|max` 明确覆盖。其他兼容接口默认不附加此参数。原默认high和后续low均在本轮真实查询出现预算耗尽、无正文，不作为已成功配置。参数依据：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。这不改变模型或证据门槛。

## 使用与前端接入

输入公开、非敏感的问题后开始调查，时间线显示实际查询、两阶段审查、补查和停止原因。点击主张查看证据及独立来源判断；来源链接可回到真实页面。历史回放和 Markdown / JSON 导出可用。

前端事件字段、SSE 封装、枚举和快照更新规则见 [FRONTEND_EVENTS.md](docs/FRONTEND_EVENTS.md)。接口入口为 `POST /api/investigations`，事件流为 `GET /api/investigations/<id>/events`。

人工复核台保留为独立功能，署名标注及人工结论不会覆盖自动结论。

## 检查与真实验收

以下命令不调用外部模型或元宝：

```powershell
python -m unittest discover -s tests -v
node --check static/app.js
python tools/browser_check.py --report-id <已保存的调查ID>
```

浏览器检查需要 Playwright 和 Chromium 或 Edge，仅用于开发验收。指定报告 ID 时从已保存报告回放，不发起新调查。

以下命令调用本地配置的元宝及推理接口，会消耗服务额度：

```powershell
python tools/run_acceptance.py
```

该工具只运行一个真实案例，保存到 `artifacts/acceptance/<id>/`；原始审计保存在 `data/investigations/<id>/audit.jsonl`，不是公开静态资源。自动检查核对来源、引用、原文快照和调用顺序；语义准确性需要另行复核，不能以模型自评替代。

默认最多 6 次搜索，调度预算 300 秒。每批最多审查 2 个来源、全程最多读取 8 个网页；模型取得与主张相关的一段连续原文，完整已读网页保留在本地报告和审计中。已读候选在预算内轮换，补查前优先审查积压材料。未读取、未适用或未完成的部分保留缺口，不承诺检查所有搜索结果。

## 目录

```text
evidence_detective/   后端、调查循环、来源/证据、存储与人工复核
static/              原生 HTML/CSS/JS 工作台
tests/               离线行为与本地 HTTP 测试
tools/               单案例真实验收、出处校验与浏览器检查
docs/                前端协议、交付状态与部署说明
artifacts/acceptance/ 每次真实验收报告与检查结果
data/investigations/  本地报告及完整审计
demos/               历史降级演示，保留原记录
```

部署边界见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)。本轮没有部署公网或扩展多案例。
