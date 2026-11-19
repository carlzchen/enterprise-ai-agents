# 跟单多 Agent 系统 · 开发说明（给 Claude Code）

> 本文件是项目的完整背景与需求。请先通读，再按「九、开发阶段」逐阶段实现。
> 每完成一个阶段：跑通测试 → 简要汇报 → 等我确认后再进入下一阶段。

---

## 一、背景

- 我在一家深圳的电机制造上市公司（年营收约 18 亿元）做 AI 应用开发实习。
- 已完成的项目一：企业知识库 RAG（产品参数、技术资料、CAD 图纸问答）。技术栈：LangGraph、Qwen3-32B / Qwen-VL（vLLM 本地部署，OpenAI 兼容接口）、BGE-M3、bge-reranker-v2-m3、Milvus 2.5、MySQL、MongoDB、MinIO、Redis、RabbitMQ、Langfuse、FastAPI、Docker。
- 本项目（项目二）：**邮件驱动的跟单多 Agent 系统**。客户通过邮件发 PO、PO 变更、交期询问、加急请求、规格书询价等，系统自动理解、拆分任务、调用工具处理，生成草稿，由内勤人工审批后执行。
- 公司环境（已确认或高度可能）：
  - ERP 为**金蝶云·星空**，数据库 **SQL Server**；公司其他系统用 **MySQL**。
  - 星空有官方 WebAPI（单据保存 / 提交 / 审核）。
  - 办公协同用**飞书**。
  - 服务部署在内网，生产上线走 Jira 变更审批。
- **我拿不到真实的业务数据和系统权限。** 所以本项目必须：
  1. 所有外部系统（邮箱、ERP、飞书、知识库）都通过**适配器接口**访问，先实现 **Mock 版本**，以后再换成真实实现，业务代码不用改；
  2. 自带**模拟数据生成器**（模拟 ERP 数据、模拟客户邮件和 PO 附件），保证项目能在本地完整运行和演示。

## 二、项目目标

1. 做出一个**可运行、可演示、可评测**的多 Agent 系统，用于求职作品集和项目说明。
2. 架构设计要经得起技术讲解追问：为什么用多 Agent、工具怎么设计、怎么保证安全、怎么评测。
3. **所有指标必须由评测脚本实际跑出来**，不允许在代码、README 或文档里写任何没有实测依据的数字。

## 三、核心设计原则

1. **确定性环节用固定流程，开放环节用 Agent。** PO 抽取后的校验（价格、料号、数量、金额）用规则或 SQL，**不交给 LLM 判断**。
2. **人在回路。** 所有写操作（发邮件、写 ERP、派任务）都必须经过 LangGraph `interrupt` 人工审批。Agent 只能起草，不能直接执行。
3. **最小权限。** 每个 Agent 只能调用自己被分配的工具；写类工具单独标记，并强制走审批。
4. **结构化通信。** Agent 之间、工具输入输出一律用 Pydantic 模型，不传自由文本。
5. **可观测、可审计。** 全链路接入 Langfuse，Langfuse 不可用时降级为本地日志，不影响主流程。每次写操作都记录审计日志：谁、什么时间、AI 给的建议、人最终改成了什么。
6. **模型可替换。** LLM 通过 OpenAI 兼容接口调用，`base_url` / `model` / `api_key` 全部从配置读取。开发时可以接任意 OpenAI 兼容服务，部署时切到内网 vLLM 上的 Qwen3-32B。

## 四、系统架构

```
客户邮件（订单专用邮箱 orders@ / market@）
        ▼
 EmailAdapter（Mock: 读本地 .eml 目录；Real: IMAP）
        ▼
 ┌──────── Supervisor（调度 Agent）────────┐
 │ 理解邮件 → 拆分子任务 → 并行/串行分派 → 汇总 │
 └──┬──────────┬──────────┬──────────┬────┘
    ▼          ▼          ▼          ▼
 订单 Agent  交付 Agent  技术 Agent  跟进 Agent
    └──────────┴─────┬────┴──────────┘
                     ▼
            审核 Agent（Reviewer）
                     ▼
        人工审批（LangGraph interrupt）
                     ▼
     执行：发送邮件 / 写入 ERP / 创建飞书任务
```

