"""
高并发保护和降级用的几样工具（问数和知识库共用）：

  限流 rate_limit      每个用户每分钟最多问多少次（Redis 计数；Redis 不可用时放行）
  隔离舱 Bulkhead      某一步同时最多跑几个、最多排几个；排满了立刻拒绝，不让请求堆到超时（重排、大模型、数据库各一个）
  熔断器 Breaker       某个依赖连续失败几次就「断开」一段时间，期间不再去碰它、直接走降级；到时间后放一个请求试探，成功就恢复
  超时 run_with_timeout 给一步操作设最长等待时间
  缓存 cache_get/set   Redis 里放 JSON；Redis 不可用时当作没命中

这些工具只负责「发现问题、拦住问题」；具体降级成什么，由各业务流程自己决定。
"""
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager

from app.infra.config.settings import settings
from app.platform import metrics
from app.shared.runtime.logger import logger


class Unavailable(RuntimeError):
    """依赖不可用（熔断中、过载或超时）。业务代码捕获它来走降级"""

    def __init__(self, name: str, reason: str):
        super().__init__(f"{name}：{reason}")
        self.name, self.reason = name, reason


class TooBusy(RuntimeError):
    """入口处被限流或过载保护拒绝"""

    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------------------------------------------- 熔断器

_breakers: dict[str, "Breaker"] = {}


class Breaker:
    def __init__(self, name: str, title: str, fail_max: int = 5, reset_seconds: int = 30):
        self.name, self.title, self.fail_max, self.reset_seconds = name, title, fail_max, reset_seconds
        self.failures, self.opened_at, self.state = 0, 0.0, "CLOSED"
        self._lock = threading.Lock()
        _breakers[name] = self
        metrics.BREAKER.labels(name).set(0)

    def allow(self) -> bool:
        with self._lock:
            if self.state == "OPEN" and time.time() - self.opened_at >= self.reset_seconds:
                self.state = "HALF_OPEN"                      # 到时间了：放一个请求进去试探
                return True
            return self.state != "OPEN"

    def success(self) -> None:
        with self._lock:
            if self.state != "CLOSED":
                logger.info(f"[breaker] {self.title} 已恢复")
            self.failures, self.state = 0, "CLOSED"
            metrics.BREAKER.labels(self.name).set(0)

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state == "HALF_OPEN" or self.failures >= self.fail_max:
                if self.state != "OPEN":
                    logger.warning(f"[breaker] {self.title} 连续失败 {self.failures} 次，熔断 {self.reset_seconds} 秒")
                    metrics.BREAKER_TRIPS.labels(self.name).inc()
                self.state, self.opened_at = "OPEN", time.time()
                metrics.BREAKER.labels(self.name).set(1)

    def call(self, fn, *args, **kwargs):
        if not self.allow():
            raise Unavailable(self.title, "熔断中")
        try:
            out = fn(*args, **kwargs)
        except Unavailable:
            raise
        except Exception as e:
            self.failure()
            raise Unavailable(self.title, f"{type(e).__name__}: {str(e)[:120]}") from e
        self.success()
        return out


def breakers() -> list[dict]:
    return [{"name": b.name, "title": b.title, "state": b.state, "failures": b.failures, "reset_seconds": b.reset_seconds} for b in _breakers.values()]


# ---------------------------------------------------------------- 隔离舱

class Bulkhead:
    def __init__(self, name: str, title: str, limit: int, queue: int, wait_seconds: float):
        self.name, self.title, self.limit, self.queue, self.wait_seconds = name, title, limit, queue, wait_seconds
        self._sem = threading.Semaphore(limit)
        self._waiting, self._lock = 0, threading.Lock()

    @contextmanager
    def enter(self):
        with self._lock:
            if self._waiting >= self.queue:
                raise Unavailable(self.title, "排队已满")
            self._waiting += 1
            metrics.WAITING.labels(self.name).set(self._waiting)
        got = self._sem.acquire(timeout=self.wait_seconds)
        with self._lock:
            self._waiting -= 1
            metrics.WAITING.labels(self.name).set(self._waiting)
        if not got:
            raise Unavailable(self.title, f"排队超过 {self.wait_seconds:g} 秒")
        metrics.INFLIGHT.labels(self.name).inc()
        try:
            yield
        finally:
            metrics.INFLIGHT.labels(self.name).dec()
            self._sem.release()


