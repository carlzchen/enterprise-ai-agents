"""
产品知识图谱（Neo4j）：把型号、系列、物料、文档之间的关系建成图，回答需要"多跳"的问题。
  (Product)-[:IN_SERIES]->(Series)        (Product)-[:USES {qty}]->(Material)      (Material)-[:USES]->(Material)
  (Document)-[:DESCRIBES]->(Product)      (Product)-[:HAS_PARAM {value}]->(Param)
典型问题：某个物料用在哪些产品上（要穿过半成品这一层）；两个型号共用哪些物料；这个型号有哪些资料。
向量检索答不了这类问题，因为答案不在任何一段文字里，而在关系里。
数据来自金蝶镜像表和知识库的结构化表，可以随时全量重建。Neo4j 连不上时问答自动跳过这一路。
"""
import re
import threading

from app.infra.config.settings import settings
from app.kbase import store
from app.shared.runtime.logger import logger

_driver = None
_lock = threading.Lock()
_down_until = 0.0


def driver():
    global _driver, _down_until
    import time
    with _lock:
        if _driver is None:
            if time.time() < _down_until:
                return None
            try:
                from neo4j import GraphDatabase
                d = GraphDatabase.driver(settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password), connection_timeout=2)
                d.verify_connectivity()
                _driver = d
            except Exception as e:
                _down_until = time.time() + 120               # 连不上时两分钟内不再尝试，避免每次提问都等超时
                logger.warning(f"[kbase] Neo4j 不可用，跳过知识图谱：{str(e)[:80]}")
                return None
    return _driver


def available() -> bool:
    return driver() is not None


def rebuild() -> dict:
    d = driver()
    if d is None:
        raise RuntimeError("Neo4j 不可用")
    mats = store.query("SELECT f_number, f_name, f_specification, f_group, f_erp_cls FROM kd_material")
    boms = store.query("SELECT f_parent, f_child, f_numerator / NULLIF(f_denominator, 0) AS qty, f_unit FROM kd_bom")
    docs = store.query("SELECT doc_key, title, doc_type, product_code, active_version FROM kb_document WHERE active_version IS NOT NULL")
    params = store.query("SELECT DISTINCT p.model, p.param_key, p.param_name, p.value_text FROM kb_param p JOIN kb_document d "
                         "ON d.doc_key = p.doc_key AND d.active_version = p.version WHERE d.doc_type IN ('DATASHEET', 'DRAWING')")
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        for label in ("Product", "Material", "Document", "Series"):
            s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.code IS UNIQUE")
        s.run("UNWIND $rows AS r MERGE (n:Product {code: r.f_number}) SET n.name = r.f_name, n.spec = r.f_specification "
              "WITH n, r MERGE (s:Series {code: replace(r.f_group, '成品-', '')}) MERGE (n)-[:IN_SERIES]->(s)",
              rows=[m for m in mats if m["f_group"].startswith("成品-")])
        s.run("UNWIND $rows AS r MERGE (n:Material {code: r.f_number}) SET n.name = r.f_name, n.spec = r.f_specification, n.group = r.f_group",
              rows=[m for m in mats if not m["f_group"].startswith("成品-")])
        s.run("UNWIND $rows AS r MATCH (a {code: r.f_parent}) MATCH (b:Material {code: r.f_child}) MERGE (a)-[u:USES]->(b) SET u.qty = r.qty, u.unit = r.f_unit",
              rows=[{**b, "qty": float(b["qty"] or 0)} for b in boms])
        s.run("UNWIND $rows AS r MERGE (d:Document {code: r.doc_key}) SET d.title = r.title, d.type = r.doc_type, d.version = r.active_version "
              "WITH d, r MATCH (p:Product {code: r.product_code}) MERGE (d)-[:DESCRIBES]->(p)", rows=[x for x in docs if x["product_code"]])
        s.run("UNWIND $rows AS r MATCH (p:Product {code: r.model}) MERGE (k:Param {code: r.param_key}) SET k.name = r.param_name "
              "MERGE (p)-[h:HAS_PARAM]->(k) SET h.value = r.value_text", rows=params)
        n = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        e = s.run("MATCH ()-[r]->() RETURN count(r) AS n").single()["n"]
    logger.info(f"[kbase] 知识图谱已重建：{n} 个节点，{e} 条关系")
    return {"nodes": n, "edges": e}


