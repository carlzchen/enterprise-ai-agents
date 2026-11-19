"""
知识库的 SQL 底座（MySQL assistant 库）。原则：SQL 是真相源，Milvus 是可以随时重建的派生索引。

  kb_document      文档的稳定身份（doc_key 来自 PLM 文档号 / 图号，不是文件名）；active_version 是当前生效版本
  kb_doc_version   每个版本一行，状态机：PARSED → STAGED → VERIFIED → ACTIVE → SUPERSEDED / FAILED
  kb_chunk         每个切片一行，是和 Milvus 对账的依据。chunk_id = md5(doc_key|首次出现的版本|序号)；
                   norm_hash 是归一化文本（去页码、页眉页脚、空白）的 SHA-256，新版本里 norm_hash 没变的切片复用原来那一行；
                   state：PENDING 暂存（检索看不到）/ VALID 生效 / INVALID 已失效；valid_from / valid_to 是生效的版本区间；
                   metas 记每个版本下的序号、章节、页码、父章节（切换或回滚时按版本取）；subject 是主题识别结果
  kb_parent        父章节原文（回答时喂给模型的完整上下文）
  kb_param         从 datasheet / 选型表拆出来的结构化参数：型号—参数名—数值—单位
  kb_drawing*      图纸的标题栏、尺寸与公差、明细表
  kb_alert         入库时发现的业务问题（图纸与 ERP BOM 不一致等）
  kb_ingest_job    导入任务：阶段、重试次数、下次重试时间、错误
  kb_event         持久化的通知事件（版本切换、入库失败、对账异常）：和业务变更在同一个事务里写，再由转发器投递到 RabbitMQ
  kb_outbox        邮件发件箱：RabbitMQ 消费者把事件渲染成邮件写到这里，由发送器经 SMTP 发出（按事件号去重）
  kb_image         文档图片：私有存储在 MinIO，按文档和生效版本关联，展示时现签临时链接
  kb_compensation  补偿任务：导入中断后清理 MinIO、Milvus 里的残留；退避重试，耗尽后告警
  kb_audit_log     审计：SSO 登录与注销、工号映射到金蝶用户后查询 ERP 数据、身份解析异常
  kb_kd_user       工号（来自 SSO）→ 金蝶 EAS 用户的映射
  kb_answer_log    每次回答的记录：引用了哪些切片和版本、各步耗时
  kb_feedback      点赞 / 点踩，点踩即 Bad Case
  kd_material/bom  金蝶物料和 BOM 的本地镜像（由 kingdee/sync.py 同步）
"""
import json
import threading
from contextlib import contextmanager

import pymysql

from app.infra.config.settings import settings