### 4.1 各 Agent 职责与工具

| Agent | 职责 | 工具（R = 只读，W = 写，需审批） |
|---|---|---|
| **Supervisor** | 意图识别（可多意图）、拆分子任务、分派、汇总成一封回复草稿 | 只能调用其他 Agent |
| **订单 Agent** | PO 录单、PO 变更比对。**内部是固定流程**：解析 → 抽取 → 规则校验 → 生成 ERP 销售订单草稿 | R: `parse_document`, `map_customer_part`, `get_price_reference`, `find_existing_po`；W: `create_erp_sales_order` |
| **交付 Agent** | 交期 / 状态查询，评估加急请求（ReAct 式自主调用工具） | R: `get_order_status`, `get_inventory`, `get_production_progress`；W: `create_task`（派给计划部门） |
| **技术 Agent** | 理解客户规格需求，推荐型号，列出参数差异 | R: `search_product_kb`, `compare_spec` |
| **跟进 Agent** | 客户长期记忆；样品 / 报价超时后主动起草跟进邮件 | R: `get_customer_memory`；W: `update_customer_memory`, `schedule_followup` |
| **审核 Agent** | 审批前检查所有草稿：①不得泄露底价、成本、毛利；②任何承诺（交期、数量、价格）必须能在工具结果中找到依据；③草稿中引用的数据与工具结果一致；④语气和格式符合商务邮件规范 | 只读，输出 `ReviewResult(passed, issues[])` |

通用工具：`draft_email`（只生成草稿，不发送）。

### 4.2 订单 Agent 内部流程（固定流程）

1. **识别客户**：根据发件人域名匹配客户档案。
2. **解析附件**：PDF 用 pdfplumber；Excel 用 pandas；扫描件预留 Qwen-VL 接口（Mock 阶段可以先不做）。
3. **抽取**：已配置模板的客户用模板解析；其他客户用 LLM 按 Pydantic Schema 输出（`POHeader` + `POLine[]`）。
4. **规则校验**（纯代码，不调用 LLM）：
   - 客户料号 → 内部物料号映射；找不到时，按描述和参数相似度给出候选，标记为待人工确认；
   - 单价与价目表或最近成交价比较，偏差超过阈值（可配置，默认 3%）就标记；
   - 数量是否满足最小起订量和包装倍数；
   - 交期早于标准交期就标记；
   - 各行金额之和是否等于 PO 总额（用来发现抽取错误）；
   - 同一个 PO 号是否已经录入过。
5. **变更比对**：同一个 PO 号再次出现时，与上一版逐行比较，输出 `POChangeReport`（数量、交期、价格的变化，新增行，取消行）。
6. **置信度分流**：所有字段高置信且规则全部通过 → 标记为可一键确认；否则高亮异常字段。
7. **输出**：ERP 销售订单草稿（星空 WebAPI 的 JSON 结构）、订单确认邮件草稿。
8. **学习**：人工确认后的料号映射写回映射表。

## 五、共享状态（LangGraph State，示意）

```python
class SubTask(BaseModel):
    id: str
    type: Literal["po_entry", "po_change", "delivery_query", "expedite", "spec_inquiry", "followup", "other"]
    assigned_agent: str
    input: dict
    status: Literal["pending", "running", "done", "failed", "needs_human"]
    result: dict | None = None

class CaseState(BaseModel):
    case_id: str
    email: EmailMessage            # 原邮件 + 附件路径
    customer_id: str | None
    subtasks: list[SubTask]
    draft_reply: EmailDraft | None
    pending_actions: list[Action]  # 待审批的写操作
    review: ReviewResult | None
    review_rounds: int = 0         # 审核驳回最多重写 2 次，之后转人工
    approval: Approval | None
    audit_log: list[AuditEntry]
```

用 checkpointer 持久化（开发时用 SQLite 或内存；部署时可以换成 MySQL 或 Postgres），审批中断后可以从断点继续。

