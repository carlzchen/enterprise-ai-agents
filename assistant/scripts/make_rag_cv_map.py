"""
生成「RAG 简历 ↔ 代码」对照文档：PYTHONPATH=. .venv/bin/python scripts/make_rag_cv_map.py
行号是从当前代码里查出来的，代码改动后重新运行一次就能更新。输出 docs/RAG-07-简历与代码对照.md
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "docs" / "RAG-07-简历与代码对照.md"


def loc(path: str, symbol: str) -> str:
    """找到函数、类、常量或文本所在的行，生成可点击的链接。symbol 以 ~ 开头时按原文查找（非 Python 文件用）"""
    text = (ROOT / path).read_text(encoding="utf-8").splitlines()
    if symbol.startswith("~"):
        pat = re.compile(re.escape(symbol[1:]))
    else:
        pat = re.compile(rf"^\s*(def|class|async def)\s+{re.escape(symbol)}\b|^{re.escape(symbol)}\s*=")
    for i, line in enumerate(text, start=1):
        if pat.search(line):
            return f"[{path.split('/')[-1]}:{i}](../assistant/{path}#L{i})"
    raise SystemExit(f"找不到 {path} 里的 {symbol}")


def table(rows: list[tuple]) -> str:
    out = ["| 位置 | 函数 / 内容 | 看什么 |", "|---|---|---|"]
    for path, sym, what in rows:
        out.append(f"| {loc(path, sym)} | `{sym.lstrip('~')}` | {what} |")
    return "\n".join(out)


K, I, Q, AU = "app/kbase/", "app/kbase/ingest/", "app/kbase/query/", "app/kbase/auth/"

SECTIONS = [
    ("1. 文档入库流水线（LangGraph 编排）",
     "文档入库：负责多格式文档解析入库，基于 LangGraph + MinerU / Docling + Qwen-VL 构建入库流水线：上传 → 文件级 SHA-256 比对 → 按类型解析（PDF / Word / PPT 转 Markdown，Excel 表格解析，图片与扫描页生成图文描述）→ 清洗、脱敏、统一术语与单位 → 按章节切分 → Qwen3-32B 逐块识别主体（型号 / 系列）并回查原文证据，无证据不入库 → 打 Metadata（doc_id / version / page / acl / subject）→ BGE-M3 向量化 → 质量门禁。<br/>增量更新：负责文档版本更新方案，基于 SHA-256 构建文档级 + chunk 级两层指纹，新版本只对变更 chunk 重新识别和向量化，未变更 chunk 直接复用；哈希前去除页码、页眉页脚等噪声，按章节切分保证 chunk 边界与指纹稳定。",
     "入库是一张 LangGraph 图，每个节点出错都不往外抛，统一走失败出口分类处理。增量的关键是两层指纹：文件没变整份跳过；"
     "文件变了也只有内容变了的 chunk 才重新识别和向量化，没变的 chunk 原样沿用（同一个主键、同一份向量），消失的在切换时标成失效。",
     [(I + "graph.py", "~导入图（LangGraph）", "整张图的结构和每个节点的职责（文件开头的图）"),
      (I + "graph.py", "node_entry", "文件级 SHA-256 判重：同版本同内容跳过，版本没升内容变了拒绝"),
      (I + "versioning.py", "decide", "判重的五种结果：SKIP / CONFLICT / STALE / NEW / RESUME"),
      (I + "graph.py", "node_router", "按文件类型分三条路线"),
      (I + "parsers.py", "parse_doc", "PDF / Word / PPT：pypdf、Docling、MinerU；扫描页和图片文件交给 Qwen-VL"),
      (I + "images.py", "scanned_pdf_to_markdown", "扫描件逐页渲染成图片，视觉模型转写成 Markdown（带页码）"),
      (I + "images.py", "enrich", "文档内图片上传 MinIO 私有存储，视觉模型写图文描述（按内容哈希缓存）"),
      (I + "graph.py", "_normalize", "每条路线解析完都做：脱敏 → 术语和单位统一"),
      (I + "clean.py", "mask_sensitive", "脱敏：手机号、身份证号、银行卡号、邮箱、敏感词，图片地址不动"),
      (I + "clean.py", "unify_terms", "术语和单位统一（扭矩→转矩、Nm→N·m、°C→℃、φ→Φ），单位只在数字后面换"),
      (I + "clean.py", "normalize_for_hash", "算 chunk 哈希前去掉文档标题、页码标记、单独成行的页码和空白"),
      (I + "graph.py", "node_split", "按章节切分，每块算归一化 SHA-256；图片挂到所在章节"),
      (I + "versioning.py", "diff", "和当前生效的 chunk 按指纹比对：沿用 / 新增 / 消失；同样内容出现两次时一对一复用"),
      (I + "graph.py", "node_subject", "只给新增的 chunk 识别主体，结果落盘，重试时不重复调模型"),
      (I + "subject.py", "identify", "逐块调用模型识别型号 / 系列；连续失败过半就改用规则，不一块块等超时"),
      (I + "subject.py", "check", "证据校验：证据必须能在原文里找到、型号要在原文或文档元数据里、系列和型号前缀一致；不过就不用模型结果"),
      (I + "graph.py", "node_quality_gate", "质量门禁：空块比例、型号识别率、参数抽取、表格行数、图纸是否什么都没读到"),
      (I + "graph.py", "node_embed", "只给新增的 chunk 算 BGE-M3 向量（沿用的不重算）"),
      (I + "graph.py", "node_switch", "对账通过后事务切换，同一次提交写通知事件"),
      (I + "events.py", "record", "持久化通知事件（和业务变更同一个事务）"),
      (I + "events.py", "relay_once", "转发到 RabbitMQ；队列挂太久时降级成本进程处理"),
      (I + "events.py", "handle", "消费者：渲染成邮件写发件箱（按事件号去重）→ SMTP 发出"),
      (K + "vectors.py", "schema", "Milvus 的元数据字段：doc_key（doc_id）、version、page、acl、level、item_name / series（subject）、state")],
     "上传一份文档 → 改一段再升版本号上传 → 看任务结果里的「切片比对：新增 x，沿用 y，消失 z」→ `kb_chunk` 里沿用的 chunk_id 不变、消失的 state = INVALID → 邮件发件箱里有版本切换通知。"
     "自测 `scripts/kbase_selftest.py` 第四节就是这个过程。",
     [("为什么不直接整份重新入库？", "整份重做要把所有 chunk 重新向量化、重新调模型识别主体；资料改版通常只改几处。按 chunk 指纹比对后只处理变化的部分，向量和识别结果也不会因为重算出现漂移。"),
      ("页码变了算不算内容变了？", "不算。哈希前去掉了页码标记、单独成行的页码和文档标题；页码属于元数据，沿用的 chunk 只更新元数据（切换时从 metas 里取这个版本的页码和章节）。"),
      ("主体识别为什么要证据校验？", "模型会「猜」型号。要求它摘一句原文作证据，并核对型号确实出现在原文或文档元数据里；对不上的结果不入库，退回规则识别，避免错误标注污染按型号过滤的检索。"),
      ("通知为什么要先落库再发消息队列？", "版本切换和事件在同一个事务里提交：切换成功就一定有事件，回滚了就一定没有。消息队列只负责投递，挂了事件还在表里，恢复后补发；消费者按事件号去重，重复投递也只发一封。")]),

    ("2. 分层存储与图文关联",
     "分层存储：负责存储层设计，基于 Milvus + MySQL + MinIO + MongoDB 构建分库存储：Milvus 存向量与 Metadata，MySQL 存结构化参数，MinIO 存储原文件、图片与解析后的 Markdown，MongoDB 记录 RAG 查询日志（问题、改写、召回结果、答案与引用、用户反馈）；按生效版本关联图文，读图时生成临时签名链接，不暴露存储地址。",
     "MySQL 是真相源（chunk 状态、版本指针、参数、图纸卡片），Milvus 是可重建的派生索引，MinIO 放原文件、图片和解析后的 Markdown 且不开匿名读，MongoDB 记录每次问答的完整过程和用户反馈。",
     [(K + "store.py", "~kb_chunk         每个切片一行", "各张表的职责（文件开头的说明）"),
      (K + "vectors.py", "~知识库的向量索引", "Milvus 集合：三种向量字段 + 元数据 + 状态"),
      (I + "graph.py", "node_archive", "解析后的 Markdown 和每块的主体识别结果（含证据）存 MinIO 的 parsed/ 目录"),
      (Q + "querylog.py", "write", "MongoDB 查询日志：问题、改写、HyDE、召回、精排、答案与引用、耗时、token"),
      (Q + "querylog.py", "set_feedback", "用户反馈回写到查询日志"),
      (I + "images.py", "object_key", "图片在 MinIO 里的路径；Markdown 里只写 kbimg:// 引用，不写存储地址"),
      ("app/infra/object_storage/minio_gateway.py", "presigned_url", "临时签名链接（签名里含主机名，所以用浏览器能访问的地址建签名客户端）"),
      (K + "server.py", "image", "看图接口：统一授权 → 图片属于生效版本 → 跳转到几分钟有效的签名链接"),
      (Q + "answer.py", "_images_of", "回答引用到某个章节时，把这个章节在生效版本下的图片带上")],
     "前端问一句命中带图章节的问题（例如「伺服电机转矩-转速特性曲线怎么看」）→ 出处下面出现缩略图 → 浏览器网络面板里图片地址带 X-Amz-Signature；直接访问 MinIO 公开地址返回 403。",
     [("为什么不让前端直接拿 MinIO 地址？", "地址一旦泄露就永久可访问，也绕过了权限。现在每次展示都先按这个人的权限判断，再签一个几分钟过期的链接。"),
      ("为什么图片要按生效版本关联？", "文档升版后旧图可能被替换，回答引用的是生效版本的章节，就只展示这个版本登记的图片，不会新文旧图混用。")]),

    ("3. 参数与图纸结构化",
     "参数与图纸结构化：负责产品参数与 CAD 图纸结构化，基于 ODA + ezdxf 构建图纸解析模块：datasheet / 选型表拆为\"型号–参数–数值–单位\"入 MySQL 供精确查询；DWG 转 DXF 后遍历模型 / 图纸空间并展开图块，从标注几何块解析尺寸与 \\S 堆叠公差（无几何块时按测量值与标注样式还原，正则识别炸开的标注），按表格线重建网格还原 BOM（兼容 GB 明细栏自下而上排列，无表格线时坐标聚类），读取标题栏属性 → 每张图纸生成结构化卡片 → 关联 ERP 物料，图纸与 BOM 交叉校验，不一致告警。",
     "图纸不做 OCR，直接读矢量实体。难点是尺寸值多在标注的几何块里、公差是 \\S 堆叠格式，明细栏是直线和单行文字画的、不是表格对象。",
     [(I + "params.py", "from_markdown", "规格书里的参数表拆成「型号–参数–数值–单位」"),
      (I + "parsers.py", "parse_table", "选型表（Excel）逐行拆参数"),
      (I + "parsers.py", "dwg_to_dxf", "DWG → ODA File Converter → DXF（ACAD2018）"),
      (I + "cad.py", "_walk", "遍历模型空间 + 图纸空间；块参照递归展开，标题栏块只读属性"),
      (I + "cad.py", "read_dimension", "尺寸取值顺序：几何块显示文字 → 文字覆盖 → 测量值 + 标注样式公差"),
      (I + "cad.py", "mtext_plain", "解析 MTEXT 的 \\S 堆叠公差，去掉其他格式码"),
      (I + "cad.py", "parse_dim_text", "统一解析成基本尺寸 / 上下偏差 / 配合代号 / 数量；支持 Φ、R、M 螺纹、±、H7"),
      (I + "cad.py", "exploded_dims", "炸开成文字的标注：标注图层上像尺寸的文字按正则识别"),
      (I + "cad.py", "bom_table", "明细表：表格线重建网格 → 按坐标落格 → 没线时坐标聚类；识别 GB 明细栏自下而上；按序号排序；合并续行"),
      (I + "cad.py", "title_block", "标题栏：读块属性（中英文属性名都认），没有块属性时按标签兜底"),
      (I + "cad.py", "read", "一张图生成一张卡片：标题栏、全部尺寸、关键特征、明细表、技术要求、缺项"),
      (I + "parsers.py", "cross_check", "和金蝶物料、BOM 交叉校验：物料不存在、用量不一致、轴径和物料规格不一致"),
      (I + "parsers.py", "parse_cad", "缺项的图纸渲染成 PNG 交给视觉模型复核"),
      (I + "cad.py", "render_png", "ezdxf + matplotlib 渲染"),
      ("scripts/make_cad_gb_sample.py", "main", "测试图纸：块里的标注、堆叠公差、炸开的标注、GB 明细栏、续行、中文属性名")],
     "`PYTHONPATH=. .venv/bin/python scripts/make_cad_gb_sample.py` 生成测试图纸 → 上传 → 看「资料管理」里的图纸卡片和明细表；单元测试 `tests/test_kbase_v3.py::test_gb_drawing_card` 覆盖全部情况。",
     [("为什么不用 OCR 读图纸？", "DXF 里尺寸、文字、线都是矢量实体，直接读是精确值；OCR 会把 Φ19 读成 019、公差读丢。只有读不全的图才渲染成图片交给视觉模型复核。"),
      ("怎么区分表头在上的 BOM 和 GB 明细栏？", "找到表头那一行后，看正文文字在它上方多还是下方多：在上方就是 GB 明细栏（表头在下、序号自下而上），按这个方向读，再按序号排序。"),
      ("续行怎么判断？", "一行只有名称、没有序号和代号，就是上一项名称太长折下来的，按填写方向接到上一项。")]),

    ("4. 问答链路（LangGraph 条件分支编排）",
     "问答链路：负责问答主链路，基于 LangGraph 条件分支 + Qwen3-32B + bge-reranker-v2-m3 构建混合检索问答：意图识别 → 主题确认（锁定型号 / 系列）→ 参数、尺寸类走 SQL 精确查询；说明类经问题改写并生成 HyDE 假设文档 → 改写 Query 稠密检索 + HyDE 检索 + BM25 多路召回 Top10 → RRF 融合 → 精排 → 分数断崖截断（≤ Top3）→ 按 32K 上下文预算拼装证据 → 生成答案并标注出处 → 校验引用真实性，不通过则重答 → FastAPI + SSE 流式输出；多轮对话采用指代消解与历史摘要压缩。",
     "数值从 SQL 来，解释从文档来。说明类问题先让模型改写并写一段「手册里大概会怎么写」的假设答案，三路各召回 10 条用 RRF 合并，再精排、在分数断崖处截断（最多 3 条）。",
     [(Q + "graph.py", "~问答图（LangGraph）", "整张图（文件开头）"),
      (Q + "intent.py", "classify", "意图路由：参数 / 说明 / 混合（规则，不调模型）"),
      (Q + "graph.py", "node_topic", "主题确认节点"),
      (Q + "topic.py", "resolve", "型号完全对上就锁定；写不全时只有一个候选自动补全，多个候选且问参数就反问；追问沿用会话里锁定的主题"),
      (Q + "lookup.py", "params_of", "参数精确查询（带权限、只查生效版本）"),
      (Q + "graph.py", "node_hyde", "改写 + HyDE；失败降级为跳过 HyDE"),
      (Q + "intent.py", "rewrite_hyde", "一次调用同时做改写（口语转术语、指代消解）和假设答案"),
      (Q + "retrieve.py", "recall", "三路各 Top10：改写问题的稠密向量、假设答案的稠密向量、Milvus 内置中文 BM25"),
      (Q + "retrieve.py", "rrf", "RRF 融合（按名次，不按分数，三路分数不在一个尺度上）"),
      (Q + "retrieve.py", "_only_valid", "按 SQL 状态再过滤，换上生效版本的元数据"),
      (Q + "retrieve.py", "rerank", "bge-reranker-v2-m3 精排"),
      (Q + "retrieve.py", "dynamic_topk", "分数断崖截断，最多 3 条（单变量调参选出来的）"),
      (Q + "answer.py", "pack", "32K 上下文预算：固定部分 → 历史（有上限，先丢最早的轮次）→ 证据按顺序装，装不下截到段落边界，再装不下丢掉并记进链路"),
      (Q + "answer.py", "context_text", "每条证据标文档号、版本、章节、页码"),
      (Q + "prompts.py", "ANSWER", "回答提示词（带版本号）"),
      (Q + "answer.py", "verify", "引用校验：编号必须存在、回答里的数值必须能在被引用的资料里找到"),
      (Q + "graph.py", "node_verify", "不过就更严格地重答一次，再不过降级为摘录原文"),
      (Q + "memory.py", "compress", "多轮：最近几轮原文 + 更早轮次的摘要（后台压缩）"),
      (K + "server.py", "ask_stream", "FastAPI + SSE 流式输出")],
     "前端问「KL-SV-80 的额定转矩是多少」→ 反问选型号 → 点 750W → 走 SQL；再问「伺服电机发烫怎么办」→ 标签显示「理解为：伺服电机过热…」、出处显示「召回：语义第1、假设答案第1、关键词第1」。",
     [("为什么要 HyDE？", "用户问「发烫怎么办」，手册写的是「过热报警处理」，问题和答案的说法差得远。假设答案用手册的写法写，向量更接近真正的答案段落。它只用来检索，不给用户看，不确定的数值用 X 代替。"),
      ("为什么三路而不是只用稠密？", "稠密擅长语义，BM25 擅长字面命中（型号、报警代码），HyDE 补口语和术语的差距。单变量调参结果见 RAG-02 第 11 节。"),
      ("断崖截断是什么？", "精排分数排好后，如果后一条比前一条明显低一截（绝对差 > 0.25 或相对差 > 50%），后面的就不要了，最多留 3 条。给模型的资料少而准，噪声少。"),
      ("为什么每路 10 条、最多留 3 条？", "单变量调参：每路 10 条和 50 条时标准章节进候选池的比例一样（98.7%），多召回只是浪费精排时间；最多留 3 条时给模型的资料里有用的占比 35.6%（留 5 条是 25%），留 2 条占比更高但开始丢命中。"),
      ("32K 预算怎么分？", "先留 2K 给回答，再放提示词和问题，历史最多 3K（摘要优先保留），剩下全给证据；按中文 1 字 1 token、其余 3.5 字符 1 token 估，偏保守。")]),

    ("5. 权限、安全与稳定性",
     "权限与安全：负责权限控制与内容安全，基于公司 SSO（OIDC）+ Milvus 标量过滤构建检索前权限预过滤：SSO 登录后由后端验签获取身份，按部门路径、职务、职级映射角色与密级；文档 acl 按部门 ID + 角色 + 密级标注，部门权限沿组织架构继承，向量检索、SQL 查询、图片链接三处统一鉴权，未标注文档默认拒绝；SSO 注销通知与每日目录对账联动，离职 / 调岗实时失效；服务仅在内网开放，远程经公司 VPN 访问；ERP 查询按工号映射金蝶用户执行数据权限并留审计日志；输入 / 输出双向审核，空召回追问、低相关兜底、无权限拒答。",
     "一个 Scope 管到底：同一个对象生成 Milvus 过滤表达式、SQL 条件和单条判断，向量检索、参数查询、图片链接三处用同一套规则。身份只在后端从 SSO 拿，前端传什么都不信。",
     [(K + "authz.py", "Scope", "可见范围 = 身份标记（全员 / 角色 / 部门，含全部上级部门）+ 密级上限；未标注的文档谁都看不到"),
      (K + "authz.py", "~def milvus_expr", "Milvus 预过滤表达式（先过滤再取 TopK）"),
      (AU + "sso.py", "exchange", "公司 SSO（OIDC）授权码 + PKCE 换 ID Token"),
      (AU + "sso.py", "verify", "RS256 验签，核对 iss、aud、exp、nonce"),
      (AU + "principal.py", "derive", "部门 → 角色、职位 → 补充角色、职级 → 密级（规则在 resources/sso_rules.json）"),
      (AU + "principal.py", "resolve", "每次请求：Redis 缓存 → 目录刷新；目录不可用沿用旧身份，离职禁用拒绝，没规则只给公开"),
      (AU + "principal.py", "on_backchannel_logout", "SSO 后端通道注销 → 吊销这个人的全部知识库登录"),
      (AU + "principal.py", "reconcile_all", "每日和公司目录全量对账"),
      (AU + "deps.py", "issue", "知识库自己的登录凭证（独立密钥和受众，拿去调 ERP 无效）"),
      (AU + "network.py", "install", "只在内网和 VPN 网段提供服务（应用层第二道闸）"),
      ("deploy/nginx/allowlist.conf", "~allow 10.8.0.0/16", "Nginx 白名单：办公网 + VPN 地址池"),
      (Q + "graph.py", "node_guard", "输入审核"),
      (Q + "guard.py", "clean_output", "输出审核（打码）"),
      (Q + "graph.py", "~按可见范围分缓存", "问答缓存 key 含权限范围"),
      (Q + "lookup.py", "bom_of", "ERP 数据：工号映射金蝶用户，查询写审计日志"),
      (Q + "answer.py", "no_result", "三类异常之一：检索为空 → 请用户补充信息"),
      (Q + "answer.py", "low_relevance", "三类异常之二：相关度低 → 不作答，给出最接近的资料"),
      (Q + "answer.py", "denied", "三类异常之三：有资料但没权限 → 明确拒答"),
      (Q + "graph.py", "degrade", "降级：跳过 HyDE、向量库不可用退全文检索、重排排不上队用融合结果、生成失败摘录原文")],
     "前端登录页点「公司 SSO 登录」选不同员工：研发 P7 能看 63 份文档，销售经理 37 份；在模拟网关把某人调岗（POST /mock/sso/users/E1001）→ 旧登录立刻 401。",
     [("身份为什么不让前端传？", "前端传的部门、角色都能改。身份只来自后端验过签的 ID Token 和公司目录，前端只拿到一个知识库自己签的登录凭证。"),
      ("部门权限怎么继承？", "SSO 给的是从根到本部门的完整路径，每一级都变成一个 D:部门编号 标记。文档授权给「研发中心」时，伺服研发部的人身上也带着研发中心的标记，自然能看。"),
      ("缓存为什么按权限范围分？", "同一个问题，研发能看到机密资料、销售看不到，回答不一样。key 里带权限范围的哈希，范围相同的人共享缓存，不同的人不会串。"),
      ("远程怎么用？", "知识库没有公网入口。在外面用公司电脑连 VPN，拿到 VPN 地址池里的地址后访问；Nginx 和应用各校验一次来源地址。")]),

    ("6. 版本一致性与补偿",
     "稳定性与一致性：负责服务稳定性与数据一致性，基于 Redis + RabbitMQ 构建限流、缓存、降级与补偿机制：分布式缓存按权限范围隔离，下游模型故障时优雅降级；doc_id + version 幂等更新，新 chunk 以 pending 状态写入，对账通过后事务内切换生效，避免半成品被检索和新旧版本混用，失败可回滚；中断残留由持久化补偿任务清理，导入结果经 RabbitMQ + SMTP 通知，每日对账 MySQL 与 Milvus。",
     "任何一步出错，用户看到的仍是旧版本。切换是一个 SQL 事务；Milvus 的状态在提交后同步，同步失败登记补偿任务，期间检索按 SQL 状态再过滤，不会查到失效内容。",
     [(I + "versioning.py", "~版本与一致性", "写入协议（文件开头）"),
      (I + "versioning.py", "stage", "暂存：新增 chunk 写成 PENDING，沿用的只登记这个版本的元数据"),
      (I + "versioning.py", "verify", "对账：缺的补、多的删、状态不一致按 SQL 改、抽样比对内容哈希"),
      (I + "versioning.py", "_apply", "事务内切换：PENDING → VALID、消失的 → INVALID（记 valid_to）、沿用的换上新版本元数据"),
      (I + "versioning.py", "switch", "切换 + 事件同一次提交，再同步 Milvus；同步失败登记补偿任务"),
      (I + "versioning.py", "rollback", "回滚就是切回上一个版本（数据都还在）"),
      (I + "graph.py", "node_fail", "失败出口：可重试的按 30 秒 / 2 分钟 / 10 分钟退避，耗尽后登记补偿任务并发通知"),
      (I + "versioning.py", "clean_staged", "补偿：清掉中断导入留下的 PENDING 切片、Milvus 向量、MinIO 原文件和独有图片"),
      (I + "versioning.py", "run_compensations", "补偿任务退避重试，耗尽后发告警事件"),
      (I + "versioning.py", "reconcile_all", "每日对账 + 找孤儿暂存数据 + 清理过期旧版本"),
      (K + "scheduler.py", "JOBS", "定时任务：每分钟跑补偿、每天对账、每天目录对账")],
     "`KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python scripts/kbase_selftest.py`：第三节演示一直失败 → 补偿清掉 MinIO 文件；第四节演示切换前「掉电」时新切片检索不到、重试后切换成功、回滚。",
     [("为什么不是先删旧的再插新的？", "两步之间出错，这份文档就从知识库里消失了。现在新内容先暂存（检索看不到），对账通过才一次切换，失败就保持旧版本。"),
      ("SQL 和 Milvus 不在一个事务里怎么办？", "SQL 是真相源先提交；Milvus 只改状态字段，失败了登记补偿任务追上。这段时间检索结果按 SQL 状态再过滤一遍，所以不会出现失效内容。"),
      ("Milvus 更新后多久能读到？", "标量更新是最终一致的，几秒内可见；所以最后一道过滤放在 SQL，自测里也是按「最终能做到」来判断。")]),

    ("7. 可观测与评测迭代",
     "可观测与评测：负责效果评测与调优，基于 Langfuse + RAGAS 构建全链路追踪与评测体系：@observe 追踪改写、HyDE、召回、精排与生成，关联文档版本、Prompt 版本与 Token 用量，结合标注集定位漏召回与排序偏差、验证策略调整效果；300 条分层测试集单变量调参（Chunk Size / TopK / 阈值 / Rerank），从 MongoDB 查询日志筛出点踩记录进入 Bad Case 池，回溯原始 chunk 闭环优化。",
     "每次问答一条链路，节点一个 span，节点里的改写、三路召回、精排、生成再嵌套 span；链路上挂文档版本、提示词版本和 token。调参脚本每次只改一个变量。",
     [("app/platform/tracing.py", "observe", "@observe 装饰器：自动开嵌套 span，记输入输出、耗时、异常"),
      ("app/platform/tracing.py", "generation", "在当前 span 下记一次大模型调用"),
      (Q + "graph.py", "ask", "链路上挂文档版本、提示词版本、token、上下文预算、锁定的主题"),
      (Q + "prompts.py", "tag", "提示词版本串（例如 answer@v4,rewrite_hyde@v1），也写进 kb_answer_log"),
      (Q + "graph.py", "feedback", "点踩写反馈，分数同步到 Langfuse"),
      (Q + "graph.py", "bad_cases", "Bad Case 池：从 MongoDB 查询日志筛点踩的记录，带当时的召回、精排和引用，回溯到原始 chunk"),
      (Q + "querylog.py", "bad_cases", "按点踩时间倒序取"),
      ("app/kbase/eval/tune.py", "main", "单变量调参：召回路数、每路召回条数、TopK、最低分、断崖落差、重排、chunk size"),
      ("app/kbase/eval/tune.py", "build_chunk_collection", "chunk size 变体在临时集合里重新切分和向量化，不动线上数据"),
      ("app/kbase/eval/tune.py", "ragas_contexts", "RAGAS 上下文精确率 / 召回率（每个变体同一批题）"),
      ("app/kbase/eval/runner.py", "ragas", "RAGAS 四项指标（全流程回答）"),
      ("Dockerfile", "~FROM python:3.12-slim", "服务镜像"),
      ("deploy/kbase/docker-compose.yml", "~services:", "部署编排：知识库服务 + 入口网关"),
      ("deploy/nginx/kbase.conf", "~include /etc/nginx/kb/allowlist.conf;", "Nginx 反向代理：内网白名单、HTTPS、SSE 不缓冲、按来源限流")],
     "Langfuse（http://localhost:3002，要单独 `docker compose --profile langfuse up -d`）打开一条链路看嵌套 span；调参结果在 `data/kbase_eval/tune.md`。",
     [("单变量调参为什么一次只改一个？", "同时改两个变量，指标变了不知道是谁的功劳。基线固定，每行只改一个，结论才能归因。"),
      ("chunk size 怎么调而不影响线上？", "在临时集合 kb_tune_cs400 / 800 里按新的大小重新切分和向量化，用同一批题对比，线上集合和 SQL 都不动。")]),
]

CV_FIX = """## 简历和代码对齐的说明