# ---------------------------------------------------------------- 超时

_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="guarded")


def run_with_timeout(name: str, seconds: float, fn, *args, **kwargs):
    """超时后调用方不再等；后台那次调用会自己跑完，结果丢弃。上下文变量（当前链路）带到工作线程里，里面的 @observe 才能挂到同一条链路上"""
    import contextvars
    future = _pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout:
        raise TimeoutError(f"{name} 超过 {seconds:g} 秒没有返回") from None


# ---------------------------------------------------------------- Redis：限流和缓存

_redis = None
_redis_down_until = 0.0


def redis_client():
    global _redis, _redis_down_until
    if time.time() < _redis_down_until:
        return None
    if _redis is None:
        import redis
        _redis = redis.Redis.from_url(settings.redis_url, socket_timeout=1.5, socket_connect_timeout=1.5, decode_responses=True)
    return _redis


def _redis_failed(e: Exception) -> None:
    global _redis_down_until
    _redis_down_until = time.time() + 30
    logger.warning(f"[redis] 不可用，30 秒内限流和缓存不生效：{str(e)[:80]}")


def rate_limit(app: str, user: str, per_minute: int) -> None:
    """固定窗口计数：同一个用户一分钟内超过 per_minute 次就拒绝"""
    r = redis_client()
    if r is None:
        return
    key = f"rl:{app}:{user}:{int(time.time() // 60)}"
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 70)
    except Exception as e:
        return _redis_failed(e)
    if n > per_minute:
        metrics.REJECTED.labels(app, "rate_limit").inc()
        raise TooBusy(f"提问太频繁了（每分钟最多 {per_minute} 次），请稍后再试", 60 - int(time.time() % 60))


def cache_key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def cache_get(app: str, layer: str, key: str):
    r = redis_client()
    if r is None:
        return None
    try:
        raw = r.get(f"cache:{app}:{layer}:{key}")
    except Exception as e:
        return _redis_failed(e)
    metrics.CACHE.labels(app, layer, "hit" if raw else "miss").inc()
    return json.loads(raw) if raw else None


def cache_set(app: str, layer: str, key: str, value, ttl: int) -> None:
    r = redis_client()
    if r is None:
        return
    try:
        r.set(f"cache:{app}:{layer}:{key}", json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
    except Exception as e:
        _redis_failed(e)


def counter(name: str, bump: bool = False) -> int:
    """一个全局递增的版本号（比如知识库内容版本）：内容一变就加一，缓存的 key 里带上它，旧缓存自然失效"""
    r = redis_client()
    if r is None:
        return int(time.time() // 60)                         # Redis 不可用：用分钟数当版本，等于缓存最多活一分钟
    try:
        return int(r.incr(f"ver:{name}") if bump else (r.get(f"ver:{name}") or 0))
    except Exception as e:
        _redis_failed(e)
        return int(time.time() // 60)


# ---------------------------------------------------------------- 入口准入

class Gate:
    """一个服务的入口闸门：先按用户限流，再看整体并发，超了直接告诉用户稍后再试"""

    def __init__(self, app: str, per_minute: int, concurrent: int):
        self.app, self.per_minute, self.concurrent = app, per_minute, concurrent
        self._n, self._lock = 0, threading.Lock()

    @contextmanager
    def enter(self, user: str):
        rate_limit(self.app, user, self.per_minute)
        with self._lock:
            if self._n >= self.concurrent:
                metrics.REJECTED.labels(self.app, "overload").inc()
                raise TooBusy("当前提问的人比较多，请过几秒再试", 5)
            self._n += 1
            metrics.INFLIGHT.labels(f"{self.app}_requests").set(self._n)
        try:
            yield
        finally:
            with self._lock:
                self._n -= 1
                metrics.INFLIGHT.labels(f"{self.app}_requests").set(self._n)
