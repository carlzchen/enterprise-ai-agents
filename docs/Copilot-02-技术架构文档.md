# 经营与人力数据分析 Copilot：技术架构文档

> 版本：2026-10-06
> 对象：开发、运维、技术讲解
> 配套文档：[需求文档](Copilot-01-需求文档.md)、[Python 版架构](Copilot-03-技术架构文档-Python版.md)
> 本文描述的是"ERP 后端用 Java、Copilot 用 Python"的架构，本仓库已按它实现。第 16 节是架构和代码的对应关系。

---

## 1. 一句话说明

用户用自然语言提问，系统先判断他想干什么，再去找"这个指标怎么算、数据在哪张表"，然后生成 SQL、检查 SQL、按权限过滤、执行，最后把数字、图表和口径一起流式返回。全程只读，全程留痕。

### 五条设计原则

| 原则 | 意思 | 为什么 |
|---|---|---|
| 数字由数据库算，模型只负责理解和表达 | 模型不心算、不估算，所有数字来自 SQL 执行结果 | 大模型算数会出错，而且无法复核 |
| 口径先于 SQL | 先查到指标定义，再写 SQL | 避免同一个指标每次算法不同 |
| 权限放在数据层，不放在提示词里 | 无权限的数据根本不会进入模型 | 提示词拦不住，模型拿不到才是真的安全 |
| 模型写的 SQL 不能直接执行 | 必须经过解析、白名单、试运行三道检查 | 模型可能写错，也可能被用户诱导 |
| 一切可评测 | 每类问题都有标准答案，改动后自动回归 | 没有评测就只能凭感觉 |

---

## 2. 总体架构图

