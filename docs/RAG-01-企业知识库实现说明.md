# 企业知识库问答（RAG）实现说明

代码在 `assistant/app/kbase/`，服务端口 8094，前端入口是左侧菜单「企业知识库」。
这份文档说明做了什么、怎么跑、哪些是真实的、哪些是模拟的。评测数字见 `RAG-02-评测结果.md`，怎么用见 `RAG-03-使用说明书.md`。
批量入库、定时任务、告警、高并发保护、降级、监控和链路追踪是后加的，见 `RAG-04-生产化改造说明.md`；本文第 3 节里「失败处理」「每日对账」的调度方式以 RAG-04 为准。
**2026-10-15 按新项目说明重构成 v3**（chunk 级增量、三路召回 + HyDE、主题确认、32K 预算、公司 SSO + 内网 / VPN、图片私有化、图纸解析重写、事件 → RabbitMQ → SMTP、补偿任务），改动和原因见 `RAG-06-v3重构说明.md`，项目说明每条对应的代码见 `RAG-07-模块与代码对照.md`。本文已同步到 v3。

## 1. 一句话说明

把产品规格书、手册、工艺文件、选型表、CAD 图纸统一导入知识库，员工用一句话提问。
问参数的（额定转矩多少、轴径多少）直接查结构化数据；问做法和原因的（过热怎么排查）走向量检索；
回答的每句话都标出处（文档号、章节、页码，带原图），没有依据就不回答，没有权限的资料查不到。
用公司 SSO 登录，按部门和职级决定能看的资料；只在公司内网提供服务，远程用公司电脑连 VPN。

## 2. 目录结构

```
app/kbase/
├── store.py              MySQL 表结构和读写（权威数据都在这里）
├── authz.py              统一授权 Scope：向量检索、SQL、图片链接用同一套可见范围（角色 / 部门 / 密级）
├── vectors.py            Milvus 集合 kb_chunks_v3（稠密 + 稀疏 + 内置中文 BM25，带 chunk 状态，从 MySQL 派生）
├── kg.py                 Neo4j 知识图谱（产品—物料—文档—参数）
├── server.py             FastAPI 服务
├── auth/                 公司 SSO（OIDC）、内部目录（SCIM）、身份解析、内网中间件、模拟 SSO
├── kingdee/              金蝶 EAS 对接层
│   ├── client.py         EAS WebService 客户端（SOAP 登录、取数、分页、会话过期重登）
│   ├── forms.py          两个数据集的字段，以及对应 EAS 的哪张表
│   ├── sync.py           同步物料和 BOM 到本地表 kd_material、kd_bom
│   └── mock_gateway.py   本地模拟网关（按 EAS WebService 的格式返回 MiniERP 的数据）
├── ingest/               入库流程
│   ├── graph.py          LangGraph 入库图：登记→路由→解析→切片→质量门禁→向量化→暂存→对账→切换版本
│   ├── clean.py          入口校验、型号写法统一、脱敏、术语单位统一、chunk 归一化哈希
│   ├── parsers.py        三条解析路线：文档 / 表格 / 图纸
│   ├── cad.py            图纸结构化（模型 + 图纸空间、块展开、尺寸公差、明细表网格、标题栏、卡片）
│   ├── images.py         图片私有存储、视觉模型描述、扫描页转写
│   ├── subject.py        新增 chunk 的主体识别 + 证据校验
│   ├── events.py         持久化通知事件 → RabbitMQ → SMTP
│   ├── params.py         参数结构化（型号—参数名—数值—单位）
│   ├── versioning.py     chunk 比对、暂存、对账、事务切换、回滚、补偿任务
│   ├── notify.py         发件箱和 SMTP 发送
│   └── batch.py          批量导入
├── query/                问答流程
│   ├── graph.py          LangGraph 问答图
│   ├── guard.py          敏感词拦截、输出脱敏
│   ├── intent.py         意图路由（规则）、追问改写、改写 + HyDE（模型）
│   ├── topic.py          主题确认（锁定型号 / 系列，不完整时反问）
│   ├── prompts.py        提示词和版本号
│   ├── memory.py         多轮对话：最近三轮原文 + 更早轮次的摘要
│   ├── lookup.py         SQL 精确查询（参数、BOM、图纸告警）
│   ├── retrieve.py       三路召回（稠密 + HyDE + BM25）、RRF、重排、断崖截断、展开成整章
│   └── answer.py         32K 上下文预算、出处和图片、引用校验、三种兜底话术
└── eval/                 评测集、评测脚本、单变量调参（tune.py）
```

