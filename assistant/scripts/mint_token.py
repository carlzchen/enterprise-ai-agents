"""
签发一个本地测试用的 JWT（和 MiniERP 后端同一个密钥），压测和接口调试时用，免得把密码写进脚本。
用法：uv run python scripts/mint_token.py hr01 HR [有效小时数]
只在本地开发环境使用；生产环境的密钥不应该出现在开发机上。
"""
import sys
import time
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.infra.config.settings import settings  # noqa: E402

if __name__ == "__main__":
    username, role = sys.argv[1], sys.argv[2]
    hours = float(sys.argv[3]) if len(sys.argv) > 3 else 2
    alg = "HS512" if len(settings.jwt_secret.encode()) >= 64 else "HS384" if len(settings.jwt_secret.encode()) >= 48 else "HS256"
    print(jwt.encode({"sub": username, "role": role, "exp": int(time.time() + hours * 3600)}, settings.jwt_secret, algorithm=alg))