```
┌─ 用户层 ─────────────────────────────────────────────────────────────────────┐
│ 浏览器：Vue 3 + Element Plus + ECharts                                       │
│ 智能问数对话页 · 经营看板 · 人力看板 · 审计日志页                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │ HTTPS / SSE
                                       ▼
┌─ 网关层 ─────────────────────────────────────────────────────────────────────┐
│ Nginx：静态资源 · 反向代理 · SSE 透传                                        │
│ /api/ → ERP 后端（8080）          /copilot-api/ → Copilot 服务（8093）       │
└──────────────────────────────────────────────────────────────────────────────┘
                 │                                          │
                 ▼                                          ▼
┌─ ERP 后端（Java） ───────────────┐    ┌─ Copilot 服务（Python） ─────────────┐
│ Java 21 · Spring Boot 3.3        │    │ FastAPI · Pydantic · sqlglot         │
│ Spring Security + JWT 登录       │    │ 校验 ERP 签发的同一个 JWT            │
│ RBAC：10 个角色                  │    │ 意图路由 → 工具调用 → 流式回答       │
│ 业务接口（HR、销售、财务 ...）   │    │ 语义层：口径库 · 模板 · 视图说明     │
│ 看板接口（人力、经营）           │    │ SQL 生成 · SQL 校验（sqlglot）       │
│ 白名单视图查询 + 敏感列裁剪      │    │ 行级过滤 · 脱敏 · 查询审计           │
│ 审计日志（AOP 切面）             │    │ 对话记忆 · 结果缓存                  │
│ Flyway 管理表和视图              │    │ 评测模块                             │
└──────────────────────────────────┘    └──────────────────────────────────────┘
                 │ 读写                                     │ 只读账号
                 ▼                                          ▼
┌─ 数据层 ─────────────────────────────────────────────────────────────────────┐
│ MySQL 8.4    业务库：127 张表、92 个视图｜assistant 库：审计日志、用户反馈   │
│ Milvus 2.6   三类知识库的向量：制度文档 · 数据语义 · 业务口径                │
│ Redis 7      对话记忆 · 查询结果缓存                                         │
│ MongoDB 7    会话历史 · 文档登记                                             │
│ MinIO        制度原文 · 导出的报表文件                                       │
└──────────────────────────────────────────────────────────────────────────────┘
                                                            │ 仅发送已按权限过滤的内容
                                                            ▼
┌─ 模型层（只有 Copilot 调用） ────────────────────────────────────────────────┐
│ 大模型 API：DeepSeek（意图识别、槽位抽取、SQL 生成、回答生成）               │
│ 本地模型：BGE-M3（向量化）· bge-reranker-v2-m3（重排）                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

**怎么读这张图**

- 用户只接触浏览器。浏览器的所有请求先到 Nginx，由它决定转给谁。
- 左边的 ERP 后端是 Java 写的，管业务数据、登录和看板接口。
- 右边的 Copilot 服务是 Python 写的，管对话、问数和评测。它不自己管账号，而是校验 ERP 签发的同一个登录令牌（JWT），所以用户在 ERP 里是什么角色，在 Copilot 里就是什么角色。
- Copilot 用一个只读账号连业务库，只能读白名单里的视图。
- 大模型在最下面，只接收"已经按权限过滤过"的表结构和查询结果。

---

## 3. 技术栈

| 层 | 技术 | 用来干什么 |
|---|---|---|
| 前端 | Vue 3、Vite、Element Plus、ECharts | 对话页、看板、图表 |
| 网关 | Nginx | 静态资源、反向代理、SSE 透传 |
| ERP 后端 | **Java 21、Spring Boot 3.3**、Spring Security、Spring Data JPA、JdbcTemplate、Flyway | 业务接口、登录、RBAC、看板接口、表和视图的版本管理 |
| Copilot 服务 | Python、FastAPI、Pydantic | 对话接口、流程编排、参数校验。流程用普通 Python 写，没有用流程框架 |
| 大模型调用 | Function Calling、Few-shot Prompt | 意图识别、槽位抽取、工具选择、SQL 生成、回答生成 |
| SQL 处理 | sqlglot | 解析 SQL、检查表和字段、改写加权限条件 |
| 检索 | BGE-M3、Milvus、bge-reranker-v2-m3 | 制度文档、数据语义、业务口径三类知识库的检索 |
| 业务数据库 | MySQL 8.4 | 业务表、视图、口径库、审计日志 |
| 缓存与记忆 | Redis | 对话记忆、查询结果缓存 |
| 会话与文件 | MongoDB、MinIO | 会话历史、制度原文、导出文件 |
| 部署 | Docker、Docker Compose | 容器化部署 |
| 压测 | JMeter | 查询接口的并发压测 |

---

## 4. 分层架构

| 层 | 职责 | 主要模块 |
|---|---|---|
| 接入层 | 校验登录令牌，取出用户、角色、所属部门和产线；生成追踪号；限流；SSE 推送 | `api/http`、`api/auth` |
| 编排层 | 决定这次请求走哪条路线、按什么顺序调用工具、失败时怎么重试 | `pipeline.py` 里的一个函数 |
| 工具层 | 每个工具只做一件事，输入输出都有固定格式 | 知识检索、SQL 生成、SQL 校验、查询、归因、图表、导出 |
| 语义层 | 告诉模型"指标怎么算、数据在哪" | 口径库、视图说明、分析模板 |
| 权限层 | 决定这个人能看哪些表、哪些行、哪些列，并记录审计 | 表白名单、行级过滤、列脱敏、审计日志 |
| 数据层 | 存数据 | MySQL、Milvus、Redis、MongoDB、MinIO |

---

## 5. 一次问数的完整流程

以"上个月伺服线的加班率是多少？"为例，提问人是 HR 经理。

```
用户提问
  │
  ▼
① 接入          校验 JWT → 用户 hr01，角色 HR；生成追踪号
  │
  ▼
② 读取记忆      从 Redis 取本会话上一轮的槽位和历史摘要（首轮为空）
  │
  ▼
③ 意图路由      先走规则：没有"制度/规定"等关键词，规则未命中
  │             再走模型（带 Few-shot 例题）：意图 = 经营问数 / 考勤工时
  │             槽位 = {指标: 加班率, 产线: 伺服, 时间: 2026-09}
  ▼
④ 语义检索      在口径库里检索到"加班率 = 加班工时 ÷ 应出勤工时"
  │             在数据语义库里检索到视图 v_attendance、v_payslip 的字段说明
  │             只保留 HR 角色有权访问的视图（Schema 裁剪）
  ▼
⑤ 模板匹配      命中模板"按产线统计月度加班率" → 直接填参数，跳到 ⑦
  │             未命中 → 进入 ⑥
  ▼
⑥ 生成 SQL      把口径、裁剪后的表结构、问题一起交给模型，输出结构化的 SQL
  │
  ▼
⑦ SQL 校验      语法解析 → 只能是查询 → 表白名单 → 字段白名单 → 试运行
  │             不通过：带着错误信息回到 ⑥ 修正，最多 2 次
  ▼
⑧ 权限处理      加行级过滤条件；强制加行数上限；去掉无权限的敏感列
  │
  ▼
⑨ 执行查询      先查 Redis 缓存；未命中则用只读账号执行，结果写回缓存
  │
  ▼