_DDL = [
    """CREATE TABLE IF NOT EXISTS kb_document (
      doc_key VARCHAR(80) PRIMARY KEY, title VARCHAR(300) NOT NULL, doc_type VARCHAR(20) NOT NULL,
      source VARCHAR(20) NOT NULL COMMENT 'PLM / UPLOAD / FOLDER',
      security_level VARCHAR(12) NOT NULL COMMENT 'PUBLIC 全员 / INTERNAL 内部 / CONFIDENTIAL 机密 / SECRET 绝密',
      acl_groups VARCHAR(300) NOT NULL COMMENT '可见范围标记，逗号分隔：ALL 全员、角色名、D:部门编号（含下属部门）',
      owner_email VARCHAR(120), product_code VARCHAR(60), series VARCHAR(30),
      active_version VARCHAR(16) NULL COMMENT '当前生效版本，查询以它为准',
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) COMMENT '知识库文档'""",
    """CREATE TABLE IF NOT EXISTS kb_doc_version (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL,
      file_name VARCHAR(300), file_sha256 CHAR(64), file_type VARCHAR(10), route VARCHAR(10) COMMENT 'doc / table / cad',
      status VARCHAR(12) NOT NULL, chunk_count INT NOT NULL DEFAULT 0, parent_count INT NOT NULL DEFAULT 0,
      param_count INT NOT NULL DEFAULT 0, parsed_by VARCHAR(30), raw_url VARCHAR(500), error VARCHAR(1000),
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, activated_at DATETIME NULL, superseded_at DATETIME NULL,
      UNIQUE KEY uk_ver (doc_key, version), INDEX idx_sha (file_sha256)
    ) COMMENT '文档版本'""",
    """CREATE TABLE IF NOT EXISTS kb_chunk (
      chunk_id CHAR(32) PRIMARY KEY COMMENT 'md5(doc_key|version|chunk_seq)，确定性主键',
      doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL, chunk_seq INT NOT NULL, parent_id CHAR(32),
      section VARCHAR(300), page INT, chunk_hash CHAR(32) NOT NULL COMMENT '内容哈希，对账用', item_name VARCHAR(200),
      content MEDIUMTEXT NOT NULL,
      UNIQUE KEY uk_seq (doc_key, version, chunk_seq)
    ) COMMENT '切片（对账依据）'""",
    """CREATE TABLE IF NOT EXISTS kb_parent (
      parent_id CHAR(32) PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL, seq INT NOT NULL,
      title VARCHAR(300), page INT, content MEDIUMTEXT NOT NULL, INDEX idx_pv (doc_key, version)
    ) COMMENT '父章节原文'""",
    """CREATE TABLE IF NOT EXISTS kb_param (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL,
      model VARCHAR(80) NOT NULL COMMENT '型号（已归一化）', param_key VARCHAR(40) NOT NULL COMMENT '标准参数名',
      param_name VARCHAR(80) NOT NULL COMMENT '原表里的参数名', value_num DECIMAL(20,6) NULL, value_text VARCHAR(200),
      unit VARCHAR(20), source_ref VARCHAR(200) COMMENT '出处：工作表/行，或章节',
      INDEX idx_model (model, param_key), INDEX idx_dv (doc_key, version)
    ) COMMENT '结构化参数：型号—参数名—数值—单位'""",
    """CREATE TABLE IF NOT EXISTS kb_drawing (
      doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL, dwg_no VARCHAR(80), name VARCHAR(200), rev VARCHAR(16),
      scale VARCHAR(16), designer VARCHAR(40), draw_date VARCHAR(20), product_code VARCHAR(60),
      erp_material VARCHAR(60) COMMENT '关联到的金蝶物料编码', PRIMARY KEY (doc_key, version)
    ) COMMENT '图纸标题栏'""",
    """CREATE TABLE IF NOT EXISTS kb_drawing_dim (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL,
      feature VARCHAR(40) NOT NULL, label VARCHAR(60) NOT NULL, value DECIMAL(16,4), tol_plus DECIMAL(10,4), tol_minus DECIMAL(10,4),
      unit VARCHAR(10) NOT NULL DEFAULT 'mm', INDEX idx_dv (doc_key, version)
    ) COMMENT '图纸尺寸与公差'""",
    """CREATE TABLE IF NOT EXISTS kb_drawing_bom (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL, seq INT,
      material_code VARCHAR(60), material_name VARCHAR(200), qty DECIMAL(16,4), erp_matched TINYINT NOT NULL DEFAULT 0,
      INDEX idx_dv (doc_key, version)
    ) COMMENT '图纸明细表'""",
    """CREATE TABLE IF NOT EXISTS kb_alert (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80), version VARCHAR(16), kind VARCHAR(30) NOT NULL,
      message VARCHAR(1000) NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'OPEN', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_alert (doc_key, version, kind, message(200))
    ) COMMENT '入库时发现的业务问题'""",
    """CREATE TABLE IF NOT EXISTS kb_ingest_job (
      job_id VARCHAR(40) PRIMARY KEY, doc_key VARCHAR(80), version VARCHAR(16), file_name VARCHAR(300), file_path VARCHAR(600),
      meta JSON, stage VARCHAR(20) NOT NULL, status VARCHAR(12) NOT NULL COMMENT 'RUNNING / RETRY / DONE / SKIPPED / FAILED',
      attempts INT NOT NULL DEFAULT 0, next_retry_at DATETIME NULL, retryable TINYINT NULL, error VARCHAR(1000),
      started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL, INDEX idx_status (status, next_retry_at)
    ) COMMENT '导入任务'""",
    """CREATE TABLE IF NOT EXISTS kb_outbox (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, dedup_key VARCHAR(120) NOT NULL, to_email VARCHAR(120) NOT NULL,
      subject VARCHAR(200) NOT NULL, body TEXT NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'PENDING',
      tries INT NOT NULL DEFAULT 0, last_error VARCHAR(500), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, sent_at DATETIME NULL,
      UNIQUE KEY uk_dedup (dedup_key)
    ) COMMENT '邮件发件箱'""",
    """CREATE TABLE IF NOT EXISTS kb_answer_log (
      answer_id CHAR(32) PRIMARY KEY, session_id VARCHAR(64), trace_id VARCHAR(64), username VARCHAR(50), role VARCHAR(20),
      question TEXT, rewritten TEXT, route VARCHAR(12), status VARCHAR(12), chunk_ids JSON, doc_versions JSON, latency JSON,
      answer MEDIUMTEXT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, INDEX idx_user (username, created_at)
    ) COMMENT '回答记录'""",
    """CREATE TABLE IF NOT EXISTS kb_feedback (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, answer_id CHAR(32) NOT NULL, username VARCHAR(50) NOT NULL, rating TINYINT NOT NULL,
      reason VARCHAR(30), correct_answer TEXT, comment VARCHAR(500), status VARCHAR(10) NOT NULL DEFAULT 'NEW',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_fb (answer_id, username)
    ) COMMENT '反馈；点踩即 Bad Case'""",
    """CREATE TABLE IF NOT EXISTS kd_material (
      f_number VARCHAR(60) PRIMARY KEY COMMENT '物料编码 FNumber', f_name VARCHAR(200), f_specification VARCHAR(200) COMMENT '规格型号',
      f_group VARCHAR(60) COMMENT '物料分组', f_erp_cls VARCHAR(10) COMMENT '物料属性：自制 / 外购 / 委外', f_unit VARCHAR(20),
      f_forbid CHAR(1), f_modify_date DATETIME, synced_at DATETIME NOT NULL
    ) COMMENT '金蝶 EAS 物料本地镜像'""",
    """CREATE TABLE IF NOT EXISTS kd_bom (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, f_bom_number VARCHAR(80) COMMENT 'BOM 版本号 FNumber', f_parent VARCHAR(60) NOT NULL,
      f_child VARCHAR(60) NOT NULL, f_child_name VARCHAR(200), f_numerator DECIMAL(18,6), f_denominator DECIMAL(18,6), f_unit VARCHAR(20),
      synced_at DATETIME NOT NULL, INDEX idx_parent (f_parent), INDEX idx_child (f_child)
    ) COMMENT '金蝶 EAS 物料清单本地镜像'""",
]
_ready = False
_lock = threading.Lock()


