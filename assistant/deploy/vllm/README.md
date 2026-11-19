# 换成公司自建的 vLLM + Qwen3

> 这一部分**没有在真实 GPU 上跑过**（开发机是 Mac，vLLM 需要 NVIDIA 显卡）。
> 代码侧的切换已经做成配置项并用现有的 OpenAI 兼容接口验证过；下面的显卡参数、并发和延迟都要在公司的 GPU 服务器上实测后才能定。

## 1. 为什么换

- 数据不出公司：图纸、成本、工资不再发给外部接口。
- 并发：vLLM 会把同时到达的请求合成一批推理（连续批处理），这是扛高并发的主力。
- 首字延迟更稳，成本从按量计费变成固定的机器成本。

## 2. 怎么部署

1. GPU 服务器上启动模型：`docker compose -f deploy/vllm/docker-compose.yml up -d`
   - 主模型 Qwen3-32B（AWQ 量化）占一张 48G 显存的卡；备用模型 Qwen3-8B 和向量、重排模型共用第二张卡。
2. 同一台机器上启动向量和重排模型服务：
   `BGE_DEVICE=cuda BGE_FP16=true BGE_RERANKER_DEVICE=cuda BGE_RERANKER_FP16=true python -m app.platform.model_server`
3. 业务服务的 `.env` 改这几项，其他代码不用动：

```
LLM_BASE_URL=http://<GPU服务器>:8000/v1
LLM_MODEL=qwen3-32b
LLM_API_KEY=EMPTY
LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
LLM_FALLBACK_BASE_URL=http://<GPU服务器>:8001/v1
LLM_FALLBACK_MODEL=qwen3-8b
MODEL_SERVER_URL=http://<GPU服务器>:8097
```

4. Prometheus 里把 `deploy/monitoring/prometheus.yml` 末尾 vllm 那一段的注释打开，就能在 Grafana 里看到排队数、首字时间和吞吐。

## 3. 换了之后要注意什么

| 事项 | 说明 |
| --- | --- |
| 思考模式 | Qwen3 默认会先「思考」再回答，在线问答必须关掉（上面的 `LLM_EXTRA_BODY`），否则延迟成倍增加 |
| 问数的工具调用 | 启动参数里的 `--enable-auto-tool-choice --tool-call-parser=hermes` 不能少。换模型后**必须重跑问数的 348 题评测**，重点看工具选错和参数填错的比例 |
| JSON 输出 | Text2SQL 那一步要求模型输出 JSON；vLLM 支持按格式约束输出，稳定性通常比提示词约束更好 |
| 评测裁判 | RAGAS 的打分模型不能和答题的是同一个，要另配一个更强的（否则是自己给自己打分） |
| 降级 | 主模型熔断后自动切到备用模型；备用模型也不可用时，知识库返回原文摘录，问数只给数据不给文字解读。**不要把备用模型配成外部 API** |
| 并发上限 | `LLM_CONCURRENCY` 要和 vLLM 的 `--max-num-seqs` 对齐：业务侧放进去的并发不应超过模型一批能处理的量 |
| 重排速度 | 开发机上重排 50 条要 4 秒左右（CPU）；放到 GPU 上预期是百毫秒级，要实测 |

## 4. 上线前的验证清单

- [ ] 问数 348 题评测准确率不低于换模型前
- [ ] 知识库 297 题评测：参数题准确率、引用有效率、RAGAS 四项
- [ ] 压测：目标并发下的首字时间、P95、错误率、被限流的比例
- [ ] 手动停掉主模型，确认自动切到备用模型，Grafana 上能看到熔断器打开、飞书收到告警
- [ ] 手动停掉向量库、重排服务、消息队列，逐个确认降级符合预期