⑩ 生成回答      选图表类型；模型根据查询结果写说明；流式返回
  │             同时返回：数字、图表、所用口径、数据截止日期
  ▼
⑪ 收尾          写审计日志；更新 Redis 记忆（槽位 + 本轮摘要）
```

追问"那步进线呢？"时，第 ③ 步只抽到"产线 = 步进"，指标和时间从第 ② 步读到的上一轮槽位继承，后面的步骤相同。

---

## 6. Agent 编排

### 6.1 流程图

```
                               START
                                 │
                          ┌──────▼──────┐
                          │ load_memory │
                          └──────┬──────┘
                          ┌──────▼──────┐
                          │route_intent │   规则 → 意图缓存 → 模型 + Few-shot
                          └─┬────┬────┬─┘
              ┌─────────────┘    │    └──────────────┐
              ▼                  ▼                   ▼
       [ 制度问答 ]         [ 经营问数 ]          [ 测算预警 ]
              │                  │                   │
       search_policy      prepare_template      run_analysis
              │             │        │          （归因 / 测算）
              │           命中     未命中            │
              │             │        ▼               │
              │             │   generate_sql ◄──┐    │
              │             │        │          │    │
              │             ▼        ▼          │    │
              │           validate_sql ─────────┤    │
              │                  │              │    │
              │          apply_permission ──────┘    │
              │                  │    校验不通过则交给模型修正，最多 2 次
              │             execute_sql              │
              │                  │                   │
              │             build_chart ◄────────────┘
              │                  │
              └────────────► answer
                               │
                           finalize ──► END

  口径说明、追问澄清、换图表、拒绝写操作这几种情况不查数据，从 route_intent 直接到 answer。
  导出不在主流程里：用户点"导出"时先只返回预览，
  等用户在界面上确认后才生成文件。
```

### 6.2 意图路由：规则 + Few-shot 两层

**为什么两层**：全交给模型不稳定，同一句话有时分对有时分错；全靠规则又覆盖不了各种说法。所以能确定的先用规则，剩下的交给模型并给例题。

```python
RULES = [
    (r"制度|规定|流程|怎么申请|需要什么证明", "制度问答"),
    (r"为什么|原因|怎么回事|如果.*会|订阅|提醒我", "测算预警"),
]

FEW_SHOT = """
问：年假有几天？
答：{"intent": "制度问答"}
问：上月伺服线加班率？
答：{"intent": "经营问数", "sub": "考勤工时", "metric": "加班率", "line": "伺服", "period": "上月"}
问：三季度毛利率为什么下降？
答：{"intent": "测算预警", "sub": "归因", "metric": "毛利率", "period": "2026-Q3", "compare": "2026-Q2"}
"""

def route_intent(state):
    q = state["question"]
    for pattern, intent in RULES:                      # 第一层：规则
        if re.search(pattern, q):
            return {"intent": intent, "slots": extract_slots(q, state)}
    out = llm.with_structured_output(IntentResult).invoke(   # 第二层：模型 + 例题
        FEW_SHOT + f"\n上一轮槽位：{state['last_slots']}\n问：{q}\n答：")
    slots = {**state["last_slots"], **out.slots}       # 没提到的沿用上一轮
    return {"intent": out.intent, "slots": slots}