## 3. 入库流程

```
文件 SHA-256 判重 → 路由 ─┬─ 文档路线（PDF / Word / PPT / Markdown / 图片）─┐
                          ├─ 表格路线（Excel 选型表）                     ├→ 清洗·脱敏·术语单位 → 按章节切分 → chunk 哈希比对（沿用 / 新增 / 消失）
                          └─ 图纸路线（DXF / DWG）                        ┘
   → 新增 chunk 主体识别 + 证据校验 → 质量门禁 → 新增 chunk 向量化 → 暂存（PENDING）→ 对账 → 事务切换 + 通知事件 → Markdown 存 MinIO
任何一步出错 → 统一的失败出口：能重试的排期重试，不能重试的通知负责人
```

| 环节 | 做法 |
| --- | --- |
| 入口校验 | 没填密级或可见范围的拒绝；加密 PDF、损坏的 Office 文件、空文件、不支持的类型直接拒绝并写明原因 |
| 文档路线 | Markdown 直接读；Word 用 Docling；PPT 按页读（保留页码）；PDF 先试直接抽文字，表格或插图多的交给 MinerU，扫描件逐页交给 Qwen-VL 转写；图片文件交给 Qwen-VL |
| 清洗 | 页眉页脚、页码、全半角；脱敏（手机号、身份证、银行卡、邮箱、敏感词）；术语单位统一（扭矩→转矩、Nm→N·m） |
| 增量 | chunk 按归一化文本算 SHA-256 和当前生效的比对：没变的沿用向量和主体识别结果，新增的才识别和向量化，消失的切换时标失效 |
| 主体识别 | 新增 chunk 逐块调模型识别型号 / 系列，证据必须能在原文里找到，不过就用规则 |
| 表格路线 | openpyxl 读选型表，每一行一个型号，每一列一个参数，拆成「型号—参数名—数值—单位」写入 `kb_param` |
| 图纸路线 | DWG → ODA → DXF；ezdxf 遍历模型空间和图纸空间并展开图块；尺寸读几何块文字、\S 堆叠公差、测量值 + 标注样式兜底、炸开的标注；明细表按表格线重建网格，识别 GB 明细栏；标题栏读块属性；每张图一张结构化卡片，缺项的渲染成图片交给 Qwen-VL 复核 |
| 交叉校验 | 图纸的轴径、明细表物料，和金蝶同步过来的物料主数据、BOM 对比，对不上就生成告警（`kb_alert`） |
| 参数结构化 | 规格书里的参数表也拆成结构化记录；不同文档里的叫法（额定转矩 / 额定扭矩）统一成同一个参数名 |
| 质量门禁 | 空块和超短块比例、型号识别率、表格行数是否一致、图纸标题栏是否完整，不达标不进索引 |
| 一致性 | doc_id + version 幂等；新增 chunk 写成 PENDING（检索看不到），对账通过后一个事务切换 chunk 状态（valid_from / valid_to）和版本指针；Milvus 状态提交后同步，失败登记补偿任务 |
| 失败处理 | 临时故障按 30 秒、2 分钟、10 分钟重试三次，从上次停下的那一步接着做；最终失败登记补偿任务清理 MinIO、Milvus 残留，并发通知事件 |
| 通知 | 持久化事件（和切换同一个事务）→ RabbitMQ → 消费者渲染成邮件 → SMTP，按事件号去重 |
| 每日对账 | 凌晨逐份文档核对 MySQL 和 Milvus 的切片数量和内容哈希，自动补缺删多，修不好的发告警 |

## 4. 问答流程

```
输入检查 → 理解问题（追问改写 + 路由）→ 主题确认 ─┬─ PARAM：SQL 查参数 ──────────────────────────────────────────────────────┐
                                                  ├─ DESC ：改写 + HyDE → 三路召回各 Top10 → RRF → 重排 → 断崖截断 ≤3 → 展开整章 ├→ 兜底判断 → 32K 预算装上下文 → 生成 → 引用校验 → 输出
                                                  └─ MIXED：两路都走 ──────────────────────────────────────────────────────┘
```

