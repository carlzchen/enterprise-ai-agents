"""
审计与反馈（MySQL assistant 库）。
  copilot_audit     每次提问一行：谁、什么角色、问了什么、识别成什么意图、执行了什么 SQL、返回多少行、耗时、Token、是否被拒绝、是否导出
  copilot_feedback  用户的点赞 / 点踩；点踩的记录就是 Bad Case 池
审计表只有服务账号能写，对话里的 SQL 用的是另一个只读账号，所以审计记录不可能被对话修改。
"""
import json

from app.copilot import db
from app.shared.runtime.logger import logger

_DDL = [
    """CREATE TABLE IF NOT EXISTS copilot_audit (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      trace_id VARCHAR(64), session_id VARCHAR(64), username VARCHAR(50) NOT NULL, role VARCHAR(20) NOT NULL,
      question VARCHAR(1000) NOT NULL, intent VARCHAR(30), tool VARCHAR(30), route_by VARCHAR(10), template_id VARCHAR(10),
      sql_text TEXT, row_count INT, status VARCHAR(12) NOT NULL COMMENT 'OK / DENIED / FAILED / CLARIFY',
      reason VARCHAR(500), scope_note VARCHAR(500), cache_hit TINYINT NOT NULL DEFAULT 0, repairs INT NOT NULL DEFAULT 0,
      elapsed_ms INT, first_token_ms INT, tokens_in INT, tokens_out INT, llm_calls INT,
      exported TINYINT NOT NULL DEFAULT 0, exported_at DATETIME, answer TEXT,
      INDEX idx_audit_user (username, created_at), INDEX idx_audit_status (status, created_at)
    ) COMMENT 'Copilot 查询审计'""",
    """CREATE TABLE IF NOT EXISTS copilot_feedback (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      audit_id BIGINT NOT NULL, username VARCHAR(50) NOT NULL, rating TINYINT NOT NULL COMMENT '1 赞 / -1 踩',
      comment VARCHAR(500), cause VARCHAR(30) COMMENT '分析后标注的原因分类', fixed TINYINT NOT NULL DEFAULT 0,
      UNIQUE KEY uk_fb (audit_id, username)
    ) COMMENT 'Copilot 反馈（点踩即 Bad Case）'""",
]
_ready = False


def ensure_tables() -> None:
    global _ready
    if _ready:
        return
    for ddl in _DDL:
        db.app_exec(ddl)
    _ready = True


def log(**f) -> int | None:
    """写一条审计。失败只记日志，不能因为审计写不进去而让用户拿不到回答；但会在日志里留下 ERROR 供排查"""
    try:
        ensure_tables()
        cols = ["trace_id", "session_id", "username", "role", "question", "intent", "tool", "route_by", "template_id", "sql_text",
                "row_count", "status", "reason", "scope_note", "cache_hit", "repairs", "elapsed_ms", "first_token_ms",
                "tokens_in", "tokens_out", "llm_calls", "answer"]
        vals = [f.get(c) for c in cols]
        for i, c in enumerate(cols):
            if c in ("question", "reason", "scope_note") and isinstance(vals[i], str):
                vals[i] = vals[i][:(1000 if c == "question" else 500)]
        return db.app_exec(f"INSERT INTO copilot_audit ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
    except Exception:
        logger.exception("写审计日志失败")
        return None


def mark_exported(audit_id: int) -> None:
    try:
        db.app_exec("UPDATE copilot_audit SET exported = 1, exported_at = NOW() WHERE id = %s", (audit_id,))
    except Exception:
        logger.exception("更新导出标记失败")


def list_audit(page: int = 1, size: int = 20, username: str | None = None, status: str | None = None, keyword: str | None = None) -> dict:
    ensure_tables()
    where, args = ["1 = 1"], []
    if username:
        where.append("username = %s")
        args.append(username)
    if status:
        where.append("status = %s")
        args.append(status)
    if keyword:
        where.append("question LIKE %s")
        args.append(f"%{keyword}%")
    cond = " AND ".join(where)
    total = db.app_query(f"SELECT COUNT(*) AS n FROM copilot_audit WHERE {cond}", args)[0]["n"]
    size = max(1, min(size, 100))
    rows = db.app_query(f"SELECT * FROM copilot_audit WHERE {cond} ORDER BY id DESC LIMIT %s OFFSET %s", [*args, size, (max(page, 1) - 1) * size])
    return {"total": total, "items": json.loads(json.dumps(rows, default=str, ensure_ascii=False))}


def stats() -> dict:
    ensure_tables()
    row = db.app_query("""SELECT COUNT(*) AS total, SUM(status = 'OK') AS ok, SUM(status = 'DENIED') AS denied,
        SUM(status = 'FAILED') AS failed, SUM(cache_hit) AS cache_hits, SUM(exported) AS exported,
        ROUND(AVG(elapsed_ms)) AS avg_ms, ROUND(AVG(tokens_in + tokens_out)) AS avg_tokens FROM copilot_audit""")[0]
    return {k: (float(v) if v is not None else 0) for k, v in row.items()}


def feedback(audit_id: int, username: str, rating: int, comment: str | None) -> None:
    ensure_tables()
    owner = db.app_query("SELECT username FROM copilot_audit WHERE id = %s", (audit_id,))
    if not owner or owner[0]["username"] != username:
        raise PermissionError("只能评价自己的提问")
    db.app_exec("INSERT INTO copilot_feedback (audit_id, username, rating, comment) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE rating = VALUES(rating), comment = VALUES(comment)", (audit_id, username, rating, (comment or "")[:500]))


def bad_cases(limit: int = 100) -> list[dict]:
    ensure_tables()
    rows = db.app_query("""SELECT f.id, f.created_at, f.comment, f.cause, f.fixed, a.id AS audit_id, a.username, a.role, a.question,
        a.intent, a.tool, a.template_id, a.sql_text, a.status, a.reason, a.answer
        FROM copilot_feedback f JOIN copilot_audit a ON a.id = f.audit_id WHERE f.rating < 0 ORDER BY f.id DESC LIMIT %s""", (min(limit, 500),))
    return json.loads(json.dumps(rows, default=str, ensure_ascii=False))


def mark_bad_case(feedback_id: int, cause: str | None, fixed: bool) -> None:
    db.app_exec("UPDATE copilot_feedback SET cause = %s, fixed = %s WHERE id = %s", ((cause or "")[:30], 1 if fixed else 0, feedback_id))