```

### 6.3 多轮追问怎么实现

| 用户的追问 | 系统做的事 |
|---|---|
| "那步进线呢？" | 只替换产线槽位，其他继承 |
| "换成按季度看" | 只替换时间粒度 |
| "只看华东" | 追加一个过滤条件 |
| "画成柱状图" | 不重新查询，只换图表类型 |
| 明显换了话题 | 清空槽位，不继承 |

指代消解（"那""它""这个客户"指什么）靠把上一轮的槽位和摘要一起放进提示词，由模型输出补全后的槽位。

---

## 7. 语义层：三类知识库和口径体系

### 7.1 三类知识库

| 知识库 | 存什么 | 给谁用 | 示例 |
|---|---|---|---|
| 制度文档库 | 考勤、请假、报销、薪酬等制度原文的切片 | 制度问答 | 《考勤管理制度》第三章 |
| 数据语义库 | 每个视图和字段的中文说明、取值、常见问法 | SQL 生成 | `v_attendance.overtime_hours`：当日加班工时 |
| 业务口径库 | 每个指标的名称、别名、公式、来源 | SQL 生成、看板 | 加班率 = 加班工时 ÷ 应出勤工时 |

三类都向量化后存进 Milvus，提问时按相似度检索出最相关的几条，拼进提示词。这一步叫 **Prompt 注入**，目的是让模型"带着资料答题"。

### 7.2 Schema 裁剪

业务库有 91 个视图。全部塞给模型有三个坏处：贵、慢、容易选错表。所以只给它：

1. 检索出来和问题最相关的 3–5 个视图；
2. 其中当前角色有权访问的；
3. 每个视图里去掉当前角色无权看的敏感列。

模型看不到的表和字段，自然写不进 SQL。

### 7.3 口径体系：原子指标 → 派生指标

```
口径库
├── 原子指标（直接从数据里数出来）
│   ├── 在职人数            v_employee        status <> 'LEFT'
│   ├── 入职人数 / 离职人数  v_employee_change  change_type
│   ├── 应出勤天数 / 实际出勤天数  v_payslip   scheduled_days / attend_days
│   ├── 加班工时            v_attendance      SUM(overtime_hours)
│   ├── 良品数              v_wo_report       报工良品数量
│   ├── 人工成本            v_payslip         应发 + 单位社保公积金
│   ├── 销售收入 / 销售成本  v_sales_fact      revenue_cny / cost_cny
│   └── 回款金额 / 应收余额  v_receipt / v_sales_invoice
│
└── 派生指标（用原子指标算出来）
    ├── 离职率          = 当月离职人数 ÷ 月初在职人数（和人力看板一致）
    ├── 出勤率          = 实际出勤天数 ÷ 应出勤天数
    ├── 加班率          = 加班工时 ÷ 应出勤工时
    ├── 人均产值        = 产线完工产值 ÷ 产线平均在职人数
    ├── 直接人工成本率  = 一线人工成本 ÷ 销售收入
    ├── 产品毛利率      = （销售收入 − 销售成本）÷ 销售收入
    └── 客户毛利率      = 同上，按客户汇总
```

口径用 YAML 文件维护，启动时加载进 MySQL 和 Milvus：

```yaml
- name: 加班率
  aliases: [加班比例, 加班占比]
  type: derived
  formula: 加班工时 / 应出勤工时
  depends_on: [加班工时, 应出勤工时]
  dimensions: [月份, 部门, 产线, 班组]
  sensitivity: normal
  unit: "%"

- name: 产品毛利率
  aliases: [毛利率, GPM]
  type: derived
  formula: (销售收入 - 销售成本) / 销售收入
  depends_on: [销售收入, 销售成本]
  dimensions: [月份, 季度, 事业部, 系列, 产品, 客户, 行业, 区域]
  sensitivity: finance        # 只对财务和管理层开放
  unit: "%"
```

### 7.4 分析模板

高频问题不让模型自由写 SQL，而是做成带参数的固定查询：

```yaml
- id: T07
  name: 按产线统计月度加班率
  intent: 考勤工时
  params: {month: "YYYY-MM", line: "可选"}
  sql: |
    SELECT line_code, SUM(overtime_hours) / (COUNT(DISTINCT employee_id, work_date) * 8) AS overtime_rate
    FROM v_attendance
    WHERE DATE_FORMAT(work_date, '%Y-%m') = :month
      AND (:line IS NULL OR line_code = :line)
    GROUP BY line_code
