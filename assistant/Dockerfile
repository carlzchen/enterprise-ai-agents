# 问数、知识库、跟单三个 Python 服务共用一个镜像，启动哪个由 APP_MODULE 决定（见 deploy/kbase/docker-compose.yml）
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    HF_HOME=/models/hf TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 tzdata && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

# 先只装依赖（依赖不变时这一层走缓存）。版本以 uv.lock 为准；
# torch 换成 CPU 版：这个服务只在 CPU 上跑向量和重排（线上大模型和视觉模型走 vLLM 服务），默认的 Linux 版 torch 会带上十几 G 的 CUDA 依赖
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/req.txt \
 && TORCH=$(grep -E '^(torch|torchvision|torchaudio)==' /tmp/req.txt | cut -d' ' -f1 | tr '\n' ' ') \
 && grep -v -E '^(torch|torchvision|torchaudio|triton|nvidia-[a-z0-9-]+)==' /tmp/req.txt > /tmp/req-cpu.txt \
 && uv venv /app/.venv \
 && uv pip install --python /app/.venv/bin/python --index-url https://download.pytorch.org/whl/cpu $TORCH \
 && uv pip install --python /app/.venv/bin/python -r /tmp/req-cpu.txt \
 && rm -rf /root/.cache

COPY app ./app
COPY scripts ./scripts
COPY data/kbase_eval/dataset.json ./data/kbase_eval/dataset.json
RUN useradd -r -u 10001 -m kb && mkdir -p /app/output /app/logs /models && chown -R kb /app/output /app/logs /models
USER kb
ENV PATH=/app/.venv/bin:$PATH APP_MODULE=app.kbase.server:app PORT=8094 WORKERS=1
EXPOSE 8094
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s CMD curl -fs http://127.0.0.1:${PORT}/health || exit 1
# 知识库服务里有定时任务和入库消费线程：一个容器一个进程，要扩容就多起几个容器（定时任务靠 Redis 锁只跑一份）
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT} --workers ${WORKERS} --proxy-headers --forwarded-allow-ips='*'"]
