# 跟单多 Agent 升级：SPEC 落地计划

需求原文见 [Workflow-06-多Agent升级需求SPEC.md](Workflow-06-多Agent升级需求SPEC.md)。2026-10-19 决定**在 MiniERP 跟单模块（`assistant/app/workflow`）上原地升级**，不另起独立项目。

## 一、为什么原地升级

- 项目说明项目二就是跟单 Workflow，一份代码，技术讲解演示也是同一套。
- 技术 Agent 可以直接调项目一的知识库（8094），不用 Mock。
- 已有并测过的逻辑直接复用：邮件去引用 / 转发识别 / Message-ID 幂等、多意图 Pydantic 抽取、PO 六项核对、数字溯源校验、发件闸门、商机状态机、待办中心页面、模拟 ERP 数据。

代价：要先起 MiniERP 的 Docker 环境才能跑；ERP 的 Mock 是 MiniERP 自己的库（当作「模拟的金蝶星空」），不是 SPEC 里的独立 SQLite。

## 二、SPEC 和现有代码的对应

| SPEC | 现有代码 | 要做的 | 阶段 |
|---|---|---|---|
| 适配器（邮箱 / ERP / 任务 / 知识库） | 收信、外发、ERP 查询、待办分散在各处 | 抽成 `adapters/`：Protocol + Mock + Real 占位 | 0 ✅ |
| 模拟数据和标准答案 | 54 封模拟邮件 + `evaluate.py` | 补 PO 的 PDF / Excel（多种版式）、PO 变更、加急、复合邮件、干扰邮件，标准答案和 ERP 订单对应 | 1 ✅ |
| 订单 Agent（固定流程） | PO 抽取 + `rules.review_po` | 补附件解析、客户料号映射（含候选）、最小起订量 / 包装倍数、金额合计、PO 变更比对、星空销售订单草稿、映射学习 | 2 ✅ |
| Supervisor / 交付 / 审核 Agent + interrupt 审批 | `triage` 按意图分派（固定代码）+ 发件闸门 | LangGraph 图：拆子任务并行分派、交付 Agent ReAct、审核 Agent（底价泄露、无依据承诺、数据一致、语气），驳回最多 2 次；interrupt + checkpointer；审批仍在待办中心点 | 3 ✅ |
| 评测体系 | `evaluate.py`（意图 / 字段） | 路由、PO、Agent 三套评测，报告到 `data/workflow_eval/reports/` | 4 |
| API、审批页面、可观测 | 跟单工作台四个页面、FastAPI | 案例（case）视图、审批改稿、Langfuse 接入（不可用降级） | 5 |
| 技术 Agent、跟进 Agent | 无 | 技术 Agent 调项目一知识库；跟进 Agent 客户记忆 + 超时扫描 | 6 |

现有的非标多 Agent 评估（`agents/nonstd.py`）和 DeepAgents 预研（`agents/research.py`）先不动，技术 Agent 做完后再决定是否合并。

## 三、阶段 0 做了什么（2026-10-19）

- `app/workflow/adapters/`
  - `models.py`：适配器的输入输出（RawEmail、EmailDraft、SentReceipt、Customer、Material、PriceReference、OrderStatus、SalesOrderDraft（含星空 Save 的 Model 结构）、TaskRequest、KBAnswer）
  - `base.py`：四个 Protocol，写操作标 [W]
  - `mock.py`：FolderEmail、MiniErpERP（销售订单草稿写成 JSON，同一客户 + PO 号幂等）、WorkbenchTask、MasterDataKB
  - `real.py`：GraphEmail / KingdeeERP / FeishuTask / RagKnowledge 占位（调用即报 NotConnected，写明要对接什么）；ImapEmail 从原来的收信代码搬过来
  - `__init__.py`：按配置选实现，测试可以 `adapters.override(...)`
- 收信（`mailbox.pull`）和外发（`flows.confirm_outbound`）已改成走适配器，Mock 下行为和改造前一致；ERP 查询和派待办的调用点在后续阶段写 Agent 工具时再切。
- 配置：`WORKFLOW_EMAIL_ADAPTER / ERP_ADAPTER / TASK_ADAPTER / KNOWLEDGE_ADAPTER`、订单邮箱、审核最多重写次数、模拟数据种子、Graph / 星空 / 飞书的连接参数，`.env.example` 里逐项写了说明；不填 `WORKFLOW_EMAIL_ADAPTER` 时兼容旧的 IMAP 配置。
- 测试：新增 `tests/test_adapters.py` 9 个（接口一致性、按配置选实现、真实系统未接时报错、收发往返、草稿幂等、发件闸门经适配器发出且不重复）。跟单、多 Agent、预研、问数、知识库测试合计 193 个全部通过。

