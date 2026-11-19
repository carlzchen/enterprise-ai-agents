"""
Prometheus 指标（问数和知识库共用）。每个服务暴露 /metrics，Prometheus 定时来抓，Grafana 画图和告警。
指标名统一以 ai_ 开头，用 app 标签区分是问数（copilot）还是知识库（kbase）。
"""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

_BUCKETS = (0.1, 0.25, 0.5, 1, 1.5, 2.5, 4, 6, 9, 12, 20, 30, 60)
REQUESTS = Counter("ai_requests_total", "问答请求数", ["app", "route", "status"])
LATENCY = Histogram("ai_request_seconds", "一次问答从收到到答完的耗时", ["app", "route"], buckets=_BUCKETS)
FIRST_TOKEN = Histogram("ai_first_token_seconds", "从收到问题到吐出第一个字的耗时", ["app"], buckets=_BUCKETS)
STAGE = Histogram("ai_stage_seconds", "流程里每一步的耗时", ["app", "stage"], buckets=_BUCKETS)
DEGRADED = Counter("ai_degraded_total", "走了降级路径的次数", ["app", "kind"])
REJECTED = Counter("ai_rejected_total", "被限流或过载保护拒绝的请求数", ["app", "reason"])
BREAKER = Gauge("ai_breaker_open", "熔断器是否打开（1 打开 / 0 关闭）", ["name"])
BREAKER_TRIPS = Counter("ai_breaker_trips_total", "熔断器打开的次数", ["name"])
INFLIGHT = Gauge("ai_inflight", "正在占用某个资源池的请求数", ["pool"])
WAITING = Gauge("ai_waiting", "在某个资源池外排队的请求数", ["pool"])
CACHE = Counter("ai_cache_total", "缓存命中情况", ["app", "layer", "result"])
TOKENS = Counter("ai_llm_tokens_total", "大模型 token 用量", ["app", "kind"])
LLM_CALLS = Counter("ai_llm_calls_total", "大模型调用次数", ["app", "result"])
FEEDBACK = Counter("ai_feedback_total", "用户反馈", ["app", "rating"])
INGEST = Gauge("kb_ingest_jobs_today", "今天各状态的入库任务数", ["status"])
QUEUE = Gauge("kb_queue_messages", "入库队列里的消息数", ["queue"])
ALERTS = Gauge("ops_alerts_open", "未关闭的告警数", ["level"])
DEPENDENCY = Gauge("ai_dependency_up", "依赖是否可用（1 正常 / 0 不可用）", ["name"])


def install(app, refresh=None) -> None:
    """给 FastAPI 应用加上 /metrics。refresh 是抓取前要刷新一下的函数（队列长度这类不是事件驱动的数）"""
    from fastapi import Response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        if refresh:
            try:
                refresh()
            except Exception:
                pass
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
