"""
告警中心（问数和知识库共用）：规则触发 → 去重 → 分级 → 通知（飞书机器人 + 邮件发件箱）→ 人工确认 / 自动恢复。
  - 同一个问题（dedup_key 相同）只保留一条，重复触发只累加次数，不重复通知
  - 一直没恢复的，每隔 ALERT_RENOTIFY_MINUTES 再提醒一次
  - 问题消失后调用 resolve，告警自动关闭并发一条「已恢复」
"""
import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta

import httpx

from app.infra.config.settings import settings
from app.kbase import store
from app.shared.runtime.logger import logger

LEVELS = {"P1": "严重", "P2": "重要", "P3": "提示"}
_DDL = """CREATE TABLE IF NOT EXISTS ops_alert (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, source VARCHAR(20) NOT NULL COMMENT 'kbase / copilot / platform', rule VARCHAR(60) NOT NULL,
  level VARCHAR(4) NOT NULL, title VARCHAR(200) NOT NULL, detail TEXT, dedup_key VARCHAR(160) NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
  hits INT NOT NULL DEFAULT 1, first_at DATETIME NOT NULL, last_at DATETIME NOT NULL, notified_at DATETIME, notify_result VARCHAR(200),
  acked_by VARCHAR(60), closed_at DATETIME, UNIQUE KEY uk_open (dedup_key, status, first_at), KEY idx_status (status, last_at)
) COMMENT '运行告警'"""
_ready = False


def ensure() -> None:
    global _ready
    if not _ready:
        store.execute(_DDL)
        _ready = True


def _sign(timestamp: str) -> str:
    key = f"{timestamp}\n{settings.feishu_secret}".encode()
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def send_feishu(title: str, text: str, level: str = "P2") -> str:
    """推送到飞书自定义机器人。返回结果说明；没配置时返回「未配置」"""
    if not settings.feishu_webhook:
        return "未配置飞书"
    body = {"msg_type": "interactive", "card": {
        "header": {"title": {"tag": "plain_text", "content": title[:100]}, "template": {"P1": "red", "P2": "orange", "P3": "blue", "OK": "green"}.get(level, "blue")},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text[:2000]}}]}}
    if settings.feishu_secret:
        ts = str(int(time.time()))
        body.update(timestamp=ts, sign=_sign(ts))
    try:
        r = httpx.post(settings.feishu_webhook, json=body, timeout=8, trust_env="127.0.0.1" not in settings.feishu_webhook)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.status_code == 200 and data.get("code", data.get("StatusCode", 0)) == 0
        return "飞书已送达" if ok else f"飞书返回异常：{r.status_code} {str(data)[:120]}"
    except Exception as e:
        return f"飞书发送失败：{str(e)[:120]}"


def _notify(row: dict, again: bool = False) -> None:
    from app.kbase.ingest import notify
    head = f"[{LEVELS.get(row['level'], row['level'])}] {row['title']}" + ("（仍未恢复）" if again else "")
    text = f"**来源** {row['source']} · {row['rule']}\n**首次发生** {row['first_at']}　**累计** {row['hits']} 次\n\n{row['detail'] or ''}"
    result = send_feishu(head, text, row["level"])
    if row["level"] in ("P1", "P2"):
        notify.enqueue(f"alert:{row['id']}:{int(again)}:{datetime.now():%Y%m%d%H}", None, f"[告警] {head}", text.replace("**", ""))
    store.execute("UPDATE ops_alert SET notified_at = NOW(), notify_result = %s WHERE id = %s", (result[:200], row["id"]))
    logger.warning(f"[alert] {head} → {result}")


def fire(source: str, rule: str, level: str, title: str, detail: str = "", dedup_key: str | None = None) -> int:
    """触发一条告警。同一个 dedup_key 还没关闭时只累加次数"""
    ensure()
    key = (dedup_key or f"{source}:{rule}")[:160]
    now = datetime.now().replace(microsecond=0)
    row = store.one("SELECT * FROM ops_alert WHERE dedup_key = %s AND status IN ('OPEN', 'ACK') ORDER BY id DESC LIMIT 1", (key,))
    if row:
        store.execute("UPDATE ops_alert SET hits = hits + 1, last_at = %s, detail = %s WHERE id = %s", (now, detail, row["id"]))
        due = row["notified_at"] is None or now - row["notified_at"] >= timedelta(minutes=settings.alert_renotify_minutes)
        if row["status"] == "OPEN" and due:
            _notify({**row, "hits": row["hits"] + 1, "detail": detail}, again=row["notified_at"] is not None)
        return row["id"]
    store.execute("INSERT INTO ops_alert (source, rule, level, title, detail, dedup_key, first_at, last_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (source, rule, level, title[:200], detail, key, now, now))
    row = store.one("SELECT * FROM ops_alert WHERE dedup_key = %s AND status = 'OPEN' ORDER BY id DESC LIMIT 1", (key,))
    _notify(row)
    return row["id"]


def resolve(dedup_key: str, note: str = "已自动恢复") -> bool:
    """问题消失：关闭告警并通知一次"""
    ensure()
    row = store.one("SELECT * FROM ops_alert WHERE dedup_key = %s AND status IN ('OPEN', 'ACK') ORDER BY id DESC LIMIT 1", (dedup_key[:160],))
    if not row:
        return False
    store.execute("UPDATE ops_alert SET status = 'CLOSED', closed_at = NOW() WHERE id = %s", (row["id"],))
    send_feishu(f"[已恢复] {row['title']}", f"{note}\n持续时间：{row['first_at']} 至 {datetime.now():%Y-%m-%d %H:%M:%S}，期间触发 {row['hits']} 次", "OK")
    logger.info(f"[alert] 已恢复：{row['title']}")
    return True


def ack(alert_id: int, username: str) -> None:
    store.execute("UPDATE ops_alert SET status = 'ACK', acked_by = %s WHERE id = %s AND status = 'OPEN'", (username, alert_id))


def close(alert_id: int, username: str) -> None:
    store.execute("UPDATE ops_alert SET status = 'CLOSED', acked_by = COALESCE(acked_by, %s), closed_at = NOW() WHERE id = %s AND status <> 'CLOSED'", (username, alert_id))


def listing(limit: int = 200) -> list[dict]:
    ensure()
    return store.query("SELECT * FROM ops_alert ORDER BY FIELD(status, 'OPEN', 'ACK', 'CLOSED'), last_at DESC LIMIT %s", (limit,))
