"""同时启动问数 Copilot（8093）、金蝶 EAS 模拟网关（8096）和知识库服务（8094）：python main.py
日常使用更推荐 ./scripts/kbase.sh start（后台运行、带日志）"""
import threading

import uvicorn

from app.infra.config.settings import settings


def _serve(app_path: str, port: int, host: str | None = None):
    uvicorn.run(app_path, host=host or settings.app_host, port=port, log_level="info")


if __name__ == "__main__":
    threading.Thread(target=_serve, args=("app.kbase.kingdee.mock_gateway:app", settings.kingdee_mock_port, "127.0.0.1"), daemon=True).start()
    threading.Thread(target=_serve, args=("app.api.http.copilot_server:app", settings.copilot_app_port), daemon=True).start()
    _serve("app.kbase.server:app", settings.kbase_app_port)