## 六、数据层（全部 Mock）

用 SQLAlchemy 定义表结构，开发默认 SQLite，配置项可切换到 MySQL。表名参考星空的命名风格，方便以后对接：

- `customers`：客户档案（含邮件域名、国家、币种、对接销售）
- `materials`：内部物料（物料号、型号、关键参数：电压、功率、转速、轴长等）
- `customer_part_map`：客户料号 ↔ 内部物料号
- `price_list`：客户 × 物料 价格，含生效日期
- `sales_orders` / `sales_order_lines`：模拟 ERP 已录入的销售订单（**用作评测的标准答案**）
- `inventory`、`production_progress`
- `customer_memory`：跟进 Agent 的长期记忆
- `followups`、`tasks`、`audit_log`、`po_versions`

### 6.1 模拟数据生成器（`scripts/gen_mock_data.py`）

- 20–30 个客户（国内和海外混合，海外客户邮件用英文）、200 种左右物料、价目表、库存、生产进度。
- 用 reportlab 生成 **PO PDF**，openpyxl 生成 **PO Excel**。至少 3–4 种不同版式，模拟不同客户的格式。
- 生成 `.eml` 邮件，覆盖下列场景，每类若干封：
  1. 新 PO（PDF / Excel）
  2. PO 变更（同一个 PO 号的第二版：改了数量、交期，或增删行）
  3. 交期 / 状态询问
  4. 加急请求
  5. 规格询价（正文里写参数需求）
  6. **复合邮件**（例如加急 + 询价，或者 PO + 交期问题）
  7. 干扰邮件（推销、无关内容）
- 同时生成**标准答案**：每封邮件对应的意图标签、期望的子任务；每张 PO 对应 `sales_orders` 里的正确记录。
- 随机种子固定，保证可以复现。

## 七、适配器接口（`adapters/`）

每个适配器都有 `Protocol` 定义、`Mock` 实现，以及 `Real` 实现的占位（真实实现留 TODO，写清楚要对接什么）：

- `EmailAdapter`：`fetch_new()`、`send(draft)`。Mock 版读 `data/inbox/*.eml`，"发送"的邮件写到 `data/outbox/`；Real 版用 IMAP / SMTP。
- `ERPAdapter`：查询订单、库存、价格，`save_sales_order(draft)`。Mock 版读写本地数据库；Real 版对接金蝶星空 WebAPI（Save 接口，只保存不审核）。
- `TaskAdapter`：`create_task()`。Mock 版写入数据库；Real 版对接飞书任务或飞书机器人。
- `KnowledgeAdapter`：`search_product_kb()`。Mock 版基于 `materials` 表做简单检索；Real 版调用项目一的 RAG 服务接口。
- `LLMClient`：OpenAI 兼容接口，支持结构化输出（JSON Schema / 工具调用）。

## 八、对外接口与界面

- FastAPI：
  - `POST /cases/ingest`：拉取新邮件并创建 case
  - `GET /cases`、`GET /cases/{id}`
  - `POST /cases/{id}/approve`：可以附带人工修改后的草稿
  - `POST /cases/{id}/reject`
- 一个简单的审批页面（Streamlit 或单页 HTML 都可以）：左边显示原邮件和附件，右边显示 AI 草稿、待执行操作、审核意见，异常字段高亮，可以修改后批准。
- CLI：`python -m app.run_demo` 一键跑完"生成数据 → 处理收件箱 → 自动批准 → 输出结果"，用于演示。

## 九、开发阶段（逐阶段完成，每阶段结束停下等我确认）

**阶段 0：脚手架**
- 项目结构、`pyproject.toml`、配置（pydantic-settings + `.env.example`）、日志、`Makefile`、`docker-compose.yml`（可选：MySQL、Langfuse）。
- 验收：`make test` 能通过一个空测试；`.env.example` 写清楚每个配置项的作用。

**阶段 1：数据层与模拟数据**
- 数据表、模拟数据生成器、PO 文档生成、`.eml` 生成、标准答案文件。
- 验收：一条命令生成全部数据；有测试检查数据完整性（比如每张 PO 都能在 `sales_orders` 里找到标准答案）。

