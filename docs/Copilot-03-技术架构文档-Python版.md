# 经营与人力数据分析 Copilot：技术架构文档（Python 版）

> 版本：2026-10-06
> 对象：开发、运维、技术讲解
> 配套文档：[需求文档](Copilot-01-需求文档.md)、[原架构（Java + Python）](Copilot-02-技术架构文档.md)
> 本文是一份设计：把原架构里所有 Java 部分换成 Python，功能不变。本仓库的后端目前仍是 Java，尚未按本文改写。

---

## 1. 改了什么，没改什么

**改了**：ERP 后端从 Java（Spring Boot）换成 Python（FastAPI）。登录、角色权限、业务接口、看板接口、审计、表结构版本管理、模拟数据生成，全部用 Python 重写。

**没改**：前端、Nginx、数据库表和视图、Copilot 的 Agent 编排、语义层、SQL 校验、评测方法。这些原本就和 Java 无关。

**最大的变化**：以前权限规则有两份，一份在 Java 的 `ViewRegistry` 里给 ERP 用，Copilot 要用还得再抄一份。全换成 Python 后，ERP 接口和 Copilot 直接引用同一个权限模块、同一个口径库，只有一份代码。

---

## 2. Java → Python 替换对照表

| 职责 | 原来（Java） | 现在（Python） | 说明 |
|---|---|---|---|
| 语言与运行时 | Java 21、JVM | Python 3.12 | — |
| Web 框架 | Spring Boot 3.3、Spring Web | FastAPI | 都是写接口用的框架 |
| Web 服务器 | 内嵌 Tomcat | Uvicorn（多进程由 Gunicorn 管理） | 负责接收请求 |
| 参数校验 | Bean Validation（`@NotNull` 等注解） | Pydantic v2 | FastAPI 自带，和 Copilot 的工具参数校验是同一套 |
| 数据访问（简单增删改查） | Spring Data JPA | SQLAlchemy 2.0 ORM | 把表映射成类 |
| 数据访问（报表、批量） | JdbcTemplate | SQLAlchemy Core / `text()` | 直接写 SQL |
| 连接池 | HikariCP | SQLAlchemy 连接池 | — |
| 数据库驱动 | MySQL Connector/J | PyMySQL | — |
| 表和视图的版本管理 | Flyway（V1–V12） | Alembic | 原有 SQL 脚本可以原样搬进迁移文件 |
| 登录令牌 | jjwt | PyJWT | 令牌格式不变，前端不用改 |
| 密码哈希 | Spring Security BCrypt | passlib（bcrypt） | 已有密码哈希可以直接验证 |
| 角色权限 | `@PreAuthorize("hasAnyRole(...)")` | FastAPI 依赖 `Depends(require_roles(...))` | 都是在接口上声明谁能访问 |
| 审计日志 | Spring AOP `@Audited` 切面 | 装饰器 `@audited` | 都是在函数外面包一层 |
| 模块间通知 | Spring 事件（`ApplicationEventPublisher`） | 进程内事件总线（几十行代码） | 流程结束时通知报销、请假等模块 |
| 定时任务 | `@Scheduled` | APScheduler | 预警订阅、每日对账 |
| 接口文档 | springdoc-openapi | FastAPI 自带（`/docs`） | 不用额外配置 |
| 日志 | SLF4J + Logback | loguru | Copilot 已经在用 |
| 追踪号 | Logback MDC | `contextvars` | 把追踪号带到每一行日志 |
| 全局异常处理 | `@RestControllerAdvice` | FastAPI `exception_handler` | — |
| 金额计算 | `BigDecimal` | `decimal.Decimal` | 都不用浮点数算钱 |
| 单元测试 | JUnit 5 | pytest | 113 个纯函数测试可以逐个对应改写 |
| 依赖和构建 | Maven（`pom.xml`） | uv（`pyproject.toml`） | Copilot 已经在用 uv |
| 容器镜像 | `eclipse-temurin:21` | `python:3.12-slim` | — |
| 模拟数据生成 | `BusinessSimulator`（Java） | Python 脚本，批量写入 | 逐日模拟两年业务 |

---

## 3. 总体架构图（Python 版）

