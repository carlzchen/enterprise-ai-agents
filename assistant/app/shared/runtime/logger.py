"""
项目日志：loguru 控制台 + 按天滚动文件（logs/app_YYYYMMDD.log）。
提供两个装饰器：
  node_log("节点名")  —— LangGraph 节点，打印开始/完成/耗时/异常，自动带 task_id / session_id 作为追踪 ID
  step_log("步骤名")  —— 业务步骤，打印开始/完成/耗时/异常，不吞异常
"""
import os
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from loguru import logger as _logger

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = PROJECT_ROOT / "logs"
# trace_id 来自请求头 X-Trace-Id（前端 / ERP 后端传入，见 app/api/http/middleware.py），与 ERP 后端日志同一个追踪号
_FORMAT = ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <magenta>{extra[trace_id]}</magenta> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")


def _init():
    _logger.remove()
    _logger.configure(extra={"trace_id": "-"})
    _logger.add(sys.stdout, level=os.getenv("LOG_CONSOLE_LEVEL", "INFO").upper(), format=_FORMAT, enqueue=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _logger.add(LOG_DIR / "app_{time:YYYYMMDD}.log", level=os.getenv("LOG_FILE_LEVEL", "INFO").upper(),
                format=_FORMAT, rotation="00:00", retention=os.getenv("LOG_FILE_RETENTION", "7 days"),
                encoding="utf-8", enqueue=True)
    return _logger


logger = _init()


def _trace_id(state) -> str:
    if isinstance(state, Mapping):
        return str(state.get("session_id") or state.get("task_id") or "-")
    return "-"


def node_log(node_name: str):
    def deco(func):
        @wraps(func)
        def wrapper(state, *args, **kwargs):
            trace_id, start = _trace_id(state), time.time()
            logger.info(f"[{node_name}] 节点开始，追踪ID={trace_id}")
            try:
                result = func(state, *args, **kwargs)
                logger.info(f"[{node_name}] 节点完成，追踪ID={trace_id}，耗时={int((time.time() - start) * 1000)}ms")
                return result
            except Exception:
                logger.exception(f"[{node_name}] 节点异常，追踪ID={trace_id}")
                raise
        return wrapper
    return deco


def step_log(step_name: str):
    def deco(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            logger.debug(f"[{step_name}] 步骤开始")
            try:
                result = func(*args, **kwargs)
                logger.info(f"[{step_name}] 步骤完成，耗时={int((time.time() - start) * 1000)}ms")
                return result
            except Exception:
                logger.exception(f"[{step_name}] 步骤异常")
                raise
        return wrapper
    return deco