**阶段 2：订单 Agent（固定流程）**
- 解析、抽取（模板 + LLM）、规则校验、变更比对、置信度分流、ERP 草稿生成。
- 规则校验部分要有完整的单元测试（不依赖 LLM）。
- 验收：对模拟 PO 跑完整流程，能输出草稿和校验结果。

**阶段 3：Supervisor + 交付 Agent + 审核 Agent + 人工审批**
- LangGraph 图：Supervisor 识别意图并拆分子任务，并行分派；交付 Agent 以 ReAct 方式调用工具；审核 Agent 不通过时驳回重写（最多 2 次）；`interrupt` 等待审批；审批后执行写操作并记录审计日志。
- 验收：复合邮件能被正确拆分；审核 Agent 能拦截"无依据承诺"和"泄露底价"这两类测试用例；审批前不会发生任何写操作（要有测试证明这一点）。

**阶段 4：评测体系（`eval/`）**
- `eval_router.py`：意图识别和任务拆分的准确率（基于标准答案）。
- `eval_po.py`：字段级和行级准确率、料号映射命中率、变更识别准确率（以 `sales_orders` 为标准答案）。
- `eval_agents.py`：端到端任务成功率、工具调用准确率；审核 Agent 的拦截率和误拦截率（要构造一批应该被拦截的草稿）。
- 输出 Markdown 报告到 `eval/reports/`，包括时间戳、模型名、数据规模，以及失败案例清单。
- 验收：一条命令生成报告。**报告里的数字就是项目说明数字的唯一来源。**

**阶段 5：API、审批页面、可观测**
- FastAPI 接口、审批页面、接入 Langfuse（可通过配置开关）。
- 验收：本地能完整演示"收件 → 处理 → 审批 → 执行"。

**阶段 6（时间够再做）：技术 Agent、跟进 Agent**
- 技术 Agent 先用 Mock 的知识库，预留对接项目一 RAG 的接口。
- 跟进 Agent：维护客户记忆；定时扫描超时的样品和报价，起草跟进邮件。

## 十、技术栈

Python 3.11+、LangGraph、Pydantic v2、SQLAlchemy、FastAPI、pdfplumber、pandas、openpyxl、reportlab、pytest、Langfuse（可选）、Streamlit（审批页面，可选）。
LLM：OpenAI 兼容接口（部署目标是内网 vLLM 上的 Qwen3-32B）。

## 十一、建议的目录结构

```
order-agents/
├── app/
│   ├── agents/        # supervisor.py, order_agent.py, delivery_agent.py, reviewer.py, tech_agent.py, followup_agent.py
│   ├── graph/         # state.py, build_graph.py
│   ├── tools/         # 按 Agent 分组的工具，每个工具标注 R/W
│   ├── order_pipeline/# parse.py, extract.py, validate.py, diff.py, templates/
│   ├── adapters/      # email.py, erp.py, task.py, knowledge.py, llm.py
│   ├── db/            # models.py, session.py
│   ├── api/           # FastAPI
│   ├── ui/            # 审批页面
│   └── config.py
├── scripts/           # gen_mock_data.py
├── eval/              # eval_*.py, reports/
├── data/              # inbox/, outbox/, attachments/, ground_truth/（生成的数据，不入库）
├── tests/
├── docker-compose.yml
├── Makefile
└── README.md
```

## 十二、约束与注意事项

- 不要硬编码任何密钥；不要把生成的数据提交到 git（写进 `.gitignore`）。
- 不要编造任何业务事实或指标：README 里的效果数字只能引用 `eval/reports/` 中实际生成的报告。
- 规则校验、变更比对、金额计算这类逻辑必须是**纯代码并有单元测试**。
- 代码注释和 README 用中文；代码标识符用英文。
- 遇到需求不明确的地方，先提出问题，再按最合理的假设实现，并在代码注释和阶段汇报中写明这个假设。
- 每个阶段结束时汇报：做了什么、怎么运行、测试结果、已知问题、下一阶段计划。
