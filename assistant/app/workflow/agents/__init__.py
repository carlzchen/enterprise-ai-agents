"""
跟单 Workflow 里的 Agent。
  runtime.py   一个 Agent 的运行循环：模型决定调哪个工具，直到它调用 submit 交卷（或用完步数）
  tools.py     工具：全部只读；每个 Agent 只拿到自己那几个；每次调用的结果都记进证据账本
  schemas.py   Agent 之间传递的数据结构
  nonstd.py    非标询价评估：技术、成本、交期、商务四个专业 Agent + 审查，用 LangGraph 编排；以及两种对照方案
  research.py  新开发预研：技术 Agent 判定需要新开发时，用 DeepAgents 列计划、派子 Agent 查知识库和历史变更
  evaluate.py  三种方案的对比评测
"""