- v21 已把「权限与安全」里的飞书免登、通讯录、变更事件改成公司 SSO（OIDC）、部门路径 / 职务 / 职级、SSO 注销 + 每日目录对账、内网 + VPN。项目二（跟单 Workflow）里的飞书是消息通知，没有改。
- 简历写的「每路 Top10、截断到 Top3」「MinIO 存解析后的 Markdown」「MongoDB 记录 RAG 查询日志，Bad Case 从这里筛」，代码已经按简历改好（`retrieve.py` 的 RECALL / FINAL、入库图的 `node_archive`、`query/querylog.py`）。
- Milvus 版本：本地跑的是 2.6（内置 BM25 从 2.5 开始有），简历写 2.5 没问题。
- 项目成果里的数字是公司上线数据；本仓库演示环境（约 60 份模拟文档）的实测数字在 RAG-02，面试时分清两者。
"""

DEMO = """## 演示环境里哪些是模拟的

| 简历里写的 | 演示环境 |
|---|---|
| Qwen3-32B（vLLM） | DeepSeek 官方接口代替（OpenAI 兼容，换地址即可）；vLLM 部署文件在 `deploy/vllm/`，没在 GPU 上验证 |
| Qwen-VL（vLLM） | 代码接好了；当前没有可用的视觉模型，图片只上传不写描述，缺项图纸转人工复核 |
| 公司 SSO、内部目录 | 模拟网关 8096 上的 OIDC + SCIM（`app/kbase/auth/sso_mock.py`），流程和字段按标准写 |
| 金蝶 ERP | 模拟网关 8096 上的 EAS WebService |
| 3,000 份文档 | 约 60 份模拟文档、300 多个切片 |

