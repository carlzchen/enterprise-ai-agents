# MiniERP AI Assistant

**Enterprise knowledge-base Q&A (multimodal RAG) + email-driven multi-agent order workflow for a micro-motor manufacturer.**
Built with LangGraph, DeepAgents, Milvus, BGE-M3 and DeepSeek on top of a full ERP/PLM system (Java + Vue) that supplies realistic business data.

面向微特电机制造企业的两个 AI 模块：**企业知识库问答（多模态 RAG）** 与 **邮件驱动的跟单多 Agent 工作流**。底层是一套完整的 ERP / PLM 业务系统，为 AI 模块提供产品主数据、BOM、价格、订单与库存数据。

> 场景公司「科朗电机集团股份有限公司」为虚构名称；客户、供应商、人员、邮件与订单均为程序生成的演示数据。知识库评测使用的 30 份厂商公开技术手册因版权原因未包含在仓库中。

---

## 1. 企业知识库问答 Agent（多模态 RAG）

代码：[`assistant/app/kbase/`](assistant/app/kbase)

产品参数、技术手册、选型表和 CAD 图纸分散在大量文件中。员工用自然语言提问，系统返回附引用出处和原图的答案。

- **文档入库**：LangGraph 入库图按 PDF / 表格 / 图纸类型条件路由（MinerU、Docling、ezdxf），统一术语与单位，按章节切分；大模型逐块识别产品型号并回查原文证据，无证据不入库。datasheet 与选型表拆为「型号–参数–数值–单位」写入 MySQL，DXF 图纸提取尺寸、公差与 BOM 明细栏。
- **问答链路**：意图识别 → 主体确认（型号不全时反问）→ 参数类走 SQL 精确查询；说明类经 Query 改写、HyDE、稠密 + BM25 多路召回 → RRF 融合 → bge-reranker 重排 → 按型号、参数与配置筛选证据 → 带引用生成与数值校验。
- **企业级能力**：OIDC 登录后按部门、角色、密级在检索前过滤，未标注文档默认拒绝；切片级 SHA-256 增量更新，校验通过后在事务内切换生效；Redis 缓存与限流、模型故障降级、RabbitMQ 批量入库与重试、Langfuse 全链路追踪。

**评测结果**（1,000 题评测集，详见 [`docs/eval/知识库-1000题评测报告.md`](docs/eval/知识库-1000题评测报告.md)）

| 指标 | 结果 |
|---|---|
| 参数问答命中预期值 | 158/160（98.8%） |
| 技术手册问答内容正确 | 576/653（88.2%） |
| 技术手册问答目标文档引用命中 | 636/653（97.4%） |
| 三路召回（稠密 + HyDE + BM25）Hit@1 | 92.3% → 97.4% |
| 难题集（表格错列、漏检页面、型号配置混淆） | 31/48 → 46/48 |
| 自动化回归测试 | 130 项 |

## 2. 跟单 Agent 工作流（邮件驱动 · 多 Agent 协同）

代码：[`assistant/app/workflow/`](assistant/app/workflow)

询价、报价、议价、PO 与交期沟通都在邮件里。Agent 负责理解、抽取、评估与起草；确定性环节由状态机和规则推进；所有外发邮件经人工审批。

- **邮件理解**：大模型 + Pydantic 多意图分类与结构化抽取，校验失败带错误信息重试；低置信度或关键字段缺失的邮件进入人工确认队列。
- **非标询价多 Agent**：LangGraph StateGraph 编排技术、成本、交期、商务 4 个 Agent，成本与交期并行；规则审查不通过时只退回出错的 Agent；「需新开发」分支由 DeepAgents 预研节点检索知识库后给出方案。
- **流程与规则**：商机状态机 + RabbitMQ 发件箱事件驱动，以 Message-ID 与 PO 号保证幂等；选型、定价与审批路由由版本化规则引擎完成，金额不经过大模型。
- **人工审批**：外发邮件统一经过发件闸门，逐个核对数字来源后由人确认发送。

**评测结果**（详见 [`docs/eval/跟单工作流-邮件路由评测报告.md`](docs/eval/跟单工作流-邮件路由评测报告.md)）

| 指标 | 结果 |
|---|---|
| 三套留出集（210 封邮件）意图识别与任务拆分 | 210/210 |
| 交期 / 加急邮件引用的订单正确 | 65/66 |
| 54 封字段标注集关键字段抽取 | 91/92 |
| 12 个非标询价用例基础型号判断（多 Agent） | 11/12，优于单 Agent 与单次调用 |
| 自动化测试 | 43 项 |

## 设计原则

- 大模型只负责理解、抽取与起草；**数字由代码计算**，金额与价格不经过大模型。
- 结论必须**可回溯到原文**：回答附引用，数值逐项校验。
- **写操作经人工审批**：外发邮件、订单写入都需要人确认。
- **指标由评测脚本实际跑出**，不写没有实测依据的数字。

## 目录

```
assistant/            AI 模块（Python 3.12 · FastAPI · LangGraph）
  app/kbase/          知识库：入库图、查询图、权限、评测
  app/workflow/       跟单工作流：邮件理解、多 Agent、状态机、规则、评测
  data/               样例文档、DXF 图纸、评测集、演示邮件与 PO 附件
  tests/              自动化测试
backend/              ERP / PLM 业务系统（Java 21 · Spring Boot）
frontend/             前端（Vue 3 · Element Plus）
docs/                 需求、架构、实现说明与评测报告
```

## 运行

```bash
docker compose up -d --build                  # ERP 业务系统（MySQL + 后端 + 前端）
cd assistant
docker compose up -d                          # Milvus / MongoDB / MinIO / Redis
cp .env.example .env                          # 填写模型 API Key
uv sync
uv run python main.py                         # 查询服务 8090 + 导入服务 8091
uv run pytest -q                              # 自动化测试
```

更多说明见 [`assistant/README.md`](assistant/README.md) 与 [`docs/`](docs)；ERP 业务系统说明见 [`docs/MiniERP-业务系统说明.md`](docs/MiniERP-业务系统说明.md)。

## 技术栈

LangGraph · DeepAgents · Pydantic · MinerU / Docling · ezdxf · BGE-M3 · BM25 · bge-reranker-v2-m3 · Milvus · MySQL · MongoDB · MinIO · Redis · RabbitMQ · Langfuse · FastAPI · Vue 3 · Docker · DeepSeek
