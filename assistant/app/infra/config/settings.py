"""集中读取 .env 配置。所有模块通过 settings 访问配置，不直接读环境变量。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from app.shared.runtime.logger import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


def _s(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _b(key: str, default: bool = False) -> bool:
    return _s(key, str(default)).lower() in ("1", "true", "yes")


def _path(key: str) -> str:
    v = _s(key)
    return str(Path(v).expanduser()) if v else ""


@dataclass(frozen=True)
class Settings:
    app_host: str = field(default_factory=lambda: _s("APP_HOST", "0.0.0.0"))
    jwt_secret: str = field(default_factory=lambda: _s("MINIERP_JWT_SECRET"))
    minierp_api_base: str = field(default_factory=lambda: _s("MINIERP_API_BASE", "http://localhost:8080/api"))
    minierp_sync_user: str = field(default_factory=lambda: _s("MINIERP_SYNC_USER"))
    minierp_sync_password: str = field(default_factory=lambda: _s("MINIERP_SYNC_PASSWORD"))

    llm_base_url: str = field(default_factory=lambda: _s("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _s("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _s("LLM_MODEL", "deepseek-chat"))
    # 备用模型：主模型熔断时退到它（自建 vLLM 时通常是一个更小的本地模型；不要填外部 API，数据会出公司）
    llm_fallback_base_url: str = field(default_factory=lambda: _s("LLM_FALLBACK_BASE_URL"))
    llm_fallback_api_key: str = field(default_factory=lambda: _s("LLM_FALLBACK_API_KEY", "EMPTY"))
    llm_fallback_model: str = field(default_factory=lambda: _s("LLM_FALLBACK_MODEL"))
    # 透传给模型服务的额外参数（JSON）。vLLM + Qwen3 时用它关掉思考模式：{"chat_template_kwargs": {"enable_thinking": false}}
    llm_extra_body: str = field(default_factory=lambda: _s("LLM_EXTRA_BODY"))
    llm_timeout: int = field(default_factory=lambda: int(_s("LLM_TIMEOUT", "60")))
    llm_concurrency: int = field(default_factory=lambda: int(_s("LLM_CONCURRENCY", "16")))          # 同时最多几路请求打到大模型
    copilot_rate_per_minute: int = field(default_factory=lambda: int(_s("COPILOT_RATE_PER_MINUTE", "30")))
    copilot_concurrency: int = field(default_factory=lambda: int(_s("COPILOT_CONCURRENCY", "16")))
    kbase_rate_per_minute: int = field(default_factory=lambda: int(_s("KBASE_RATE_PER_MINUTE", "30")))
    kbase_concurrency: int = field(default_factory=lambda: int(_s("KBASE_CONCURRENCY", "8")))
    llm_temperature: float = field(default_factory=lambda: float(_s("LLM_TEMPERATURE", "0.1")))
    vl_base_url: str = field(default_factory=lambda: _s("VL_BASE_URL"))
    vl_api_key: str = field(default_factory=lambda: _s("VL_API_KEY"))
    vl_model: str = field(default_factory=lambda: _s("VL_MODEL", "qwen-vl-max"))

    mcp_enabled: bool = field(default_factory=lambda: _b("MCP_WEB_SEARCH_ENABLED", True))
    mcp_base_url: str = field(default_factory=lambda: _s("MCP_BASE_URL"))
    mcp_api_key: str = field(default_factory=lambda: _s("MCP_API_KEY"))
    mineru_base_url: str = field(default_factory=lambda: _s("MINERU_BASE_URL", "https://mineru.net/api/v4"))
    mineru_api_token: str = field(default_factory=lambda: _s("MINERU_API_TOKEN"))

    bge_m3_path: str = field(default_factory=lambda: _path("BGE_M3_PATH"))
    bge_device: str = field(default_factory=lambda: _s("BGE_DEVICE", "cpu"))
    bge_fp16: bool = field(default_factory=lambda: _b("BGE_FP16"))
    reranker_path: str = field(default_factory=lambda: _path("BGE_RERANKER_PATH"))
    reranker_device: str = field(default_factory=lambda: _s("BGE_RERANKER_DEVICE", "cpu"))
    reranker_fp16: bool = field(default_factory=lambda: _b("BGE_RERANKER_FP16"))

    milvus_url: str = field(default_factory=lambda: _s("MILVUS_URL", "http://127.0.0.1:19531"))
    chunks_collection: str = field(default_factory=lambda: _s("CHUNKS_COLLECTION", "minierp_kb_chunks"))
    item_name_collection: str = field(default_factory=lambda: _s("ITEM_NAME_COLLECTION", "minierp_kb_item_names"))

    mongo_url: str = field(default_factory=lambda: _s("MONGO_URL", "mongodb://127.0.0.1:27018"))
    mongo_db: str = field(default_factory=lambda: _s("MONGO_DB_NAME", "minierp_assistant"))

    minio_endpoint: str = field(default_factory=lambda: _s("MINIO_ENDPOINT", "127.0.0.1:9010"))
    minio_access_key: str = field(default_factory=lambda: _s("MINIO_ACCESS_KEY", "minioadmin"))
    minio_secret_key: str = field(default_factory=lambda: _s("MINIO_SECRET_KEY", "minioadmin"))
    minio_bucket: str = field(default_factory=lambda: _s("MINIO_BUCKET_NAME", "minierp-kb"))
    minio_secure: bool = field(default_factory=lambda: _b("MINIO_SECURE"))
    minio_public_base: str = field(default_factory=lambda: _s("MINIO_PUBLIC_BASE", "http://localhost:9010"))

    mysql_host: str = field(default_factory=lambda: _s("MYSQL_HOST", "127.0.0.1"))
    mysql_port: int = field(default_factory=lambda: int(_s("MYSQL_PORT", "3307")))
    mysql_user: str = field(default_factory=lambda: _s("MYSQL_USER", "assistant"))
    mysql_password: str = field(default_factory=lambda: _s("MYSQL_PASSWORD", "assistant"))
    mysql_database: str = field(default_factory=lambda: _s("MYSQL_DATABASE", "assistant"))
    erp_database: str = field(default_factory=lambda: _s("ERP_DATABASE", "minierp"))
    redis_url: str = field(default_factory=lambda: _s("REDIS_URL", "redis://127.0.0.1:6381/0"))

    # 经营与人力数据分析 Copilot（app/copilot）
    copilot_app_port: int = field(default_factory=lambda: int(_s("COPILOT_APP_PORT", "8093")))
    copilot_db_user: str = field(default_factory=lambda: _s("COPILOT_DB_USER", "copilot_ro"))          # 只能读白名单视图
    copilot_db_password: str = field(default_factory=lambda: _s("COPILOT_DB_PASSWORD", "copilot_ro"))
    copilot_today: str = field(default_factory=lambda: _s("COPILOT_TODAY"))                            # 评测时固定"今天"，如 2026-10-06

    # 企业知识库（app/kbase）
    kbase_app_port: int = field(default_factory=lambda: int(_s("KBASE_APP_PORT", "8094")))
    kbase_collection: str = field(default_factory=lambda: _s("KBASE_COLLECTION", "kb_chunks_v3"))
    kbase_admin_email: str = field(default_factory=lambda: _s("KBASE_ADMIN_EMAIL", "kb-admin@example.com"))   # 找不到文档负责人时的兜底收件人
    kbase_subject_llm: bool = field(default_factory=lambda: _b("KBASE_SUBJECT_LLM", "1"))           # 新增切片用大模型识别主题（关掉则只用规则）
    kbase_hyde: bool = field(default_factory=lambda: _b("KBASE_HYDE", "1"))                         # 说明类问题生成假设答案参与召回
    kbase_context_budget: int = field(default_factory=lambda: int(_s("KBASE_CONTEXT_BUDGET", "32000")))   # 生成时的上下文 token 预算（Qwen3-32B 32K）
    kbase_answer_reserve: int = field(default_factory=lambda: int(_s("KBASE_ANSWER_RESERVE", "2048")))     # 给回答预留的 token
    kbase_image_ttl: int = field(default_factory=lambda: int(_s("KBASE_IMAGE_TTL", "600")))          # 图片临时签名链接的有效秒数
    kbase_events_queue: str = field(default_factory=lambda: _s("KBASE_EVENTS_QUEUE", "kb.events"))
    # 公司 SSO（OIDC）和内部目录（SCIM）。本地开发指向模拟网关 8096；知识库只在内网和 VPN 网段提供服务
    sso_issuer: str = field(default_factory=lambda: _s("SSO_ISSUER", "http://127.0.0.1:8096/idp"))
    sso_client_id: str = field(default_factory=lambda: _s("SSO_CLIENT_ID", "kbase"))
    sso_client_secret: str = field(default_factory=lambda: _s("SSO_CLIENT_SECRET", "mock-kbase-secret"))
    sso_redirect_uri: str = field(default_factory=lambda: _s("SSO_REDIRECT_URI", "http://127.0.0.1:8094/auth/sso/callback"))
    sso_after_login: str = field(default_factory=lambda: _s("SSO_AFTER_LOGIN", "http://localhost:5180/kbase"))   # 登录完成后回到的前端页面
    sso_scim_url: str = field(default_factory=lambda: _s("SSO_SCIM_URL", "http://127.0.0.1:8096/scim/v2"))
    sso_scim_token: str = field(default_factory=lambda: _s("SSO_SCIM_TOKEN", "mock-scim-token"))
    sso_principal_ttl: int = field(default_factory=lambda: int(_s("SSO_PRINCIPAL_TTL", "28800")))       # 身份缓存 = 知识库登录凭证有效期（8 小时）
    sso_stale_ttl: int = field(default_factory=lambda: int(_s("SSO_STALE_TTL", "604800")))              # 目录不可用时，旧身份最多沿用多久
    kbase_allowed_networks: str = field(default_factory=lambda: _s("KBASE_ALLOWED_NETWORKS", "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"))  # 内网 + VPN 网段
    kbase_trusted_proxies: str = field(default_factory=lambda: _s("KBASE_TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12"))     # 只信任这些反向代理传来的 X-Forwarded-For
    # 金蝶 EAS WebService。本地开发时指向模拟网关（app/kbase/kingdee/mock_gateway.py），上线时换成真实地址和集成账号
    kingdee_base_url: str = field(default_factory=lambda: _s("KINGDEE_BASE_URL", "http://127.0.0.1:8096"))
    kingdee_username: str = field(default_factory=lambda: _s("KINGDEE_USERNAME", "kb_sync"))
    kingdee_password: str = field(default_factory=lambda: _s("KINGDEE_PASSWORD", "mock-password"))
    kingdee_sln_name: str = field(default_factory=lambda: _s("KINGDEE_SLN_NAME", "eas"))            # 解决方案名
    kingdee_dc_name: str = field(default_factory=lambda: _s("KINGDEE_DC_NAME", "mock-dc"))          # 数据中心（账套）
    kingdee_language: str = field(default_factory=lambda: _s("KINGDEE_LANGUAGE", "L2"))             # L2 = 简体中文
    kingdee_db_type: int = field(default_factory=lambda: int(_s("KINGDEE_DB_TYPE", "1")))           # 数据库类型，按 EAS 环境填
    kingdee_facade: str = field(default_factory=lambda: _s("KINGDEE_FACADE", "WSKbMasterDataFacade"))   # EAS 侧发布的只读查询服务名
    # 向量模型和重排模型的独立服务（app/platform/model_server.py，放在 GPU 机器上）。留空则在本进程里用 CPU 跑
    model_server_url: str = field(default_factory=lambda: _s("MODEL_SERVER_URL"))
    # 销售与采购跟单 Workflow（app/workflow）
    workflow_app_port: int = field(default_factory=lambda: int(_s("WORKFLOW_APP_PORT", "8095")))
    workflow_mail_dir: str = field(default_factory=lambda: _s("WORKFLOW_MAIL_DIR", "data/workflow_mailbox/inbox"))      # 本地模拟邮箱：一个放 eml 文件的目录
    workflow_imap_host: str = field(default_factory=lambda: _s("WORKFLOW_IMAP_HOST"))                                # 配了就改用 IMAP 读真实邮箱
    workflow_imap_user: str = field(default_factory=lambda: _s("WORKFLOW_IMAP_USER"))
    workflow_imap_password: str = field(default_factory=lambda: _s("WORKFLOW_IMAP_PASSWORD"))
    workflow_erp_write: bool = field(default_factory=lambda: _b("WORKFLOW_ERP_WRITE"))                               # 是否允许写 ERP；关着时生成「请人工导入」的待办
    workflow_agents: bool = field(default_factory=lambda: _s("WORKFLOW_AGENTS", "true").lower() == "true")                 # 非标询价是否交给多 Agent 评估
    workflow_confidence: float = field(default_factory=lambda: float(_s("WORKFLOW_CONFIDENCE", "0.75")))             # 低于它进待确认队列
    # 外部系统适配器（app/workflow/adapters）：mock 用本地实现跑通全流程，其他值切到真实系统
    workflow_email_adapter: str = field(default_factory=lambda: _s("WORKFLOW_EMAIL_ADAPTER", "imap" if _s("WORKFLOW_IMAP_HOST") else "mock"))  # mock / graph / imap
    workflow_erp_adapter: str = field(default_factory=lambda: _s("WORKFLOW_ERP_ADAPTER", "mock"))                    # mock（MiniERP 库当作模拟的星空）/ kingdee
    workflow_task_adapter: str = field(default_factory=lambda: _s("WORKFLOW_TASK_ADAPTER", "mock"))                  # mock（待办中心）/ feishu
    workflow_knowledge_adapter: str = field(default_factory=lambda: _s("WORKFLOW_KNOWLEDGE_ADAPTER", "mock"))        # mock（产品主数据检索）/ rag（项目一知识库）
    workflow_order_mailboxes: str = field(default_factory=lambda: _s("WORKFLOW_ORDER_MAILBOXES", "orders@kelang.example.com,market@kelang.example.com"))
    workflow_review_max_rounds: int = field(default_factory=lambda: int(_s("WORKFLOW_REVIEW_MAX_ROUNDS", "2")))       # 审核 Agent 驳回后最多重写几次
    workflow_mock_seed: int = field(default_factory=lambda: int(_s("WORKFLOW_MOCK_SEED", "42")))                     # 模拟数据的随机种子
    graph_cloud: str = field(default_factory=lambda: _s("GRAPH_CLOUD", "global"))                                    # global / china（世纪互联）
    graph_tenant_id: str = field(default_factory=lambda: _s("GRAPH_TENANT_ID"))
    graph_client_id: str = field(default_factory=lambda: _s("GRAPH_CLIENT_ID"))
    graph_cert_path: str = field(default_factory=lambda: _s("GRAPH_CERT_PATH"))
    kingdee_base_url: str = field(default_factory=lambda: _s("KINGDEE_BASE_URL"))                                   # 金蝶云·星空 WebAPI
    kingdee_acct_id: str = field(default_factory=lambda: _s("KINGDEE_ACCT_ID"))
    kingdee_app_id: str = field(default_factory=lambda: _s("KINGDEE_APP_ID"))
    kingdee_app_secret: str = field(default_factory=lambda: _s("KINGDEE_APP_SECRET"))
    kingdee_username: str = field(default_factory=lambda: _s("KINGDEE_USERNAME"))
    kingdee_lcid: int = field(default_factory=lambda: int(_s("KINGDEE_LCID", "2052")))
    feishu_app_id: str = field(default_factory=lambda: _s("FEISHU_APP_ID"))                                          # 飞书自建应用（派任务用，和告警机器人分开）
    feishu_app_secret: str = field(default_factory=lambda: _s("FEISHU_APP_SECRET"))
    feishu_tasklist_guid: str = field(default_factory=lambda: _s("FEISHU_TASKLIST_GUID"))
    # 链路追踪（Langfuse）。不填就不记录，问答不受影响
    langfuse_host: str = field(default_factory=lambda: _s("LANGFUSE_HOST"))
    langfuse_public_url: str = field(default_factory=lambda: _s("LANGFUSE_PUBLIC_URL"))        # 浏览器里打开用的地址，和 host 不同时才填
    langfuse_public_key: str = field(default_factory=lambda: _s("LANGFUSE_PUBLIC_KEY"))
    langfuse_secret_key: str = field(default_factory=lambda: _s("LANGFUSE_SECRET_KEY"))
    langfuse_project_id: str = field(default_factory=lambda: _s("LANGFUSE_PROJECT_ID", "minierp-ai"))
    # 消息队列（RabbitMQ）：批量入库、导出、通知等离线任务。连不上时退回进程内执行
    rabbitmq_url: str = field(default_factory=lambda: _s("RABBITMQ_URL", "amqp://minierp:minierp-mq@127.0.0.1:5673/%2F"))
    kbase_workers: int = field(default_factory=lambda: int(_s("KBASE_WORKERS", "2")))                  # 入库消费线程数
    kbase_inbox: str = field(default_factory=lambda: _s("KBASE_INBOX", "data/kbase_inbox"))            # 共享目录：定时扫描增量入库
    kbase_batch_fail_ratio: float = field(default_factory=lambda: float(_s("KBASE_BATCH_FAIL_RATIO", "0.2")))   # 一批里失败比例超过它就暂停
    # 飞书自定义机器人（告警）。留空时只记告警中心和邮件，不推飞书
    feishu_webhook: str = field(default_factory=lambda: _s("FEISHU_WEBHOOK"))
    feishu_secret: str = field(default_factory=lambda: _s("FEISHU_SECRET"))
    alert_renotify_minutes: int = field(default_factory=lambda: int(_s("ALERT_RENOTIFY_MINUTES", "60")))    # 告警一直没恢复，多久再提醒一次
    kingdee_mock_port: int = field(default_factory=lambda: int(_s("KINGDEE_MOCK_PORT", "8096")))
    # 告警邮件（SMTP）。不配置主机时只写发件箱，不发送
    smtp_host: str = field(default_factory=lambda: _s("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_s("SMTP_PORT", "25")))
    smtp_user: str = field(default_factory=lambda: _s("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _s("SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: _s("SMTP_FROM", "kb-noreply@example.com"))
    smtp_tls: bool = field(default_factory=lambda: _b("SMTP_TLS"))
    # Neo4j 产品知识图谱（可选；连不上时问答自动跳过图谱这一路）
    neo4j_url: str = field(default_factory=lambda: _s("NEO4J_URL", "bolt://127.0.0.1:7688"))
    neo4j_user: str = field(default_factory=lambda: _s("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _s("NEO4J_PASSWORD", "minierp-kg"))
    oda_converter: str = field(default_factory=lambda: _path("ODA_CONVERTER"))      # ODA File Converter 可执行文件路径，DWG 转 DXF 用


settings = Settings()
