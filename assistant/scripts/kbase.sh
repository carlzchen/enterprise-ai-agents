#!/bin/sh
# 智能问数和企业知识库一键启停：./scripts/kbase.sh start | stop | status
# 启动四个本机进程：问数 Copilot（8093）、跟单 Workflow（8095）、外部系统模拟网关（8096，金蝶 EAS + 公司 SSO + 飞书告警接收端）、知识库服务（8094）；数据库、消息队列、监控等容器用 docker compose 管理
# 链路追踪（Langfuse）按需另外启动：docker compose --profile langfuse up -d
cd "$(dirname "$0")/.." || exit 1
RUN=output/run
mkdir -p "$RUN"

up() {   # 名称 模块 端口
  if lsof -i ":$3" -sTCP:LISTEN >/dev/null 2>&1; then echo "$1 已经在运行（端口 $3）"; return; fi
  nohup .venv/bin/python -m "$2" > "$RUN/$1.log" 2>&1 &
  echo $! > "$RUN/$1.pid"
  echo "$1 已启动（端口 $3，日志 $RUN/$1.log）"
}

case "$1" in
  start)
    docker compose up -d >/dev/null 2>&1 && echo "容器已就绪（Milvus、MongoDB、MinIO、Redis、Neo4j、RabbitMQ、Prometheus、Grafana）"
    up eas-mock app.kbase.kingdee.mock_gateway 8096
    up kbase app.kbase.server 8094
    up copilot app.api.http.copilot_server 8093
    up workflow app.workflow.server 8095
    printf "等待模型加载"
    for i in $(seq 1 60); do
      curl -s localhost:8094/health | grep -q '"models":true' && { echo " 完成"; echo "打开 http://localhost:5180 ，左侧菜单「智能问数」「企业知识库」「跟单工作台」"; echo "监控看板 http://localhost:3003 ；消息队列 http://localhost:15673 ；飞书模拟接收端 http://localhost:8096/feishu/messages"; exit 0; }
      printf "."; sleep 3
    done
    echo " 超时，请看 $RUN/kbase.log" ;;
  stop)
    for n in kbase eas-mock copilot workflow; do [ -f "$RUN/$n.pid" ] && kill "$(cat "$RUN/$n.pid")" 2>/dev/null && echo "$n 已停止"; rm -f "$RUN/$n.pid"; done ;;
  restart)
    "$0" stop; sleep 2; "$0" start ;;
  status)
    for p in 8093 8094 8095 8096; do lsof -i ":$p" -sTCP:LISTEN >/dev/null 2>&1 && echo "端口 $p 运行中" || echo "端口 $p 未运行"; done
    curl -s localhost:8094/health; echo ;;
  *) echo "用法：$0 start | stop | restart | status" ;;
esac