```
┌─ 用户层 ─────────────────────────────────────────────────────────────────────┐
│ 浏览器：Vue 3 + Element Plus + ECharts                                       │
│ 智能问数对话页 · 经营看板 · 人力看板 · 审计日志页                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │ HTTPS / SSE
                                       ▼
┌─ 网关层 ─────────────────────────────────────────────────────────────────────┐
│ Nginx：静态资源 · 反向代理 · SSE 透传                                        │
│ /api/ → ERP 服务（8080）          /copilot-api/ → Copilot 服务（8093）       │
└──────────────────────────────────────────────────────────────────────────────┘
                 │                                          │
                 ▼                                          ▼
┌─ ERP 服务（Python） ─────────────┐    ┌─ Copilot 服务（Python） ─────────────┐
│ FastAPI · SQLAlchemy · Pydantic  │    │ FastAPI · Pydantic · sqlglot         │
│ 登录与 JWT 签发（PyJWT）         │    │ 意图路由 → 工具调用 → 流式回答       │
│ 业务接口（HR、销售、财务 ...）   │    │ SQL 生成 · SQL 校验（sqlglot）       │
│ 看板接口（人力、经营）           │    │ 对话记忆 · 结果缓存                  │
│ 通用只读查询                     │    │ 制度文档 RAG                         │
│ Alembic 管理表和视图             │    │ 评测模块                             │
└──────────────────────────────────┘    └──────────────────────────────────────┘
                 │ import                                   │ import
                 ▼                                          ▼
┌─ 共享包 common（两个服务都引用，只有一份） ──────────────────────────────────┐
│ auth         JWT 签发与校验 · 密码哈希 · 当前用户                            │
│ permission   角色 · 表白名单 · 行级范围 · 敏感列                             │
│ semantic     口径库 · 分析模板 · 视图说明                                    │
│ models       SQLAlchemy 表模型                                               │
│ audit        审计装饰器 · 审计表                                             │
│ logging      日志格式 · 追踪号                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                 │ 读写                                     │ 只读账号
                 ▼                                          ▼
┌─ 数据层 ─────────────────────────────────────────────────────────────────────┐
│ MySQL 8.4    业务库：127 张表、91 个视图｜Copilot 库：口径、模板、审计、评测 │
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

**和原架构的区别**

- 原来左边是 Java 后端、右边是 Python 服务，中间靠"校验同一个令牌"和"各自维护权限规则"衔接。
- 现在两个服务都是 Python，中间多了一层**共享包**。权限、口径、数据模型只写一次，两边都引用。
- 两个服务仍然分开部署。原因是它们的负载特点不同：ERP 接口是短平快的增删改查；Copilot 每次请求要等大模型，耗时长、占连接久。分开后可以各自扩容，互不拖累。

---

## 4. 代码组织

一个仓库，三个包：

```
minierp/
├── pyproject.toml                依赖和构建配置（uv）
├── alembic/                      表和视图的迁移脚本（原 Flyway V1–V12）
│   └── versions/
│
├── common/                       共享包：两个服务都引用
│   ├── auth/                     JWT 签发与校验、密码哈希、当前用户
│   ├── permission/               角色、表白名单、行级范围、敏感列
│   ├── semantic/                 口径库、视图说明、分析模板（YAML）
│   ├── models/                   SQLAlchemy 表模型
│   ├── audit/                    审计装饰器和审计表
│   ├── events.py                 进程内事件总线
│   └── logging.py                loguru 配置、追踪号
│
├── erp/                          ERP 服务（原 Java 后端）
│   ├── main.py                   FastAPI 入口
│   ├── routers/                  接口：hr、sales、purchase、gl、bi ...
│   ├── services/                 业务编排：读写数据库、发事件
│   ├── rules/                    纯函数业务规则：个税、计件分摊、成本、排程 ...
│   └── seed/                     模拟数据生成器
│
├── copilot/                      Copilot 服务
│   ├── main.py                   FastAPI 入口
│   ├── pipeline.py               主流程和各个步骤（普通函数）
│   ├── tools/                    工具：检索、SQL 生成、校验、归因、图表、导出
│   ├── sqlguard/                 SQL 解析、白名单检查、行级改写
│   ├── memory/                   Redis 对话记忆、结果缓存
│   ├── rag/                      制度文档的导入和检索
│   └── eval/                     评测集、运行器、打分
│
├── tests/                        pytest：规则单测、权限单测、SQL 校验单测
├── frontend/                     Vue 前端（不变）
└── docker-compose.yml
```

原 Java 后端"业务规则抽成纯函数类"的做法保留：`erp/rules/` 里的函数不依赖数据库和框架，直接用 pytest 测。

---

## 5. 各部分怎么用 Python 实现

### 5.1 接口和角色权限

Java 里是在方法上加注解，Python 里是在接口参数里声明依赖，效果一样。

```java
// 原来（Java）
@GetMapping("/dashboard")
@PreAuthorize("hasAnyRole('ADMIN','HR','MANAGEMENT')")
public Map<String, Object> dashboard(@RequestParam String month) { ... }
```

```python
# 现在（Python）
@router.get("/hr/dashboard")
def dashboard(month: str, user: User = Depends(require_roles("ADMIN", "HR", "MANAGEMENT"))):
    return hr_service.dashboard(month, user)
