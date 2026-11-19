"""
并发压测：模拟多个人同时提问，看在限流和过载保护下系统的表现。
用法：PYTHONPATH=. .venv/bin/python scripts/loadtest.py kbase|copilot [并发人数=20] [每人提问数=5] [参数题占比=0.7]
输出：成功、被限流、出错各多少；成功请求的 P50 / P95；有多少走了降级、多少命中缓存。结果同时写到 output/loadtest/。
每个虚拟用户用自己的账号名（各自一份限流额度），问题从固定的题库里轮着取。
"""
import json
import random
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.infra.config.settings import settings  # noqa: E402

MODELS = ["KL-SV-60-200W", "KL-SV-80-400W", "KL-SV-80-750W", "KL-SV-110-1200W", "KL-SV-130-1500W", "KL-SV-130-2000W", "KL-ST-42x48", "KL-BL-60W-310"]
PARAMS = ["额定转矩", "额定转速", "额定功率", "防护等级", "绝缘等级", "额定电压", "噪声", "效率"]
KB_DESC = ["伺服电机过热报警怎么排查", "AL.060 是什么报警怎么处理", "定子浸漆的工艺要求是什么", "步进电机失步了怎么办", "来料检验磁钢要查哪些项目", "伺服电机安装时要注意什么"]
CP = ["上个月各产线出勤率", "今年各月销售额", "各部门现在分别有多少人", "年假有几天", "上个月各产线产量和良率", "前十大客户销售额", "今年每个月的离职率", "迟到多久算旷工"]


def token(user: str, role: str) -> str:
    n = len(settings.jwt_secret.encode())
    return jwt.encode({"sub": user, "role": role, "exp": int(time.time() + 7200)}, settings.jwt_secret, algorithm="HS512" if n >= 64 else "HS384" if n >= 48 else "HS256")


def main() -> None:
    app = sys.argv[1] if len(sys.argv) > 1 else "kbase"
    users, per_user = int(sys.argv[2]) if len(sys.argv) > 2 else 20, int(sys.argv[3]) if len(sys.argv) > 3 else 5
    param_ratio = float(sys.argv[4]) if len(sys.argv) > 4 else 0.7
    url = f"http://127.0.0.1:{8094 if app == 'kbase' else 8093}/{'ask' if app == 'kbase' else 'chat'}"
    rows, lock, rnd = [], threading.Lock(), random.Random(7)
    plan = [[(f"{rnd.choice(MODELS)} 的{rnd.choice(PARAMS)}是多少" if rnd.random() < param_ratio else rnd.choice(KB_DESC)) if app == "kbase" else rnd.choice(CP)
             for _ in range(per_user)] for _ in range(users)]

    def user(i: int) -> None:
        name = f"load{i:03d}"
        headers = {"Authorization": f"Bearer {token(name, 'ENGINEER' if app == 'kbase' else 'MANAGEMENT')}"}
        with httpx.Client(timeout=180, trust_env=False) as http:
            for q in plan[i]:
                t = time.time()
                try:
                    r = http.post(url, json={"query": q}, headers=headers)
                    d = r.json() if r.status_code == 200 else {}
                    row = {"code": r.status_code, "seconds": time.time() - t, "status": d.get("status"), "degraded": bool(d.get("degraded")), "cache": bool(d.get("cache_hit")),
                           "first_token": (d.get("first_token_ms") or 0) / 1000, "route": d.get("route") or d.get("intent")}
                except Exception as e:
                    row = {"code": 0, "seconds": time.time() - t, "error": str(e)[:80]}
                with lock:
                    rows.append(row)

    start = time.time()
    threads = [threading.Thread(target=user, args=(i,)) for i in range(users)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.time() - start
    ok = [r for r in rows if r["code"] == 200]
    sec = sorted(r["seconds"] for r in ok)
    pct = lambda p: round(sec[min(len(sec) - 1, int(round(p * (len(sec) - 1))))], 2) if sec else None
    out = {"app": app, "at": datetime.now().isoformat(timespec="seconds"), "users": users, "per_user": per_user, "requests": len(rows), "wall_seconds": round(wall, 1),
           "ok": len(ok), "rate_limited_429": sum(r["code"] == 429 for r in rows), "errors": sum(r["code"] not in (200, 429) for r in rows),
           "throughput_per_min": round(len(ok) / wall * 60, 1), "p50_s": pct(0.5), "p95_s": pct(0.95), "max_s": round(sec[-1], 2) if sec else None,
           "mean_s": round(statistics.mean(sec), 2) if sec else None, "degraded": sum(r.get("degraded", False) for r in ok), "cache_hits": sum(r.get("cache", False) for r in ok),
           "by_status": {s: sum(1 for r in ok if r.get("status") == s) for s in sorted({str(r.get("status")) for r in ok})},
           "limits": {"per_minute": settings.kbase_rate_per_minute if app == "kbase" else settings.copilot_rate_per_minute,
                      "concurrency": settings.kbase_concurrency if app == "kbase" else settings.copilot_concurrency}}
    folder = Path(__file__).resolve().parents[1] / "output" / "loadtest"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{app}_{users}x{per_user}_{datetime.now():%m%d%H%M}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