```

命中模板时模型只负责填参数，SQL 是人写好并验证过的。项目里沉淀了 24 个模板，覆盖约 75% 的常见意图，剩下的才走 SQL 生成。

### 7.5 毛利归因的算法

"毛利为什么变了"由一个确定性的计算工具回答，不让模型自己分析。把两期的毛利差拆成四项：

| 因素 | 计算方式 | 含义 |
|---|---|---|
| 价格 | Σ（本期单价 − 上期单价）× 本期销量 | 卖贵了还是卖便宜了 |
| 成本 | −Σ（本期单位成本 − 上期单位成本）× 本期销量 | 做一台的成本涨了还是降了 |
| 销量 | （本期总销量 − 上期总销量）× 上期平均单位毛利 | 总共多卖还是少卖了 |
| 结构 | Σ（本期销量占比 − 上期销量占比）× 本期总销量 × 上期该产品单位毛利 | 高毛利产品的占比变了没有 |

四项相加严格等于总毛利变动。模型拿到这四个数后只负责写成一段人能读的话。

---

## 8. 工具清单

每个工具的参数都用 Pydantic 定义。模型通过 Function Calling 说"我要调用哪个工具、参数是什么"，程序先校验参数格式，再真正执行。校验不通过会把错误还给模型重填一次。

模型能选择的工具有 8 个（`schemas.py`）：

| 工具 | 作用 | 关键参数 |
|---|---|---|
| `policy_question` | 制度问答 | 补全后的问题 |
| `run_template` | 用分析模板查数（优先） | 模板编号、分组、过滤、时间短语、前几名、排序列、是否追问 |
| `free_query` | 模板接不住时，交给 SQL 生成 | 补全后的问题、时间短语 |
| `attribution` | 毛利变动归因 | 分析期、对比期、拆分维度、过滤 |
| `what_if` | 按假设测算 | 场景（计件单价、基本工资、销售价格、单位成本）、调整幅度 |
| `explain_metric` | 说明某个指标的口径 | 指标名 |
| `clarify` | 信息不够时追问 | 要问的话、可选项 |
| `chitchat` | 和数据、制度无关的话 | 简短回复 |

模型选完工具后，下面这些步骤由流程固定执行，模型不能跳过也不能改顺序：

| 步骤 | 作用 | 风险等级 |
|---|---|---|
| 语义检索 | 检索口径、视图说明、模板 | 低 |
| SQL 生成 | 根据口径和裁剪后的表结构生成 SQL | 中 |
| SQL 校验 | 语法、只读、白名单、敏感列、危险函数 | — |
| 权限改写 | 行级过滤、列裁剪、打码、行数上限 | — |
| 查询执行 | 用只读白名单账号执行 | 中 |
| 图表生成 | 根据结果选图表 | 低 |
| 报表导出 | 导出 Excel | **高，需人工确认** |

预警订阅和 Excel 台账解析没有实现。

参数校验示例：

```python
class SqlQuery(BaseModel):
    """模型必须按这个格式输出，不能只返回一段文字"""
    sql: str = Field(description="一条 MySQL SELECT 语句")
    metrics: list[str] = Field(description="用到的指标名称，必须来自口径库")
    explanation: str = Field(description="用一句话说明这条 SQL 在算什么")

class TemplateArgs(BaseModel):
    template_id: str = Field(pattern=r"^T\d{2}$")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")       # 写成"十月"会被直接拦下
    line: Literal["伺服", "步进", "无刷", "罩极", "贯流", "减速"] | None = None
```

---

## 9. SQL 安全校验

模型生成的 SQL 要依次通过下面几道检查，任何一道不过都不会执行。

```
模型生成的 SQL
  │
  ▼
① 语法解析        用 sqlglot 解析成语法树；解析失败 → 退回修正
  │
  ▼
② 语句类型        只能是一条 SELECT；出现增删改、建表、多条语句 → 拒绝
  │
  ▼
③ 表白名单        用到的表必须都在当前角色的可访问视图里 → 否则拒绝
  │
  ▼
④ 字段白名单      字段必须存在；用到无权限的敏感列 → 拒绝
  │
  ▼
⑤ 危险函数        SLEEP、BENCHMARK、LOAD_FILE、系统库 → 拒绝
  │
  ▼
⑥ 改写            把每张视图换成"这个人能看到的版本"：加行级条件、去掉无权限的列、
  │               打码；强制 LIMIT 1000
  ▼
⑦ 试运行          用执行账号跑一遍 LIMIT 0 的版本，让数据库确认表、字段、权限都没问题；
  │               再看执行计划，预计扫描量过大 → 要求用户缩小范围
  ▼
用只读账号执行
```

```python
def validate_sql(sql: str, role: str) -> exp.Expression:
    tree = sqlglot.parse_one(sql, dialect="mysql")                 # ①
    if not isinstance(tree, exp.Select):                           # ②
        raise SqlRejected("只允许查询语句")
    tables = {t.name for t in tree.find_all(exp.Table)}
    if extra := tables - allowed_views(role):                      # ③
        raise SqlRejected(f"无权访问：{extra}")
    cols = {c.name for c in tree.find_all(exp.Column)}
    if hidden := cols & hidden_columns(role):                      # ④
        raise SqlRejected(f"无权查看字段：{hidden}")
    if any(f.name.upper() in BANNED_FUNCS for f in tree.find_all(exp.Func)):   # ⑤
        raise SqlRejected("包含不允许的函数")
    return tree
```

最后一道保险在数据库：执行这些 SQL 用的是专门的账号 `copilot_ro`，它只被授予了白名单视图的 SELECT 权限。就算前面全漏了，它也读不到别的表，更改不了任何数据。

---

## 10. 权限与审计

### 10.1 四层权限

```
用户登录（ERP 签发 JWT，里面有用户名和角色）
  │
  ▼
① 角色（RBAC）    10 个角色；Copilot 没有任何新增、修改、删除的工具
  │
  ▼