```

`require_roles` 的实现：

```python
def require_roles(*roles: str):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "没有权限")
        return user
    return checker

def current_user(token: str = Depends(oauth2_scheme)) -> User:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256", "HS384", "HS512"])
    user = user_cache.get(payload["sub"])          # 缓存 60 秒，离职后最多 60 秒失效
    if not user or not user.enabled:
        raise HTTPException(401, "账号已停用")
    return user
```

### 5.2 数据访问

简单的增删改查用 ORM，报表和看板直接写 SQL，和原来 JPA 加 JdbcTemplate 的分工一致。

```python
class Employee(Base):                               # 对应表 hr_employee
    __tablename__ = "hr_employee"
    id: Mapped[int] = mapped_column(primary_key=True)
    emp_no: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str]
    dept_id: Mapped[int]
    line_code: Mapped[str | None]
    base_salary: Mapped[Decimal]
    status: Mapped[str]

def headcount_by_dept(db: Session, month: str) -> list[dict]:      # 报表直接写 SQL
    rows = db.execute(text("""
        SELECT dept_name, COUNT(*) AS headcount
        FROM v_employee
        WHERE hire_date <= LAST_DAY(:d) AND (leave_date IS NULL OR leave_date > LAST_DAY(:d))
        GROUP BY dept_name
    """), {"d": month + "-01"})
    return [dict(r._mapping) for r in rows]
```

### 5.3 表结构版本管理

Flyway 的 12 个 SQL 脚本原样搬到 Alembic。视图定义本来就是纯 SQL，不需要改写。

```python
# alembic/versions/0008_org_oa_hr.py
def upgrade():
    op.execute(Path("sql/V8__org_oa_hr.sql").read_text(encoding="utf-8"))
```

### 5.4 统一的权限模块

这是换成 Python 后最有价值的一处。原来 Java 的 `ViewRegistry` 变成一份 Python 数据，ERP 的通用查询接口和 Copilot 的 SQL 校验都读它。

```python
# common/permission/views.py
FIN = frozenset({"ADMIN", "FINANCE", "MANAGEMENT"})
HRS = frozenset({"ADMIN", "HR", "MANAGEMENT"})

@dataclass(frozen=True)
class ViewDef:
    table: str
    roles: frozenset[str]                    # 谁能访问这张视图；空集表示所有角色
    sensitive: frozenset[str] = frozenset()  # 敏感列
    sensitive_roles: frozenset[str] = FIN    # 谁能看敏感列
    row_scope: str | None = None             # 行级范围按哪一列过滤

VIEWS = {
    "employees":  ViewDef("v_employee", frozenset(), frozenset({"base_salary", "social_base"}), HRS | FIN),
    "attendance": ViewDef("v_attendance", HRS | {"PRODUCTION"}, row_scope="line_code"),
    "payslips":   ViewDef("v_payslip", HRS | FIN, frozenset({"gross_pay", "net_pay"}), HRS | FIN),
    "sales-fact": ViewDef("v_sales_fact", FIN | {"SALES"}, frozenset({"cost_cny", "gross_profit_cny"})),
}

def allowed_views(role: str) -> set[str]:
    return {v.table for v in VIEWS.values() if not v.roles or role in v.roles}

def hidden_columns(role: str) -> set[str]:
    return {c for v in VIEWS.values() if role not in v.sensitive_roles for c in v.sensitive}
