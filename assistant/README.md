# MiniERP 产品技术助手（企业级 RAG）

面向微特电机企业的产品技术问答：销售、FAE、生产、质量人员用自然语言查询产品参数、选型方法、驱动器报警处理、工艺与检验标准、工程变更。回答带编号引用，可追溯到具体文档版本与章节。

知识来源：
- **MiniERP PLM 文档**：产品规格书、SOP、检验标准。增量同步，文档升版后自动替换旧切片。
- **上传的产品手册（PDF / Markdown）**：PDF 经 MinerU 解析，图片由视觉模型理解。
- **ERP 实时数据**：产品主数据、当前 BOM 版本、ECN 记录，走参数化只读查询。
- **联网搜索（MCP）**：仅作为行业背景补充。

## 架构

```
                      ┌──────────── 导入图（LangGraph）────────────┐
PLM 同步 / 页面上传 → node_entry ─(pdf)→ node_pdf_to_md(MinerU) ─┐
                         └───────(md)───────────────────────────┴→ node_md_img(qwen-vl 图片理解 → MinIO)
                      → node_document_split(标题语义切分 + 递归细切 + 短块合并；父子两级)
                      → node_item_name_recognition(PLM 元数据优先，否则 LLM 识别 → item_name 集合)
                      → node_parent_store(父章节 → MySQL，预热 Redis)
                      → node_bge_embedding(BGE-M3 稠密+稀疏) → node_import_milvus(按 doc_key 幂等替换)

                      ┌──────────── 查询图（LangGraph）────────────┐
问题 → node_item_name_confirm(MongoDB 历史 + LLM 改写 + item_name 混合检索对齐；型号有歧义时反问)
     → 并行：node_search_embedding(混合检索) │ node_search_embedding_hyde(HyDE) │ node_web_search_mcp(MCP) │ node_erp_facts(ERP 只读)
     → node_rrf(加权 RRF) → node_rerank(bge-reranker + 超长压缩 + 动态 TopK + 父文档扩展 Redis→MySQL)
     → node_answer_output(带引用生成，SSE 流式，写 MongoDB)
```

| 组件 | 用途 |
|---|---|
| Milvus 2.6 | `chunks` 子块集合：HNSW(COSINE) 稠密向量 + SPARSE_INVERTED_INDEX(IP) 稀疏向量，item_name / doc_key 建 INVERTED 标量索引；`item_names` 主体集合 |
| BGE-M3 / bge-reranker-v2-m3 | 本地模型（`~/models`），稠密 + 稀疏向量；交叉编码重排序 |
| MySQL | `assistant.parent_document` 父章节原文；只读账号查询 `minierp` 业务库 |
| Redis | 父章节缓存，未命中回源 MySQL 并回填 |
| MongoDB | 会话历史（按 session_id + ts 索引）、知识库文档登记表 `kb_document` |
| MinIO | 原始文件（raw/）与文档图片（images/，匿名可读供前端展示） |
| DeepSeek | 问题改写、主体识别、HyDE、长文本压缩、回答生成 |
| qwen-vl-max | PDF 图片理解（曲线图、尺寸图、面板图） |
| MinerU | PDF → Markdown（版面、表格、图片） |
| DashScope WebSearch（MCP Streamable HTTP） | 联网搜索 |

## 关键设计

- **子块检索、父章节回答（Parent Document Retriever）**：子块小，检索定位准；父章节完整，喂给大模型的上下文不会被切碎。同一章节命中多个子块时只保留一次。
- **子块带文档上下文头**：31 份规格书结构完全相同，不加"【文档标题】章节"前缀，向量会高度相似，检索容易串型号。
- **主体对齐分级处理**：
  - 问题里直接写出库中存在的型号编码时，用规则确认。
  - 其他说法走向量对齐，高分直接确认。
  - 只有"说的是某个型号但分不清是哪个"时才反问用户。
  - 系列、工艺这类宽泛主体不收窄检索范围，交给 ERP 系列数据和全库检索。
- **ERP 数据优先**：规格书可能滞后于系统（例如 ECN 刚发布），BOM 版本、ECN 状态以 ERP 实时数据为准。只做参数化查询，不接受模型生成的 SQL。
- **增量同步与幂等**：以 `doc_key` 为粒度，Milvus、MySQL、Redis、MinIO 统一先删后写；PLM 版本号不变的文档跳过。
- **安全**：
  - 复用 MiniERP 的 JWT（jjwt 按密钥长度选 HS256/384/512，这里全部接受）。
  - 知识库维护仅限研发、质量和管理员；会话只能本人访问。
  - Milvus 过滤表达式做了转义，防注入；MinerU 结果解压时校验路径，防目录穿越。

## 运行

```bash
# 1. 基础设施（Milvus / MongoDB / MinIO / Redis / Attu）
docker compose up -d
# 2. MiniERP（MySQL、后端、前端）需已启动；首次在已有 MySQL 上执行建库授权
docker compose -f ../docker-compose.yml exec -T mysql mysql -uroot -proot < mysql-init/01_assistant.sql
# 3. 配置
cp .env.example .env   # 填写 LLM_API_KEY / VL_API_KEY / MCP_API_KEY / MINERU_API_TOKEN
uv sync
# 4. 启动查询服务 8090 + 导入服务 8091
uv run python main.py
```

