"""
统一授权：向量检索、SQL 参数、父章节展开、图片签名链接、文档列表，全部用同一个 Scope 判断能不能看。

  一个人的可见范围 = 「身份标记」+「密级上限」
    身份标记  ALL（全员）、角色名（ENGINEER）、部门标记 D:<部门编号，来自公司 SSO>（包含所在部门的全部上级部门，实现沿组织树继承）
    密级上限  0 公开 / 1 内部 / 2 机密 / 3 绝密
  一份文档能看 = 文档已标注（有可见范围、有密级） 且 文档密级 ≤ 密级上限 且 文档的可见范围和身份标记有交集
  没标注的文档谁都看不到（默认拒绝），入库时也会拒收

  文档的可见范围写成逗号分隔的标记，例如「D:od-rd,QUALITY」= 研发中心（含下属部门）和质量角色可见。
  同一个 Scope 生成三种过滤条件：Milvus 表达式（检索前置过滤）、SQL 条件、Python 判断（图片链接等单条判断）。
  问答缓存的 key 用 Scope.key()：可见范围相同的人共享缓存，不同的人互不串。
"""
import hashlib
from dataclasses import dataclass, field

from app.infra.vector_store.milvus_gateway import escape

LEVELS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "SECRET": 3}
LEVEL_CN = {0: "公开", 1: "内部", 2: "机密", 3: "绝密"}
# MiniERP 账号（不经过 SSO）按角色给密级上限。研发、质量、管理层能看机密资料；其他角色到内部为止
ROLE_CLEARANCE = {"ADMIN": 3, "ENGINEER": 2, "QUALITY": 2, "MANAGEMENT": 2}
DEFAULT_CLEARANCE = 1


def level_of(security_level: str | None) -> int | None:
    return LEVELS.get((security_level or "").upper())


def parse_acl(acl_groups: str | None) -> list[str]:
    return [t.strip() for t in (acl_groups or "").split(",") if t.strip()]


@dataclass(frozen=True)
class Scope:
    user: str
    tokens: tuple[str, ...]                   # 已排序、去重
    clearance: int
    admin: bool = False                       # 管理员：所有已标注的文档都能看（未标注的照样看不到）
    source: str = "erp"                       # erp：MiniERP 账号；sso：公司 SSO
    employee_no: str | None = None
    notes: tuple[str, ...] = field(default=())  # 解析身份时的异常说明（用了缓存、没匹配到角色……），展示给用户

    @staticmethod
    def build(user: str, tokens, clearance: int, **kw) -> "Scope":
        return Scope(user, tuple(sorted({"ALL", *tokens})), int(clearance), **kw)

    @staticmethod
    def for_role(role: str, user: str = "") -> "Scope":
        role = (role or "").upper()
        return Scope.build(user or role, [role] if role else [], ROLE_CLEARANCE.get(role, DEFAULT_CLEARANCE), admin=role == "ADMIN")

    @property
    def role(self) -> str:
        """兼容旧代码：第一个角色标记（不是 ALL、不是部门）"""
        return "ADMIN" if self.admin else next((t for t in self.tokens if t != "ALL" and not t.startswith("D:")), "")

    def key(self) -> str:
        """缓存用：可见范围相同 → key 相同"""
        raw = "|".join(self.tokens) + f"#{self.clearance}#{int(self.admin)}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    # ---------- 三种过滤条件 ----------

    def milvus_expr(self) -> str:
        """检索前置过滤：先过滤再取 TopK。未标注的切片 level 是 -1，永远不满足 level >= 0"""
        lvl = f"level >= 0 and level <= {self.clearance}"
        if self.admin:
            return lvl
        return f'{lvl} and array_contains_any(acl, [{", ".join(chr(34) + escape(t) + chr(34) for t in self.tokens)}])'

    def sql(self, alias: str = "d") -> tuple[str, list]:
        """SQL 条件和参数，拼在 WHERE 后面。kb_document 的 sec_level 是数字密级，acl_groups 是逗号分隔的标记"""
        base = f"({alias}.acl_groups <> '' AND {alias}.sec_level IS NOT NULL AND {alias}.sec_level <= %s"
        if self.admin:
            return base + ")", [self.clearance]
        ors = " OR ".join(f"FIND_IN_SET(%s, {alias}.acl_groups)" for _ in self.tokens)
        return f"{base} AND ({ors}))", [self.clearance, *self.tokens]

    def can_view(self, security_level: str | int | None, acl_groups: str | None) -> bool:
        lvl = security_level if isinstance(security_level, int) else level_of(security_level)
        acl = parse_acl(acl_groups)
        if lvl is None or not acl or lvl > self.clearance:
            return False
        return self.admin or bool(set(acl) & set(self.tokens))


def as_scope(x) -> Scope:
    """检索函数既接受 Scope，也接受角色名（评测和旧代码传的是角色名）"""
    return x if isinstance(x, Scope) else Scope.for_role(str(x or ""))