```

```python
# ERP 的通用查询接口用它
@router.get("/views/{key}")
def query_view(key: str, user: User = Depends(current_user)):
    view = VIEWS[key]
    if view.roles and user.role not in view.roles:
        raise HTTPException(403)
    ...

# Copilot 的 SQL 校验也用它（同一个函数）
if extra := tables - allowed_views(user.role):
    raise SqlRejected(f"无权访问：{extra}")
```

### 5.5 审计

Java 用切面，Python 用装饰器，都是"在函数执行前后自动记一笔"。

```python
def audited(action: str):
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, user: User, **kwargs):
            ok, detail = True, ""
            try:
                return fn(*args, user=user, **kwargs)
            except Exception as e:
                ok, detail = False, str(e)
                raise
            finally:
                write_audit(user.username, action, ok, detail, trace_id.get())
        return inner
    return wrap

@audited("导出报表")
def export_report(result_id: str, *, user: User): ...
```

### 5.6 模块间通知

原来靠 Spring 事件：审批流程结束时发一个事件，报销、请假、合同等模块各自监听。Python 里用一个很小的事件总线实现同样的效果。

```python
# common/events.py
_handlers: dict[type, list[Callable]] = defaultdict(list)

def subscribe(event_type):
    def deco(fn):
        _handlers[event_type].append(fn)
        return fn
    return deco

def publish(event, db: Session):
    for handler in _handlers[type(event)]:
        handler(event, db)                # 同一个数据库事务里执行，失败一起回滚

# 请假模块监听流程结束
@subscribe(WorkflowFinished)
def on_leave_approved(event, db):
    if event.def_code == "LEAVE" and event.approved:
        write_attendance(db, event.biz_id)
```

### 5.7 追踪号

```python
trace_id: ContextVar[str] = ContextVar("trace_id", default="-")

@app.middleware("http")
async def trace_middleware(request, call_next):
    trace_id.set(request.headers.get("X-Trace-Id") or uuid4().hex[:16])
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id.get()
    return response

logger.configure(patcher=lambda r: r["extra"].update(trace=trace_id.get()))
```

ERP 和 Copilot 用同一段代码，所以一次请求在两个服务里的日志带同一个追踪号。

---

## 6. Copilot 部分（和原架构相同）

这一部分原本就是 Python，换语言后没有变化。这里只放图，详细说明见[原架构文档](Copilot-02-技术架构文档.md)第 5–13 节。

### 6.1 一次问数的流程

```
用户提问
  │
  ▼
① 接入          校验 JWT（common.auth）；生成追踪号
  ▼
② 读取记忆      Redis：上一轮槽位 + 历史摘要
  ▼
③ 意图路由      规则 → 模型 + Few-shot；分成制度问答、经营问数、测算预警
  ▼
④ 语义检索      口径库 + 视图说明（common.semantic）；按角色裁剪表结构
  ▼
⑤ 模板匹配      命中 → 填参数；未命中 → 生成 SQL
  ▼
⑥ SQL 校验      解析 → 只读 → 表白名单 → 字段白名单 → 试运行（common.permission）
  ▼
⑦ 权限处理      行级过滤 → 行数上限 → 敏感列裁剪和打码
  ▼
⑧ 执行查询      Redis 缓存 → 只读账号查询
  ▼
⑨ 生成回答      图表 + 文字说明 + 口径；流式返回
  ▼
⑩ 收尾          审计日志（common.audit）；更新记忆
```

括号里标出的是换成 Python 后直接引用共享包的地方。

### 6.2 流程图

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
```

### 6.3 看板

```
业务表 ──► 视图 ──┬──► erp 服务的看板接口 ──► 前端 ECharts
                  │            │
                  │            └── from common.semantic import metrics
                  │
                  └──► copilot 服务的问数
                               │
                               └── from common.semantic import metrics
```

看板接口和问数引用的是同一个 Python 模块里的指标定义，"两边数字一致"从约定变成了代码层面的保证。

---

## 7. 部署架构（Python 版）