## 三之二、阶段 1 做了什么（2026-10-19）

一条命令：`PYTHONPATH=. .venv/bin/python scripts/workflow_gen_mock.py`（`--no-db` 只生成文件）。代码在 `app/workflow/mockdata/`（world 读 ERP 并写补充表、docs 渲染 PO、emails 生成邮件和标准答案）。

**补充数据表**（都在 assistant 库，`source='mock'` 的行可以随时重建，不碰 MiniERP 业务表）

| 表 | 内容 | 行数 |
|---|---|---|
| wf_material_ext | 最小起订量、包装倍数、标准交期（按产品线） | 31 |
| wf_price_list | 客户价目表 = 近 6 个月平均成交价，生效日 2026-07-01 | 170 |
| wf_customer_part_map | 客户料号 ↔ 内部物料号，每家客户一种编号风格 | 170 |
| wf_order_po_ref | 近期在途 / 刚发货订单对应的客户 PO 号 | 138 |
| wf_po_version | 预置 3 张「上个月已录入」的 PO 第一版（给变更邮件引用） | 3 |
| wf_customer_memory、wf_followup | 跟进 Agent 用，先建空表 | 0 |

**生成的文件**（`data/workflow_mock/`，种子 42，模拟「今天」= 2026-10-12）

- 70 封 .eml：新 PO 24（PDF 14、Excel 10）、PO 变更 8、重复发送 2、交期询问 8、加急 6、规格询价 8（其中 2 封是我方没有的规格）、复合邮件 8、干扰邮件 6；海外客户写英文；四分之一从客户公司域名的公共地址发出。
- 38 个 PO 附件，4 种版式：A 中文 PDF 带表格线 15、B 英文 PDF 无表格线 7、C 中文 Excel 14、D 英文 Excel 两个工作表 2；第 2 版 8 张、第 3 版 2 张。
- 标准答案：`ground_truth/emails.json`（每封邮件的意图和期望子任务、引用的 PO 号 / 订单号 / 料号 / 要求日期）、`ground_truth/pos.json`（每个附件的正确抬头和明细、映射后的内部物料号、问题标记、变更内容、期望生成的销售订单）。
- 问题标记按 SPEC 规则计算（单价偏离价目表超过 3% 等），变更后的版本重新计算：价格偏离 9、早于标准交期 7、非整箱倍数 3、料号没登记 3、低于起订量 2、重复下单 2、总额对不上 1。
- `manifest.json`：统计和每个文件的 sha256。同一份 ERP 数据 + 同一个种子 → 字节完全相同。

**测试** `tests/test_mock_data.py`（10 个）：同种子逐字节复现、场景配比、每封邮件能解析且和标准答案一致、发件人能认成标准答案里的客户、每个附件都有标准答案且能读出 PO 号和料号、标准订单自洽、变更都指向更早的版本、引用的订单在 ERP 里存在且加急日期早于原交期、客户邮件里不出现我方型号、补充表覆盖标准答案且重复运行结果一样。合计 203 个测试通过。

**和 SPEC 的差异**：物料用 MiniERP 现有的 31 个产品，没有造 200 种——问数和知识库的评测依赖现有主数据，加假产品会让那两边的评测前后不可比；料号映射的难度改由「每家客户自己一套编号」来造（170 条）。

## 三之三、阶段 2 做了什么（2026-10-19）

代码 `app/workflow/order_pipeline/`：parse（pdfplumber / openpyxl）→ extract（中文版式按模板，英文版式大模型按 Schema 输出、校验不过重试一次）→ validate（纯代码：料号映射 + 候选、价格偏离、起订量、包装倍数、标准交期、行金额、总额、重复下单）→ diff（按客户料号逐行比对）→ agent（新单 / 变更 / 重复判定、分流、星空销售订单草稿、确认邮件草稿、记录版本，同一封邮件重跑不算重复）。tools.py 的工具标 R / W，W（写 ERP、学习料号映射）没有审批凭证直接拒绝。规则参数在规则表 `order_check`、模板配置在 `po_templates`。