② 表级            每个角色有一份可访问视图清单
  │               例：销售看不到工资表；生产看不到总账
  ▼
③ 行级            按用户所属部门或产线自动加过滤条件
  │               例：车间主任只能查本产线的考勤
  ▼
④ 列级            敏感列按角色整列去掉或打码
  │               例：销售查发货记录时没有成本和毛利两列
  ▼
审计日志          用户、角色、问题、SQL、行数、耗时、是否被拒绝
```

行级过滤不是把条件拼到 SQL 末尾（那样容易被绕过），而是在语法树上把受限的表替换成带条件的子查询：

```python
def apply_row_filter(tree, user):
    scope = row_scope(user)                 # 例：{"v_attendance": "line_code = 'L5'"}
    for table in tree.find_all(exp.Table):
        if cond := scope.get(table.name):
            sub = sqlglot.parse_one(f"SELECT * FROM {table.name} WHERE {cond}")
            table.replace(sub.subquery(table.alias_or_name))
    return tree
```

### 10.2 敏感字段处理

| 字段 | 无权限角色看到的 |
|---|---|
| 身份证号、银行卡号 | `4301**********1234` |
| 薪资、社保基数 | 整列不返回 |
| 产品成本、采购价 | 整列不返回 |
| 产品毛利、客户毛利 | 整列不返回，并提示"需要管理层或财务权限" |

### 10.3 审计日志表

| 字段 | 含义 |
|---|---|
| trace_id | 追踪号，和 ERP、Nginx 日志里的是同一个 |
| username / role | 谁、什么角色 |
| question | 原始问题 |
| intent | 识别出的意图 |
| sql_text | 实际执行的 SQL（改写后的） |
| row_count / elapsed_ms | 返回行数、耗时 |
| status | 成功 / 被拒绝 / 失败，以及原因 |
| exported | 是否导出、导出时间、确认人 |

### 10.4 导出需人工确认

导出是唯一会让数据离开系统的操作，所以分两步，中间必须由人确认：

```python
def start(result_id, username):                 # 第一步：只给预览，不生成文件
    payload = memory.result_get(result_id)
    if payload["username"] != username:
        raise PermissionError("只能导出自己查询的结果")
    export_id = uuid4().hex[:16]
    _pending[export_id] = {"username": username, "payload": payload}
    return {"export_id": export_id, "preview": {"rows": ..., "sensitive": ...}}

def resume(export_id, username, decision):      # 第二步：用户点了确认才写 Excel
    req = _pending[export_id]
    if decision != "confirm":
        _pending.pop(export_id)
        return {"confirmed": False}
    write_excel(req["payload"], watermark=f"{username} {now()}")
    audit.mark_exported(req["audit_id"])
    return {"confirmed": True}
```

---

## 11. 记忆与性能

### 11.1 Redis 里存什么

| 键 | 内容 | 过期时间 |
|---|---|---|
| `copilot:mem:{会话}:slots` | 上一轮的槽位 | 2 小时 |
| `copilot:mem:{会话}:turns` | 最近 4 轮原文 | 2 小时 |
| `copilot:mem:{会话}:summary` | 更早对话的摘要 | 2 小时 |
| `copilot:sql:{角色}:{行级范围}:{SQL 哈希}` | 查询结果 | 10 分钟 |

缓存键里必须带角色和行级范围，否则管理层查过的结果会被销售命中，造成越权。

### 11.2 四个优化手段

| 手段 | 做法 | 解决什么 |
|---|---|---|
| 上下文压缩 | 超过 4 轮的历史压成摘要；表结构只给裁剪后的 | 输入变短，更快更省 |
| 结果缓存 | 相同角色、相同范围、相同 SQL 直接返回 | 重复查询不再打数据库 |
| 模板优先 | 命中模板就不调用模型生成 SQL | 少一次模型调用 |
| 流式输出 | 先推"正在查询"等进度，再逐字推回答 | 用户感知的等待时间变短 |

项目数据（和项目说明一致）：P95 首字响应由 6.8 秒降到 1.5 秒；单次分析 Token 消耗由约 12k 降到约 6k。

### 11.3 压测

用 JMeter 对查询接口做并发压测，关注三件事：并发升高时的 P95 耗时、错误率、缓存命中率。瓶颈通常在大模型调用，所以压测分两组：命中模板和缓存的请求，以及需要模型生成 SQL 的请求。

---

## 12. 看板架构

```
ERP 业务表 ──► 视图（v_employee、v_attendance、v_payslip、v_sales_fact ...）
                 │
                 ├──► ERP 后端看板接口（Java）──► 前端 ECharts 看板
                 │         │
                 │         └── 指标算法取自口径库
                 │
                 └──► Copilot 问数（Python）
                           │
                           └── 指标算法取自口径库
