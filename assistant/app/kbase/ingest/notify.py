"""
告警邮件：发件箱模式。
业务节点只往 kb_outbox 写一行，不直接发邮件；独立的发送器定时读发件箱发出。
这样邮件服务器出问题不会拖累入库流程，恢复后自动补发。邮件里只放文档名、任务号、错误摘要，不放文档正文。
"""
import smtplib
import threading
import time
from email.header import Header
from email.mime.text import MIMEText

from app.infra.config.settings import settings
from app.kbase import store
from app.shared.runtime.logger import logger

MAX_TRIES = 5


def enqueue(dedup_key: str, to_email: str | None, subject: str, body: str) -> bool:
    """写发件箱。dedup_key 相同的只会有一封（同一个任务只通知一次）"""
    n = store.execute("INSERT IGNORE INTO kb_outbox (dedup_key, to_email, subject, body) VALUES (%s,%s,%s,%s)",
                      (dedup_key[:120], to_email or settings.kbase_admin_email, subject[:200], body))
    return bool(n)


def _send(to_email: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"], msg["To"] = settings.smtp_from, to_email
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        if settings.smtp_tls:
            s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.sendmail(settings.smtp_from, [to_email], msg.as_string())


def send_pending(limit: int = 20) -> dict:
    """发出待发邮件。没有配置 SMTP 时什么都不做，邮件留在发件箱里"""
    if not settings.smtp_host:
        return {"sent": 0, "failed": 0, "skipped": "没有配置 SMTP_HOST"}
    sent = failed = 0
    for row in store.query("SELECT * FROM kb_outbox WHERE status = 'PENDING' ORDER BY id LIMIT %s", (limit,)):
        try:
            _send(row["to_email"], row["subject"], row["body"])
            store.execute("UPDATE kb_outbox SET status = 'SENT', sent_at = NOW(), tries = tries + 1 WHERE id = %s", (row["id"],))
            sent += 1
        except Exception as e:
            failed += 1
            status = "FAILED" if row["tries"] + 1 >= MAX_TRIES else "PENDING"
            store.execute("UPDATE kb_outbox SET tries = tries + 1, last_error = %s, status = %s WHERE id = %s", (str(e)[:500], status, row["id"]))
            logger.warning(f"[kbase] 邮件发送失败（第 {row['tries'] + 1} 次）：{e}")
    return {"sent": sent, "failed": failed}


_started = False


def start_sender(interval: int = 30) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                send_pending()
            except Exception:
                logger.exception("[kbase] 发件器异常")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="kb-outbox").start()