在 MiniERP 前端的"技术助手 → 知识库管理"页面点击"同步 PLM 文档"并上传 `data/samples/` 下的 PDF 手册，然后到"产品技术问答"提问。Milvus 管理界面：http://localhost:3001 。

示例手册由 `uv run python scripts/make_sample_pdfs.py` 生成，参数与 MiniERP 数据库一致。

## 评估

```bash
uv run pytest -q                           # 单元测试
uv run python -m app.rag_eval.runner       # 检索消融 + 完整链路答案准确率
```

- **评测集**：由 `app/rag_eval/dataset.py` 从 MiniERP 数据库和手册原文自动生成，覆盖规格参数、ERP 实时数据、故障诊断、选型、应用、工艺、质量、变更八类。标准答案可复现，数据更新后可以重建。
- **判定标准**：答案必须包含全部期望关键词才算正确；检索以 gold 文档是否进入最终上下文计算 Hit@K 和 MRR。
- **报告位置**：`output/eval/report_*.md`。

---

# 经营与人力数据分析 Copilot（智能问数）

同一个服务目录下的第二个应用：用自然语言查制度、查经营和人力数据、问原因。代码在 `app/copilot/`，前端入口是菜单"智能问数"。
需求、架构和评测结果见 `docs/Copilot-01` 到 `Copilot-04`。

```
问题 → 读记忆 → route_intent（规则 → 意图缓存 → 模型 Function Calling + Few-shot）
        ├─ 制度问答 → search_policy（制度库混合检索）→ 带出处回答
        ├─ 经营问数 → prepare_template（24 个分析模板）─ 接不住 → generate_sql（口径 + Schema 裁剪）
        │             → validate_sql → apply_permission（行级、列级、打码、LIMIT）→ execute_sql → build_chart
        └─ 测算预警 → run_analysis（毛利归因 / 假设测算，代码计算）
      → answer（流式）→ finalize（记忆、会话历史、审计）
```

| 文件 | 作用 |
|---|---|
| `semantic/metrics.yaml` | 口径库：41 个指标（原子 → 派生） |
| `semantic/views.yaml` | 20 个白名单视图的字段说明，以及表级、行级、列级权限和打码规则 |
| `semantic/templates.yaml` | 24 个分析模板 |
| `pipeline.py` | 主流程：一个函数里的 if 和 while，把下面各步骤串起来（没有用 LangGraph） |
| `router.py` / `schemas.py` | 意图路由、追问合并、8 个工具的 Pydantic 参数定义 |
| `template_engine.py` / `text2sql.py` | 模板填参、SQL 生成 |
| `sqlguard.py` / `permission.py` | SQL 校验与改写、权限 |
| `analysis.py` | 毛利归因（价格、成本、销量、结构）和假设测算 |
| `memory.py` / `audit.py` / `export.py` | Redis 记忆与缓存、审计与反馈、导出（人工确认） |
| `eval/` | 评测集、评测运行器、性能对照 |

## 运行

```bash
# 1. 建只读白名单账号（只需一次；ERP 后端要先启动过，视图才存在）
docker compose -f ../docker-compose.yml exec -T mysql mysql -uroot -proot < mysql-init/02_copilot.sql
docker compose -f ../docker-compose.yml exec -T mysql mysql -uroot -proot < mysql-init/03_employee_pii.sql   # 模拟的身份证、银行卡、手机号，演示打码用
# 2. 启动（和技术助手一起：8090 查询、8091 导入、8093 Copilot）
uv run python main.py
#    或者只启动 Copilot
uv run python -m app.api.http.copilot_server
```

首次启动会建语义向量索引、导入 `data/policies/` 下的制度文档，大约 30 秒。之后打开 MiniERP 前端的"智能问数 → 经营与人力问数"。

## 测试、评测、压测

```bash
uv run pytest -q                                   # 单元测试（纯函数，不依赖数据库和模型）
uv run python -m app.copilot.eval.runner           # 348 题评测，报告在 output/eval/copilot_*.md
uv run python -m app.copilot.eval.bench            # 优化前后的首字响应和 Token 对照
TOKEN=$(uv run python scripts/mint_token.py admin ADMIN)
cd loadtest && jmeter -n -t copilot_query.jmx -Jtoken=$TOKEN -Jthreads=50 -Jloops=20 -l result.jtl -e -o report
```

## 说明

- `data/policies/` 里的 10 份制度是为这个模拟企业编写的示例，条款和 ERP 里的考勤、工资、审批规则保持一致，不是任何真实公司的制度。
- 员工的身份证号、工资卡号、手机号是按员工 ID 推算的模拟数据（见 `mysql-init/03_employee_pii.sql`，单独一张表，不改 ERP 原有的表），用来演示打码。
- 只做查询。提问里出现"修改、删除、审批"这类要求会被直接拒绝；模型生成的 SQL 用 `copilot_ro` 账号执行，这个账号只有白名单视图的 SELECT 权限。