| 环节 | 做法 |
| --- | --- |
| 输入检查 | 敏感词库拦截（个人隐私、薪酬、违法内容） |
| 追问改写 | 只有带指代的追问才调模型，结合「最近三轮原文 + 更早轮次的摘要」补全成完整问题；首轮提问用规则，不调模型 |
| 意图路由 | 有型号且问参数 → PARAM；问原因、做法 → DESC；问了参数又问了别的 → MIXED |
| 主题确认 | 锁定型号或系列；型号写不全且有多个候选时，参数类问题反问用户；追问沿用上文锁定的主题 |
| 权限 | 同一个 Scope 生成 Milvus 预过滤表达式、SQL 条件、图片链接判断；部门沿组织树继承，未标注默认拒绝；缓存按权限范围分 |
| 召回 | 改写后问题的稠密向量、HyDE 假设答案的稠密向量、Milvus 内置中文 BM25，三路各 10 条，RRF 融合；HyDE 失败就跳过这一路 |
| 重排 | bge-reranker-v2-m3 打分，在分数断崖处截断，最多 3 条；命中切片后取它所在的完整章节作为上下文 |
| 上下文 | 按 32K token 预算：给回答留 2K，历史最多 3K，证据按顺序装，装不下的截断或丢弃并记录 |
| 生成 | 要求每句话标 `[编号]`，只依据给出的资料；出处显示文档号、章节、页码和所在章节的图片（鉴权后签临时链接） |
| 引用校验 | 检查有没有引用、编号存不存在、回答里的数值在不在被引用的资料里；不通过就重答一次，还不通过就降级成摘录原文 |
| 三种兜底 | 搜不到 → 提示补充型号；相关度太低 → 说明不作答并列出最接近的资料；有资料但没权限 → 明确告知无权限 |
| 输出 | SSE 分阶段推送：进度 → 理解结果 → 引用来源 → 正文 → 完整结果；手机号、身份证号自动打码 |
| 反馈 | 每次问答写 MongoDB 查询日志（问题、改写、召回、精排、答案与引用）；点踩时选原因、可填正确答案，回写到这条日志，Bad Case 池从 MongoDB 筛 |

## 5. 金蝶 EAS 对接

科朗用的是金蝶 EAS。EAS 对外集成走的是 WebService（SOAP），和金蝶云星空的 REST 接口不是一回事。对接层在 `kingdee/`：

- 登录：`/ormrpc/services/EASLogin` 的 `login` 方法（用户名、密码、解决方案名、数据中心、语言、数据库类型），返回会话号 `sessionId`
- 取数：调用一个只读查询服务的 `query(数据集, 起始行, 每页行数)`，请求头里带 `sessionId`，分页返回 JSON
- 只读，只同步物料和 BOM 两类主数据到本地表 `kd_material`、`kd_bom`，用来和图纸做交叉校验、回答「某个型号用了哪些物料」

**需要注意的四点：**

1. **EAS 标准产品里没有「随便查一张表」的通用接口。** 读物料和 BOM 需要 EAS 那边做一个只读查询服务并发布成 WebService（属于 BOS 二次开发，一般由 EAS 顾问或公司 IT 完成）。代码里约定的服务名是 `WSKbMasterDataFacade`，这是我定的约定，不是 EAS 自带的。
2. 另一种更常见的做法是让 IT 在 EAS 数据库上建两个只读视图、开一个只读账号直接查。两种方式拿到的字段是一样的，`kingdee/forms.py` 里写了每个字段对应 EAS 的哪张表（`T_BD_Material`、`T_MM_BOM`、`T_MM_BOMEntry` 等）。
3. 现在连的是本地模拟网关（`mock_gateway.py`，端口 8096），它按 EAS WebService 的格式把 MiniERP 的数据包装出来。**没有连过任何真实的 EAS 环境**，表名和字段名是按 EAS 标准产品写的，真实环境做过二次开发的话要让 EAS 顾问核对。
4. 接真实环境需要一个 EAS 集成账号和发布好的查询服务，这要公司正式授权。改 `.env` 里 `KINGDEE_` 开头的几项即可，代码不用动。