测试 `tests/test_order_pipeline.py` 18 个（规则边界、候选排序、变更比对、规范化、抽取重试、写工具拒绝、模板逐字段、全流程逐项对标准答案、映射学习）；模拟数据测试 11 个。

演示 `scripts/workflow_order_demo.py`（真实大模型）：38 个附件（模板 29、大模型 9）的抬头、明细、新单 / 变更 / 重复与版本、问题标记、分流全部和标准答案一致，耗时约 38 秒。这是开发自查，正式指标以阶段 4 报告为准。

开发中发现并修正的标准答案问题：料号没登记的行不该再标依赖物料的问题；复合邮件中途换了客户；变更里取消又加回同一料号。

## 三之四、阶段 3 做了什么（2026-10-19）

代码 `app/workflow/graph/`，入口 `cases.py`：`start(raw)` 开案例并跑到审批停下，`pending(case_id)` 取审批页要看的内容，`decide(case_id, {...})` 提交审批。

```
intake → supervisor ─┬─(Send)→ order_agent    ─┐
                     ├─(Send)→ delivery_agent ─┼→ compose → review ─┬─ 通过 ─────────────→ approval（interrupt）─┬─ 批准 → execute → END
                     ├─(Send)→ tech_agent     ─┘     ↑              ├─ 不通过且没到上限 → compose               └─ 驳回 → rejected → END
                     └─ 全是无关邮件 → ignore → END   └──────────────┘└─ 不通过且到上限 → approval（标「审核未通过，转人工」）
```

| 文件 | 做什么 |
|---|---|
| `state.py` | SubTask / Split（Supervisor 答卷）、AgentOutput、ProposedAction（待审批的写操作）、ReviewResult、Draft、Approval；图的状态只放 JSON，能存进 checkpointer |
| `supervisor.py` | 大模型拆子任务（po_entry / po_change / delivery_query / expedite / spec_inquiry / other），Schema 校验不过重试一次；PO 附件和订单子任务的配对、去重、去掉 other、编号由代码做 |
| `delivery.py` | 交付 Agent，复用 `agents/runtime.py` 的 ReAct 循环和证据账本。工具：R `find_order` / `get_order_status` / `get_inventory` / `get_production_progress`（MES 工单）/ `check_expedite`；W `create_task`（只登记）。工具按发件客户绑定范围；能不能满足加急日期由 `check_expedite` 的代码判断（库存扣掉其他订单占用后够不够、日期来不来得及），满足才给 `confirmable_date` |
| `workers.py` | 订单子任务调阶段 2 的订单 Agent（`record=False`，PO 版本也等审批后再记）；规格询价先派工程师、回复只说「技术同事在确认」（阶段 6 换成真正的技术 Agent） |
| `compose.py` | 大模型把各段合成一封回复，驳回时带着问题清单重写；不可用时用模板拼接（审批页同时给出模板版作为安全版本） |
| `reviewer.py` | 审核 Agent，规则是代码：底价 / 成本 / 毛利（关键词 + 按型号算出的单位成本和底价逐个比数字）、无依据承诺（承诺句里的日期必须出现在工具结果里；客户自己要求的日期、PO 里早于标准交期那一行的日期都不算依据；只是复述「您要求的 X 日正在评估」不算承诺）、无依据数字、数据一致（没查过的订单号、张冠李戴的日期、漏掉的子任务）、语气；可选再加一次大模型语气检查 |
| `actions.py` | 审批后的写操作：W `send_email` / `create_task`，加上订单 Agent 的 W `create_erp_sales_order` / `record_po_version`；全部要在 `approved(审批人, 动作编号)` 里才能执行，否则报 NotApproved |
| `build.py` | 组图；`execute` 节点按审批结果只执行批准的动作，审批人改过的主题 / 正文替换进去；审计 `case.approve / case.reject` 记 AI 原稿、人工终稿、是否改过、审核轮次、批了哪些动作 |
| `cases.py` | 案例入口；同一封邮件只开一个案例；新表 `wf_case` 记案例索引和状态（WAITING_APPROVAL / DONE / REJECTED / IGNORED / FAILED） |

checkpointer 用 SQLite（`data/workflow_cases.sqlite`，依赖 `langgraph-checkpoint-sqlite`）：进程重启后用同一个 case_id 从审批断点恢复。

**测试** `tests/test_case_graph.py` 27 个，模型全部换成按标准答案 / 剧本作答的假模型：

