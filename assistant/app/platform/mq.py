"""
RabbitMQ 封装（问数和知识库共用）。只用来跑离线任务，在线问答不经过队列。

队列设计（以入库为例）：
  kb.ingest            工作队列：一条消息 = 一个文件的入库任务，消费者处理完手动确认
  kb.ingest.retry.30   延迟重试队列：消息在里面躺 30 秒，到期后自动回到 kb.ingest（利用消息过期 + 死信转发实现延迟）
  kb.ingest.retry.120  同上，2 分钟
  kb.ingest.retry.600  同上，10 分钟
  kb.ingest.dlq        死信队列：重试用完仍然失败的任务放这里，等人处理
消息持久化 + 发布确认 + 手动确认：进程崩了，没确认的消息会被重新投递，不会丢。
"""
import json
import threading
import time
from typing import Callable

import pika

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

EXCHANGE = "minierp.tasks"
DELAYS = [30, 120, 600]
_local = threading.local()
_declared: set[str] = set()
_lock = threading.Lock()


def _connect() -> pika.BlockingConnection:
    params = pika.URLParameters(settings.rabbitmq_url)
    params.heartbeat, params.blocked_connection_timeout, params.socket_timeout = 600, 30, 5
    params.connection_attempts, params.retry_delay = 1, 0
    return pika.BlockingConnection(params)


def _channel():
    """每个线程一条连接（pika 的连接不能跨线程用）"""
    ch = getattr(_local, "ch", None)
    if ch is None or ch.is_closed or ch.connection.is_closed:
        conn = _connect()
        ch = conn.channel()
        ch.confirm_delivery()
        _local.ch = ch
    return ch


def declare(queue: str) -> None:
    """声明一个工作队列和它配套的重试队列、死信队列。重复调用没有副作用"""
    with _lock:
        if queue in _declared:
            return
        ch = _channel()
        ch.exchange_declare(EXCHANGE, "direct", durable=True)
        ch.queue_declare(queue, durable=True, arguments={"x-max-priority": 10})
        ch.queue_bind(queue, EXCHANGE, queue)
        ch.queue_declare(f"{queue}.dlq", durable=True)
        ch.queue_bind(f"{queue}.dlq", EXCHANGE, f"{queue}.dlq")
        for d in DELAYS:
            name = f"{queue}.retry.{d}"
            ch.queue_declare(name, durable=True, arguments={"x-message-ttl": d * 1000, "x-dead-letter-exchange": EXCHANGE, "x-dead-letter-routing-key": queue})
            ch.queue_bind(name, EXCHANGE, name)
        _declared.add(queue)


def publish(queue: str, body: dict, *, delay: int | None = None, dead: bool = False, priority: int = 5) -> None:
    """delay 取 30 / 120 / 600 之一时进对应的延迟队列；dead=True 进死信队列"""
    declare(queue)
    key = f"{queue}.dlq" if dead else f"{queue}.retry.{delay}" if delay else queue
    for attempt in (1, 2):
        try:
            _channel().basic_publish(EXCHANGE, key, json.dumps(body, ensure_ascii=False).encode(),
                                     pika.BasicProperties(delivery_mode=2, content_type="application/json", priority=priority), mandatory=True)
            return
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.ChannelError, pika.exceptions.StreamLostError):
            _local.ch = None
            if attempt == 2:
                raise


def available() -> bool:
    try:
        declare("kb.ingest")
        return True
    except Exception:
        _local.ch = None
        return False


def depth(queue: str) -> dict:
    """各队列里还有多少条消息（监控和页面展示用）"""
    out = {}
    try:
        ch = _channel()
        for name in (queue, *[f"{queue}.retry.{d}" for d in DELAYS], f"{queue}.dlq"):
            out[name.replace(queue, "").lstrip(".") or "ready"] = ch.queue_declare(name, passive=True).method.message_count
    except Exception:
        _local.ch = None
    return out


def purge_dead(queue: str) -> list[dict]:
    """把死信队列里的消息全部取出来（人工重试时用）"""
    out, ch = [], _channel()
    while True:
        method, _, body = ch.basic_get(f"{queue}.dlq", auto_ack=True)
        if method is None:
            return out
        out.append(json.loads(body))


def consume(queue: str, handler: Callable[[dict], None], *, name: str = "worker", prefetch: int = 1, stop: threading.Event | None = None) -> None:
    """阻塞消费。handler 正常返回就确认；抛异常则不确认并重新入队（只在处理函数自己崩溃时发生，业务失败由 handler 内部决定去向）。
    连接断了自动重连。"""
    while not (stop and stop.is_set()):
        try:
            conn = _connect()
            ch = conn.channel()
            declare(queue)
            ch.basic_qos(prefetch_count=prefetch)
            logger.info(f"[mq] {name} 开始消费 {queue}")
            for method, _, body in ch.consume(queue, inactivity_timeout=2):
                if stop and stop.is_set():
                    break
                if method is None:
                    continue
                try:
                    handler(json.loads(body))
                    ch.basic_ack(method.delivery_tag)
                except Exception:
                    logger.exception(f"[mq] {name} 处理消息时崩溃，消息重新入队")
                    ch.basic_nack(method.delivery_tag, requeue=True)
                    time.sleep(2)
            conn.close()
        except Exception as e:
            logger.warning(f"[mq] {name} 连接断开，5 秒后重连：{str(e)[:120]}")
            time.sleep(5)