## 6. 哪些是真实的，哪些是模拟的

| 项目 | 状态 |
| --- | --- |
| 入库图、问答图、chunk 增量、版本切换、补偿、对账、重试、事件、权限过滤、引用校验 | 真实实现，有自测（`scripts/kbase_selftest.py` 38 项） |
| 公司 SSO、内部目录 | 模拟网关 8096 上的 OIDC + SCIM，流程按标准写；没连过真实身份平台 |
| 大模型 | DeepSeek 官方接口代替 Qwen3-32B；视觉模型当前没有可用的 key（图片只存储不描述，缺项图纸转人工） |
| Milvus、MySQL、MongoDB、MinIO、Redis、Neo4j、RabbitMQ、Prometheus、Grafana、Langfuse | 本机 Docker 真实运行 |
| BGE-M3、bge-reranker-v2-m3 | 本机真实模型（CPU） |
| MinerU | 真实调用云端接口（需要外网） |
| Docling | Word 可用；PDF 需要从 HuggingFace 下载版面模型，本机下载不了，所以 PDF 不走 Docling |
| DWG 图纸 | 代码支持，但本机没装 ODA File Converter，实际只测过 DXF；DWG 文件目前会被拒绝并告警 |
| 金蝶 EAS | 模拟网关，没连真实环境；查询服务的接口是自己约定的 |
| 邮件 | 没配 SMTP 时只写发件箱；自测里用本地收信服务器验证过能发出去 |
| 资料 | 全部是模拟数据：MiniERP 的 48 份 PLM 文档、3 份 PDF 手册、1 份选型表、1 份 Word、1 份 PPT、6 张 DXF 图纸 |

## 7. 怎么跑

```bash
cd assistant
./scripts/kbase.sh start                                 # 容器 + 问数（8093）+ 外部系统模拟网关（8096）+ 知识库服务（8094）

PYTHONPATH=. .venv/bin/python -m app.kbase.kingdee.sync              # 同步金蝶 EAS 物料和 BOM
PYTHONPATH=. .venv/bin/python scripts/make_kbase_samples.py          # 生成样例文件
PYTHONPATH=. .venv/bin/python -m app.kbase.ingest.batch all          # 批量导入
PYTHONPATH=. .venv/bin/python -m pytest tests/test_kbase.py tests/test_kbase_v3.py -q   # 单元测试
KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python -m app.kbase.eval.tune --ragas 40      # 单变量调参
KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python scripts/kbase_selftest.py        # 入库链路自测（借用服务里的模型，省内存）
KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python scripts/resilience_selftest.py   # 故障注入自测
PYTHONPATH=. .venv/bin/python scripts/loadtest.py kbase 20 3                       # 压测
PYTHONPATH=. .venv/bin/python -m app.kbase.eval.runner all           # 评测
```

## 8. 主要的表

| 表 | 内容 |
| --- | --- |
| `kb_document` | 文档主表：类型、密级、可见范围、负责人、当前生效版本 |
| `kb_doc_version` | 每个版本的状态（暂存 / 生效 / 已替换 / 失败）、文件哈希、解析方式 |
| `kb_chunk` / `kb_parent` | 切片（状态、生效区间、归一化哈希、主体识别结果、各版本的元数据）和它所在的整章（权威内容，Milvus 由它派生） |
| `kb_param` | 结构化参数：型号、参数名、数值、单位、出处 |
| `kb_drawing` / `kb_drawing_dim` / `kb_drawing_bom` | 图纸标题栏、尺寸公差、明细表 |
| `kb_alert` | 图纸和 ERP 对不上的告警 |
| `kb_ingest_job` | 入库任务：停在哪一步、试了几次、下次什么时候重试 |
| `kb_event` | 持久化通知事件（版本切换、入库失败、对账异常……） |
| `kb_outbox` | 待发的通知邮件 |
| `kb_image` | 文档图片：MinIO 对象、所属版本和章节、描述 |
| `kb_compensation` | 补偿任务 |
| `kb_audit_log` / `kb_kd_user` | 审计日志；工号 → 金蝶用户映射 |
| `kb_answer_log` / `kb_feedback` | 每次回答引用了哪些切片和版本；点赞点踩 |
| `kd_material` / `kd_bom` | 从金蝶同步的物料和 BOM |
