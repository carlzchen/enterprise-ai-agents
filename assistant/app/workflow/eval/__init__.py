"""
跟单多 Agent 的评测（SPEC 阶段 4）。一条命令生成全部报告：

    PYTHONPATH=. .venv/bin/python -m app.workflow.eval.run

  eval_router   Supervisor：意图识别、任务拆分、单号引用
  eval_po       订单 Agent：字段级 / 行级准确率、料号映射命中率、变更识别、规则标记、ERP 草稿和标准销售订单一致率
  eval_agents   整张图：端到端任务成功率、交付 Agent 工具调用准确率；审核 Agent 拦截率和误拦截率（构造的违规 / 正常草稿）

报告写到 data/workflow_eval/reports/。报告里的数字就是对外说明和文档里效果数字的唯一来源。
"""
