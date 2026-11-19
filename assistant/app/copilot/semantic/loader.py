"""语义层加载：视图说明（含权限）、口径库、分析模板。YAML 是唯一的维护入口，改完重启即生效。"""
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.copilot import db
from app.shared.runtime.logger import logger

_DIR = Path(__file__).parent


@dataclass
class ViewDef:
    name: str
    title: str
    desc: str = ""
    questions: list[str] = field(default_factory=list)
    date_col: str | None = None
    fresh_as: str | None = None                                   # 数据截止日期跟随哪张视图
    access: dict[str, str] = field(default_factory=dict)          # 角色 → all / dept / self
    row_scope: dict[str, str] = field(default_factory=dict)       # dept_id / dept_name / line_code / employee_id → 列名
    sensitive: dict[str, set[str]] = field(default_factory=dict)  # 敏感列 → 能看的角色
    mask: dict[str, set[str]] = field(default_factory=dict)       # 打码列 → 能看明文的角色
    columns: dict[str, str] = field(default_factory=dict)         # 列 → 中文说明（按数据库里的实际列顺序）


@dataclass
class Metric:
    name: str
    type: str
    formula: str
    aliases: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    view: str | None = None
    expr: str | None = None
    note: str | None = None
    dims: list[str] = field(default_factory=list)
    unit: str = ""
    sensitivity: str = "normal"
    templates: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"{self.name}：{self.formula}"]
        if self.view and self.expr:
            parts.append(f"来源 {self.view}，表达式 {self.expr}")
        if self.note:
            parts.append(self.note)
        return "；".join(parts)


@dataclass
class Template:
    id: str
    name: str
    intent: str
    desc: str
    sql: str
    time: str = "range"
    kind: str = "agg"
    metrics: list[str] = field(default_factory=list)
    dims: dict[str, str] = field(default_factory=dict)
    fixed: list[str] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)
    default_group: list[str] = field(default_factory=list)
    default_top: int | None = None
    roles: list[str] = field(default_factory=list)
    order: str | None = None
    chart: str = "bar"
    examples: list[str] = field(default_factory=list)


class Semantic:
    def __init__(self):
        self._lock = threading.Lock()
        self._loaded = False
        self.views: dict[str, ViewDef] = {}
        self.metrics: list[Metric] = []
        self.templates: dict[str, Template] = {}
        self.data_until: dict[str, str] = {}        # 视图 → 日期列的最大值（数据截止日期）

    def load(self, with_db: bool = True) -> "Semantic":
        with self._lock:
            if self._loaded:
                return self
            raw = yaml.safe_load((_DIR / "views.yaml").read_text(encoding="utf-8"))
            for name, v in raw.items():
                self.views[name] = ViewDef(
                    name=name, title=v["title"], desc=v.get("desc", ""), questions=v.get("questions", []),
                    date_col=v.get("date_col"), fresh_as=v.get("fresh_as"), access=v.get("access", {}), row_scope=v.get("row_scope", {}),
                    sensitive={c: set(r) for c, r in (v.get("sensitive") or {}).items()},
                    mask={c: set(r) for c, r in (v.get("mask") or {}).items()}, columns=dict(v.get("columns") or {}))
            self.metrics = [Metric(**m) for m in yaml.safe_load((_DIR / "metrics.yaml").read_text(encoding="utf-8"))]
            self.templates = {t["id"]: Template(**t) for t in yaml.safe_load((_DIR / "templates.yaml").read_text(encoding="utf-8"))}
            if with_db:
                self._merge_db_columns()
            self._loaded = True
            logger.info(f"语义层已加载：{len(self.views)} 个视图、{len(self.metrics)} 个指标、{len(self.templates)} 个模板")
            return self

    def _merge_db_columns(self):
        """以数据库里的实际列为准：YAML 里写了但库里没有的列丢弃，库里有但没写说明的列保留列名"""
        rows = db.meta_query(
            "SELECT table_name AS t, column_name AS c FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name IN %s ORDER BY table_name, ordinal_position", (list(self.views),))
        actual: dict[str, list[str]] = {}
        for r in rows:
            actual.setdefault(r["t"], []).append(r["c"])
        for name in [n for n in self.views if not actual.get(n)]:
            logger.warning(f"白名单视图 {name} 在数据库里不存在，已从可查询范围里去掉")
            self.views.pop(name)
        for name, v in self.views.items():
            cols = actual[name]
            v.columns = {c: v.columns.get(c, "") for c in cols}
            v.sensitive = {c: r for c, r in v.sensitive.items() if c in v.columns}
            v.mask = {c: r for c, r in v.mask.items() if c in v.columns}
        self.refresh_data_until()

    def refresh_data_until(self):
        for name, v in self.views.items():
            if v.date_col and v.date_col in v.columns:
                try:
                    r = db.meta_query(f"SELECT MAX(`{v.date_col}`) AS d FROM `{name}`")
                    self.data_until[name] = str(r[0]["d"]) if r and r[0]["d"] else ""
                except Exception as e:  # 数据截止日期只是提示信息，取不到不影响主流程
                    logger.warning(f"读取 {name} 的数据截止日期失败：{e}")
        for name, v in self.views.items():
            if v.fresh_as and self.data_until.get(v.fresh_as):
                self.data_until[name] = self.data_until[v.fresh_as]

    def metric(self, name: str) -> Metric | None:
        """按名称或别名找指标"""
        for m in self.metrics:
            if name == m.name or name in m.aliases:
                return m
        return None

    def find_metrics(self, text: str) -> list[Metric]:
        """问题里出现了哪些指标（名称或别名的字面匹配，名称长的优先）"""
        hits = []
        for m in self.metrics:
            keys = sorted([m.name, *m.aliases], key=len, reverse=True)
            pos = next((text.find(k) for k in keys if k in text), -1)
            if pos >= 0:
                hits.append((m.name != next(k for k in keys if k in text), -len(next(k for k in keys if k in text)), m))
        return [m for *_, m in sorted(hits, key=lambda x: x[:2])]


semantic = Semantic()
