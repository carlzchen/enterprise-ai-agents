"""
订单 Agent（SPEC 阶段 2）：内部是固定流程。parse 解析 → extract 抽取（模板 / 大模型）→ validate 规则校验 → diff 变更比对 → agent 串起来并产出草稿。
工具见 tools.py（R 只读 / W 写，W 必须经人工审批）。
"""