- 验收 1（复合邮件拆分）：8 封复合邮件，假 Supervisor 只给类型和单号、不给附件名、顺序打乱，代码配好附件后和标准答案逐项一致，每个子任务分到对应的 Agent，订单 Agent 的 PO 号、交付 Agent 查到的订单号都和标准答案一致。
- 验收 2（审核拦截）：无依据承诺 5 种说法（保证客户要的日期、「可以满足您要求的」、英文 will ship、查不到的日期、「3 个工作日内回复」）、泄露底价 / 成本 4 种（直接说底价、不说「底价」但数字就是底价、英文 our cost、毛利率）全部拦下；有依据的、复述客户要求的 5 种说法放行；初稿泄露底价 → 重写后通过；一直不合格 → 重写 2 次后转人工。
- 验收 3（审批前不写）：图停在审批时，邮件、ERP、待办三个适配器零调用，PO 版本、审计、待办表行数不变；W 工具直接调用报 NotApproved；批准后每样只写一次；驳回只多一条审计；只批准部分动作、审批人改稿都按实际执行并记审计；SQLite 断点「重启」后恢复；重复提交被拒绝、邮件不会发两次。
- 全部测试 298 个通过（含阶段 0–2、旧跟单、多 Agent、问数、知识库）。

**真实模型演示** `scripts/workflow_case_demo.py`：70 封模拟邮件按时间顺序跑完整张图并自动批准（外发、ERP、待办换成记录器，不进工作台）。第二遍结果：拆分和标准答案完全一致 70 / 70（复合 8 / 8），无关邮件 6 封直接结束，62 封初稿一次通过审核、1 封重写一次后通过（E059 英文稿写了「We confirm … 2026-10-21」，而这一行交期早于标准交期）、1 封重写 2 次后转人工（E058 是误拦，见下），约 8 分钟。交付 Agent 每次调 3–5 个工具。这是开发自查，正式指标以阶段 4 的评测报告为准。

**演示中发现并修正的问题**

1. 阶段 2 的订单确认邮件直接截取内部问题说明，把「参考价 23.73、阈值 3%、最小起订量 100、包装倍数 20」写给了客户，括号还被截断。第一遍演示里审核把这些当成无依据数字拦了下来（E004 / E008 / E017 / E037）。现在确认邮件只写「第 n 行 单价 / 交期 / 数量（整箱倍数）在核对」，中英文一致；这些内部数字也不进证据，以后再写出来照样拦。
2. 审核把复述客户要求的句子当成承诺（「您希望提前至 10-23 的需求」「该交期暂时无法确认」）。现在句子里有「评估 / 无法确认 / 您希望 / your request」这类说法、又没有「可以满足 / 确认按 / 保证」这类肯定说法时，客户自己提的日期不算承诺；这两种说法都加进了测试。E058 改完后单独复跑通过。

**还没做 / 留到后面阶段**

- 图还没接到收信轮询和待办中心：现在旧的 `flows.triage` 照常运行，新图通过 `cases.start` 调用；阶段 5 做案例视图、审批改稿页面，再把收信切到新图。
- 规格询价是占位（派工程师），阶段 6 接项目一知识库做技术 Agent。
- 料号候选的人工确认 → `learn_part_mapping` 要等审批页面（阶段 5）。
- 交付 Agent 的「可用库存」= 现有库存 − 其他已确认订单未发数量，没考虑在途和安全库存；能否提前只看库存，排产判断交给计划部任务。

## 四、假设（有变化请告诉我）

1. 真实邮箱以 Microsoft Graph 为主（公司用 Microsoft 365，Exchange Online 已停用账号密码方式），IMAP 作备选。
2. 开发阶段大模型用 DeepSeek（OpenAI 兼容），部署切内网 vLLM 上的 Qwen3-32B，只改配置。
3. ERP 按金蝶云·星空：Mock 用 MiniERP 的库；销售订单只调 Save，不提交、不审核。
4. 现有评测数字（邮件意图 50/54 等）是旧版本的结果；升级后的指标一律以阶段 4 生成的报告为准。
5. 图的 checkpointer 用 SQLite（单机部署够用、不用再加 MySQL 的第三方 saver）；多实例部署时换成 Postgres / MySQL saver，只改 `graph/build.py` 一处。
6. 加急能不能答应只看成品库存（扣掉其他订单占用）；库存不够一律派计划部评估，AI 不给新日期。