_MATERIAL = re.compile(r"\b((?:M|SA)-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b")


def _fact(title: str, text: str) -> dict:
    return {"type": "kg", "kind": "graph", "doc_key": None, "version": None, "title": title, "section": "产品知识图谱", "page": None,
            "chunk_id": None, "content": text, "score": 1.0}


def search(question: str, models: list[str]) -> list[dict]:
    """按问题的说法套用几种图查询。没有命中任何模式时返回空，不影响其他检索"""
    d = driver()
    if d is None:
        return []
    out = []
    try:
        with d.session() as s:
            mat = _MATERIAL.search(question.upper())
            name = None
            if not mat:                                       # 没给物料编码：按名称找（磁钢、编码器、轴承……）
                m = re.search(r"(磁钢|编码器|轴承|漆包线|硅钢|转轴|端子|PCB|转子组件|定子组件|机壳|端盖)", question)
                name = m.group(1) if m else None
            if (mat or name) and re.search(r"哪些(产品|型号|电机)|用在|用到|影响|涉及|where", question):
                rows = s.run("MATCH (p:Product)-[:USES*1..3]->(m:Material) WHERE " + ("m.code = $v" if mat else "m.name CONTAINS $v") +
                             " RETURN m.code AS mc, m.name AS mn, collect(DISTINCT p.code) AS products ORDER BY size(products) DESC LIMIT 5",
                             v=mat.group(1) if mat else name).data()
                for r in rows:
                    out.append(_fact(f"物料 {r['mc']} 的使用范围", f"物料 {r['mc']}（{r['mn']}）直接或通过半成品用在 {len(r['products'])} 个产品上："
                                     + "、".join(sorted(r["products"]))))
            if len(models) >= 2 and re.search(r"共用|相同|一样|通用|区别|不同", question):
                r = s.run("MATCH (a:Product {code: $a})-[:USES*1..3]->(m:Material)<-[:USES*1..3]-(b:Product {code: $b}) "
                          "RETURN collect(DISTINCT m.code + ' ' + m.name) AS shared", a=models[0], b=models[1]).single()
                only = s.run("MATCH (a:Product {code: $a})-[:USES*1..3]->(m:Material) WHERE NOT (:Product {code: $b})-[:USES*1..3]->(m) "
                             "RETURN collect(DISTINCT m.code + ' ' + m.name) AS only", a=models[0], b=models[1]).single()
                out.append(_fact(f"{models[0]} 与 {models[1]} 的物料对比",
                                 f"两者共用的物料（{len(r['shared'])} 种）：" + "、".join(sorted(r["shared"])) +
                                 f"。只有 {models[0]} 使用的（{len(only['only'])} 种）：" + ("、".join(sorted(only["only"])) or "无")))
            if len(models) == 1 and re.search(r"同系列|同一系列|还有哪些型号|哪些资料|有哪些文档|相关文档", question):
                r = s.run("MATCH (p:Product {code: $c})-[:IN_SERIES]->(s)<-[:IN_SERIES]-(o:Product) RETURN s.code AS series, collect(o.code) AS others", c=models[0]).single()
                docs = s.run("MATCH (d:Document)-[:DESCRIBES]->(:Product {code: $c}) RETURN collect(d.title + '（' + d.code + '）') AS docs", c=models[0]).single()
                if r:
                    out.append(_fact(f"{models[0]} 的关联信息", f"{models[0]} 属于 {r['series']} 系列，同系列还有：" + "、".join(sorted(r["others"])) +
                                     "。知识库里关于它的文档：" + ("、".join(docs["docs"]) or "无")))
    except Exception as e:
        logger.warning(f"[kbase] 知识图谱查询失败，本次跳过：{str(e)[:120]}")
    return out