def _connect():
    return pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user, password=settings.mysql_password,
                           database=settings.mysql_database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                           autocommit=True, connect_timeout=5, read_timeout=30)


_BATCH_DDL = """CREATE TABLE IF NOT EXISTS kb_batch (
  batch_id VARCHAR(40) PRIMARY KEY, source VARCHAR(20) NOT NULL COMMENT 'INBOX 共享目录 / PLM / ZIP 压缩包 / MANUAL',
  title VARCHAR(200), trigger_by VARCHAR(60), status VARCHAR(12) NOT NULL COMMENT 'RUNNING / PAUSED / DONE / PARTIAL / CANCELLED',
  total INT NOT NULL DEFAULT 0, done INT NOT NULL DEFAULT 0, skipped INT NOT NULL DEFAULT 0, failed INT NOT NULL DEFAULT 0,
  pending INT NOT NULL DEFAULT 0, pause_reason VARCHAR(300), mode VARCHAR(10) NOT NULL DEFAULT 'MQ' COMMENT 'MQ 走消息队列 / LOCAL 队列不可用时进程内执行',
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL, KEY idx_started (started_at)
) COMMENT '批量入库批次'"""


_V3_DDL = [
    """CREATE TABLE IF NOT EXISTS kb_event (
      event_id CHAR(32) PRIMARY KEY, kind VARCHAR(30) NOT NULL COMMENT 'VERSION_SWITCHED / INGEST_FAILED / RECONCILE_BROKEN / DRAWING_MISMATCH / ...',
      doc_key VARCHAR(80), version VARCHAR(16), payload JSON NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'NEW' COMMENT 'NEW / PUBLISHED / DONE',
      tries INT NOT NULL DEFAULT 0, last_error VARCHAR(300), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at DATETIME NULL,
      handled_at DATETIME NULL, INDEX idx_status (status, created_at)
    ) COMMENT '持久化通知事件'""",
    """CREATE TABLE IF NOT EXISTS kb_image (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL, sha CHAR(16) NOT NULL,
      object_key VARCHAR(300) NOT NULL, mime VARCHAR(40), bytes INT, summary TEXT, parent_seq INT NULL COMMENT '图片所在的章节序号',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_img (doc_key, version, sha), INDEX idx_sha (sha)
    ) COMMENT '文档图片（MinIO 私有存储）'""",
    """CREATE TABLE IF NOT EXISTS kb_compensation (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, job_id VARCHAR(40), doc_key VARCHAR(80) NOT NULL, version VARCHAR(16) NOT NULL,
      kind VARCHAR(20) NOT NULL COMMENT 'CLEAN_STAGED 清理中断导入的残留 / SYNC_STATE 把 SQL 的状态同步到 Milvus',
      status VARCHAR(10) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / DONE / FAILED', attempts INT NOT NULL DEFAULT 0,
      next_run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, result JSON NULL, error VARCHAR(500),
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL,
      UNIQUE KEY uk_task (doc_key, version, kind), INDEX idx_due (status, next_run_at)
    ) COMMENT '补偿任务'""",
    """CREATE TABLE IF NOT EXISTS kb_audit_log (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, user_id VARCHAR(64), employee_no VARCHAR(40),
      kd_user VARCHAR(60), action VARCHAR(40) NOT NULL, target VARCHAR(200), detail JSON, result VARCHAR(20),
      INDEX idx_user (user_id, ts), INDEX idx_action (action, ts)
    ) COMMENT '审计日志'""",
    """CREATE TABLE IF NOT EXISTS kb_kd_user (
      employee_no VARCHAR(40) PRIMARY KEY, kd_user VARCHAR(60) NOT NULL COMMENT '金蝶 EAS 用户编码', kd_org VARCHAR(60),
      enabled TINYINT NOT NULL DEFAULT 1, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) COMMENT '工号 → 金蝶用户'""",
]