```
┌─ 一台服务器，Docker Compose ─────────────────────────────────┐
│                                                              │
│   frontend   Nginx + 前端静态文件            5180（对外）    │
│   erp        ERP 服务（FastAPI）             8080            │
│   copilot    Copilot 服务（FastAPI）         8093            │
│   mysql      MySQL 8.4                       3307            │
│   redis      Redis 7                         6381            │
│   milvus     Milvus 2.6（含 etcd、MinIO）    19531           │
│   mongo      MongoDB 7                       27018           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

`erp` 和 `copilot` 用同一个基础镜像（`python:3.12-slim`）、同一份依赖锁文件，只是启动命令不同：

```yaml
services:
  erp:
    build: .
    command: gunicorn erp.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8080
  copilot:
    build: .
    command: uvicorn copilot.main:app --host 0.0.0.0 --port 8093
```

端口和路径都不变，所以前端和 Nginx 配置不用改。

---

## 8. 换成 Python 的得与失

| 方面 | 得 | 失 |
|---|---|---|
| 代码复用 | 权限、口径、数据模型只写一份 | — |
| 团队 | 只需要会一种后端语言 | — |
| 部署 | 一个镜像、一套依赖管理 | — |
| 开发效率 | 代码量更少，改完即可运行，不用编译 | — |
| 类型安全 | — | Java 编译期就能发现的类型错误，Python 要靠类型标注加检查工具（mypy 或 pyright）和测试来补 |
| 计算密集任务 | — | 模拟器逐日生成两年数据、APS 排程这类纯计算，Python 比 Java 慢；可以用批量写入和 NumPy 缓解 |
| 并发 | 接口多是等数据库和等大模型，用异步处理够用 | 受全局解释器锁限制，纯计算要靠多进程 |
| 事务和企业级生态 | — | Spring 的声明式事务、成熟的安全框架要自己用依赖和上下文管理器实现 |

**结论**：这个项目的重心在 AI 应用，业务接口以读为主、逻辑不复杂，全用 Python 是合适的。如果是交易量很大、事务很复杂的核心业务系统，保留 Java 后端更稳妥。

---

## 9. 迁移步骤

如果要把本仓库的 Java 后端实际改成 Python，建议按下面的顺序，每一步都能独立验证：

| 步骤 | 内容 | 怎么验证 |
|---|---|---|
| 1 | 建共享包：把 `ViewRegistry` 改写成 `common/permission`，JWT 校验改写成 `common/auth` | 用 Java 签发的令牌能在 Python 里校验通过 |
| 2 | Flyway 脚本搬到 Alembic | 在空库上跑完，表和视图数量与原来一致（127 张表、91 个视图） |
| 3 | 先迁只读接口：通用查询、看板接口 | 同一个请求，Java 和 Python 返回的数据逐字段相同 |
| 4 | 迁业务规则：把纯函数类逐个改写成 `erp/rules`，测试同步改成 pytest | 113 个测试全部通过 |
| 5 | 迁写接口和流程引擎 | 走通 9 条业务链路 |
| 6 | 迁模拟器 | 重新生成数据后，凭证借贷平衡、总账与子账对账无差异 |
| 7 | 下线 Java 后端 | 前端全部功能回归 |

第 1–3 步完成后，Copilot 需要的东西就已经全部是 Python 了。第 4–7 步是否要做，取决于是否还想保留完整的 ERP 写入能力。

---

## 10. 讲解时怎么讲这次替换

| 问题 | 回答要点 |
|---|---|
| 为什么考虑把 Java 换成 Python？ | 原架构里权限规则要在 Java 和 Python 各维护一份，容易不一致。全用 Python 后，ERP 接口和 Copilot 引用同一个权限模块和口径库。 |
| Spring 的注解在 Python 里怎么对应？ | `@PreAuthorize` 对应 FastAPI 的依赖注入；`@Audited` 切面对应装饰器；Spring 事件对应一个小的进程内事件总线；Flyway 对应 Alembic。 |
| Python 做后端有什么要注意的？ | 类型错误要靠类型标注和测试提前发现；纯计算任务比 Java 慢；金额必须用 Decimal。 |
| 为什么换了语言还分两个服务？ | 负载特点不同。ERP 接口短平快，Copilot 请求要等大模型。分开后可以各自扩容。 |
| 什么情况下你会保留 Java？ | 交易量大、事务复杂的核心业务系统，或者团队已有成熟的 Java 体系时。 |