## 阅读顺序

1. `docs/RAG-06-v3重构说明.md`（这次改了什么、为什么）
2. 入库图 `app/kbase/ingest/graph.py` 开头的图 → `versioning.py` 开头的协议
3. 问答图 `app/kbase/query/graph.py` 开头的图 → `retrieve.py` → `answer.py`
4. 权限 `app/kbase/authz.py` → `auth/principal.py` → `auth/sso.py`
5. 图纸 `app/kbase/ingest/cad.py`
6. 测试 `tests/test_kbase_v3.py`（每个测试名就是一条规则）
"""


def main() -> None:
    parts = ["# 企业知识库（RAG）：简历与代码对照", "",
             "> 由 `assistant/scripts/make_rag_cv_map.py` 生成，行号来自当前代码；改了代码后重新运行一次就会更新。简历原文取自 `CHEN_Carl_Ze_CV_v21.docx`。", ""]
    for title, cv, short, rows, demo, qa in SECTIONS:
        parts += [f"## {title}", "", f"**简历原文**：{cv}", "", f"**一句话讲法**：{short}", "", table(rows), "", f"**怎么演示**：{demo}", "", "**可能被追问**", ""]
        parts += [f"- **{q}** {a}" for q, a in qa]
        parts.append("")
    parts += [CV_FIX, DEMO]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"已生成 {OUT}，{sum(len(s[3]) for s in SECTIONS)} 处代码位置")


if __name__ == "__main__":
    main()