def _has_col(cur, table: str, col: str) -> bool:
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s", (table, col))
    return bool(cur.fetchone()["n"])


def _has_index(cur, table: str, index: str) -> bool:
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s", (table, index))
    return bool(cur.fetchone()["n"])


def _migrate_v3(cur) -> None:
    """切片级增量、生效区间、数字密级、图纸卡片、提示词版本"""
    for ddl in _V3_DDL:
        cur.execute(ddl)
    if not _has_col(cur, "kb_document", "sec_level"):
        cur.execute("ALTER TABLE kb_document ADD COLUMN sec_level TINYINT NULL COMMENT '数字密级：0 公开 / 1 内部 / 2 机密 / 3 绝密' AFTER security_level")
        cur.execute("UPDATE kb_document SET sec_level = CASE security_level WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1 WHEN 'CONFIDENTIAL' THEN 2 WHEN 'SECRET' THEN 3 END")
    if not _has_col(cur, "kb_chunk", "state"):
        cur.execute("ALTER TABLE kb_chunk ADD COLUMN norm_hash CHAR(64) NULL COMMENT '归一化文本的 SHA-256，判断切片是否变化', "
                    "ADD COLUMN state VARCHAR(10) NOT NULL DEFAULT 'VALID' COMMENT 'PENDING / VALID / INVALID', "
                    "ADD COLUMN valid_from VARCHAR(16) NULL, ADD COLUMN valid_to VARCHAR(16) NULL, "
                    "ADD COLUMN series VARCHAR(30) NULL, ADD COLUMN subject JSON NULL COMMENT '主题识别结果和证据', "
                    "ADD COLUMN metas JSON NULL COMMENT '{版本: {seq, section, page, parent_id}}'")
        # 已有数据：生效版本的切片是 VALID，被替换下来的版本是 INVALID
        cur.execute("UPDATE kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key SET c.valid_from = c.version, "
                    "c.state = IF(d.active_version = c.version, 'VALID', 'INVALID'), c.valid_to = IF(d.active_version = c.version, NULL, d.active_version), "
                    "c.metas = JSON_OBJECT(c.version, JSON_OBJECT('seq', c.chunk_seq, 'section', c.section, 'page', c.page, 'parent_id', c.parent_id))")
    if _has_index(cur, "kb_chunk", "uk_seq"):               # 复用的切片会出现在多个版本里，(文档, 版本, 序号) 不再唯一
        cur.execute("ALTER TABLE kb_chunk DROP INDEX uk_seq, ADD INDEX idx_doc_state (doc_key, state), ADD INDEX idx_norm (doc_key, norm_hash)")
    if not _has_col(cur, "kb_drawing", "card"):
        cur.execute("ALTER TABLE kb_drawing ADD COLUMN card JSON NULL COMMENT '图纸结构化卡片', ADD COLUMN needs_review TINYINT NOT NULL DEFAULT 0, "
                    "ADD COLUMN review_note VARCHAR(1000) NULL")
    if not _has_col(cur, "kb_doc_version", "diff"):
        cur.execute("ALTER TABLE kb_doc_version ADD COLUMN diff JSON NULL COMMENT '切片比对：新增 / 沿用 / 失效的数量'")
    if not _has_col(cur, "kb_answer_log", "prompt_version"):
        cur.execute("ALTER TABLE kb_answer_log ADD COLUMN prompt_version VARCHAR(60) NULL, ADD COLUMN scope_key VARCHAR(20) NULL")