```

看板和问数读同一批视图、用同一套口径，所以同一个指标在两边的数字一定相同。看板上每个卡片都有"问一问"入口，点击后带着当前的筛选条件进入对话。

| 看板 | 主要内容 |
|---|---|
| 人力看板 | 在职人数、入离职、流失率、出勤率、加班、人工成本、人均工资 |
| 经营看板 | 收入、毛利、订单、库存、应收逾期；按事业部、系列、客户切换 |

---

## 13. 评测架构

```
评测集（8 类意图，每条有问题、意图、标准答案）
  │
  ▼
评测运行器 ──► 逐条调用 Copilot ──► 记录意图、SQL、结果、耗时
  │
  ▼
打分
  ├── 意图识别准确率      识别的意图 = 标注的意图
  ├── 文档召回准确率      标准出处是否在检索结果里（制度问答）
  ├── SQL 执行成功率      SQL 通过校验并执行成功
  ├── 指标口径一致率      SQL 用的指标和口径库定义一致
  ├── 结果准确率          查询结果 = 标准答案（数值容差内）
  └── 响应耗时            首字时间、总时间
  │
  ▼
报告 + Bad Case 清单 ──► 分析原因 ──► 修复 ──► 加入评测集回归
```

**标准答案从哪来**：问数类由人工写 SQL 在数据库里算出来，不能让模型生成，否则等于让学生自己批卷。

**Bad Case 常见原因和对策**

| 原因 | 对策 |
|---|---|
| 意图分错 | 补规则或补 Few-shot 例题 |
| 选错表 | 改视图说明，补常见问法 |
| 口径用错 | 补指标别名，或做成模板 |
| 时间理解错（"上月""本季度"） | 时间解析改成代码处理，不交给模型 |
| SQL 语法错 | 靠校验后的自动修正解决 |
| 多轮继承错 | 调整槽位合并规则 |

---

## 14. 部署架构

```
┌─ 一台服务器，Docker Compose ─────────────────────────────────┐
│                                                              │
│   frontend   Nginx + 前端静态文件            5180（对外）    │
│   backend    ERP 后端（Java）                8080            │
│   copilot    Copilot 服务（Python）          8093            │
│   mysql      MySQL 8.4                       3307            │
│   redis      Redis 7                         6381            │
│   milvus     Milvus 2.6（含 etcd、MinIO）    19531           │
│   mongo      MongoDB 7                       27018           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
外部：大模型 API（DeepSeek）
本地模型：BGE-M3、bge-reranker-v2-m3
```

配置项通过环境变量注入，密钥只放在 `.env` 文件里，不进代码仓库。

---

## 15. Java 部分具体负责什么

这一节列出架构里所有用 Java 的地方，方便和 [Python 版](Copilot-03-技术架构文档-Python版.md) 对照。

| 职责 | Java 技术 | 代码位置 |
|---|---|---|
| Web 框架和接口 | Spring Boot、Spring Web | `backend/src/main/java/com/minierp/**/*Controller.java` |
| 登录和令牌 | Spring Security、jjwt、BCrypt | `security/` |
| 角色权限 | `@PreAuthorize` 注解 | 各 Controller |
| 数据访问 | Spring Data JPA、JdbcTemplate | 各 Service |
| 表和视图的版本管理 | Flyway | `resources/db/migration/V1–V12` |
| 白名单视图查询、敏感列裁剪 | 自研 `ViewRegistry`、`ViewQueryService` | `query/` |
| 审计日志 | Spring AOP `@Audited` 切面 | `system/` |
| 看板接口 | `HrController`、`BiController` | `hr/`、`bi/` |
| 模块间通知 | Spring 事件（如 `WorkflowFinishedEvent`） | `workflow/` |
| 接口文档 | springdoc-openapi（Swagger） | — |
| 日志和追踪号 | Logback、MDC | `common/TraceIdFilter` |
| 单元测试 | JUnit 5 | `backend/src/test` |
| 构建和运行 | Maven、Eclipse Temurin 21 | `backend/pom.xml`、`backend/Dockerfile` |
| 模拟数据生成 | `BusinessSimulator` | `seed/` |

---

## 16. 和本仓库的对应关系

2026-10-06 已按本文实现，代码都在 `assistant/app/copilot/`。评测结果、性能数字和没做的部分见 [实现说明](Copilot-04-实现说明与评测结果.md)。

| 架构里的部分 | 代码位置 |
|---|---|
| ERP 后端、登录、RBAC、看板接口 | `backend/`（原有，Java） |
| 主流程和各个步骤 | `assistant/app/copilot/pipeline.py` |
| 意图路由（规则 + Function Calling + Few-shot）、追问合并 | `router.py`、`schemas.py` |
| 时间解析 | `timeparse.py` |
| 口径库、视图说明与权限配置、分析模板 | `semantic/metrics.yaml`、`views.yaml`、`templates.yaml` |
| 模板引擎 | `template_engine.py` |
| SQL 生成 | `text2sql.py` |
| SQL 校验与改写 | `sqlguard.py` |
| 表级、行级、列级权限和打码 | `permission.py` |
| 只读白名单账号 | `assistant/mysql-init/02_copilot.sql` |
| 归因、测算 | `analysis.py` |
| 图表选择、结果整理 | `chart.py`、`result.py` |
| 语义检索、制度文档库 | `retriever.py`、`policy.py`，制度原文在 `assistant/data/policies/` |
| 对话记忆、意图缓存、结果缓存 | `memory.py` |
| 审计、反馈、Bad Case | `audit.py` |
| 导出（人工确认） | `export.py` |
| 接口服务（8093） | `assistant/app/api/http/copilot_server.py` |
| 评测集、评测运行器、性能对照 | `eval/dataset.py`、`eval/runner.py`、`eval/bench.py` |
| 压测计划 | `assistant/loadtest/copilot_query.jmx` |
| 单元测试 | `assistant/tests/test_copilot.py` |
| 前端页面 | `frontend/src/views/Copilot.vue`、`CopilotSemantic.vue`、`CopilotAudit.vue` |

和设计稿不一样的地方：

- 最初用 LangGraph 编排流程，后来去掉了。流程只有三条分支和一个"校验不过就重写"的循环，用一个函数里的 if 和 while 就能写清楚（`pipeline.py` 最下面的 `_pipeline`，三十多行），读起来比"节点加边"直接。导出确认原来用它的暂停机制，现在改成"先存预览、确认后再生成"两个接口。

- 口径库和模板放在 YAML 文件里，没有单独建"Copilot 库"；审计和反馈表建在已有的 `assistant` 库。
- 试运行没有用 `EXPLAIN`：MySQL 不允许只有视图权限的账号对视图做 `EXPLAIN`，所以改成用执行账号跑 `LIMIT 0` 的版本。
- 预警订阅、Excel 台账解析没有做。

---

## 17. 关键设计决策（技术讲解常问）

| 问题 | 回答要点 |
|---|---|
| 为什么不让模型直接写 SQL 然后执行？ | 模型会写错，也可能被诱导。所以加了解析、白名单、试运行三道检查，数据库账号本身也是只读。 |
| 为什么要口径库？ | 同一个指标不同人算法不同。口径库让指标只有一个定义，看板和问数共用，数字才对得上。 |
| 为什么要模板，不全用 Text2SQL？ | 高频问题就那几十种，用人写好的 SQL 又快又准又省钱。模型只处理模板覆盖不到的长尾问题。 |
| 权限为什么不写在提示词里？ | 提示词是"请求"不是"强制"。在数据层过滤后，模型根本拿不到无权限的数据，也就不可能泄露。 |
| 缓存会不会造成越权？ | 会，如果缓存键只用 SQL。所以键里带了角色和行级范围。 |
| 为什么归因不让模型分析？ | 归因是数学分解，四项必须加起来等于总变动。用代码算能保证这一点，模型只负责把结果讲清楚。 |
| 时间"上个月"谁来解析？ | 代码解析。模型不知道今天是几号，交给它容易错。 |
| 为什么不用 LangGraph 这类流程框架？ | 一开始用了，后来去掉。流程是固定的：三条分支加一个最多两次的重写循环，没有并行、没有需要动态决定的路径。用普通函数写只要三十多行，调试和读代码都更直接。框架适合流程复杂、需要中断恢复或多 Agent 协作的场景。 |
| 为什么 ERP 用 Java、Copilot 用 Python？ | ERP 是事务型业务系统，Java 生态成熟；大模型相关的库基本都在 Python。两边通过同一个登录令牌和同一个数据库视图层衔接。 |
| 怎么证明效果？ | 八类意图的评测集，问数类标准答案由人工 SQL 算出，每次改动后回归，Bad Case 修复后加入评测集。 |