def _migrate(cur) -> None:
    """给早期建好的表补字段"""
    cur.execute(_BATCH_DDL)
    _migrate_v3(cur)
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'kb_chunk' AND index_name = 'ft_content'")
    if not cur.fetchone()["n"]:                              # 中文全文索引（按 2 个字切词），向量库不可用时降级检索用
        cur.execute("ALTER TABLE kb_chunk ADD FULLTEXT INDEX ft_content (content) WITH PARSER ngram")
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'kb_ingest_job' AND column_name = 'batch_id'")
    if not cur.fetchone()["n"]:
        cur.execute("ALTER TABLE kb_ingest_job ADD COLUMN batch_id VARCHAR(40) NULL, ADD KEY idx_batch (batch_id, status)")


def ensure_tables() -> None:
    global _ready
    with _lock:
        if _ready:
            return
        with _connect() as c, c.cursor() as cur:
            for ddl in _DDL:
                cur.execute(ddl)
            _migrate(cur)
        _ready = True


def query(sql: str, args=None) -> list[dict]:
    ensure_tables()
    with _connect() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def one(sql: str, args=None) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=None) -> int:
    ensure_tables()
    with _connect() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid or cur.rowcount


def executemany(sql: str, rows: list) -> None:
    if not rows:
        return
    ensure_tables()
    with _connect() as c, c.cursor() as cur:
        cur.executemany(sql, rows)


@contextmanager
def transaction():
    """一个事务里做多步修改：要么全部生效，要么全部回滚（版本切换用）"""
    ensure_tables()
    conn = _connect()
    conn.autocommit(False)
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)
