"""
补齐组织和人员（演示数据，全部虚构）：
  1. 占位名换成正常姓名：员工档案、系统账号显示名，以及所有表里引用这些名字的文字（审批人、检验员、凭证制单人、8D 小组……）
     客户联系人（「王经理」「黄工」「Purchasing Manager」）也换成正常姓名；设备报修的报修人「班组长」换成具体的班组长
  2. 补职能部门（证券部、审计部、法务部、战略投资部、市场部、信息部两个组、研发中心五个部门、计划物控部、工艺工程部）和岗位
  3. 新增 300 多名职能和研发员工（高管按上市公司常见架构：董事长、总经理、副总经理、财务总监、董事会秘书）
  4. 所有员工补 HR 常用信息：邮箱、学历、专业、毕业院校、籍贯、婚姻状况、办公地点、办公电话（hr_employee_profile）；
     家庭住址、紧急联系人（hr_employee_pii，和身份证号、工资卡号、手机号放一起）
  5. 员工花名册视图 v_employee_roster：每个敏感字段同时给打码版（所有人可见）和明文版（只有人事和管理员能看，由后端按角色隐藏）

用法：ROOTPW=... PYTHONPATH=. .venv/bin/python scripts/seed_org_people.py        （先自己备份数据库；脚本可以重复执行，结果一样）
所有姓名、电话、住址、身份证号都是随机生成的，不对应任何真实的人；邮箱用保留域名 keli.example.com。
"""
import json
import os
import random
from datetime import date, timedelta

import pymysql
from pypinyin import lazy_pinyin

from app.infra.config.settings import settings

RNG = random.Random(20261016)
TODAY = date(2026, 10, 16)
DOMAIN = "keli.example.com"

RENAME = {"林总经理": "林建国", "陈总": "陈思敏", "刘会计": "刘婷", "何人事": "何雅琴", "唐行政": "唐晓雯", "系统管理员": "梁启航", "周采购": "周文斌",
          "王销售": "王浩然", "Amy 许": "许诗雨", "林工": "林志远", "陈工": "陈家豪", "吴品质": "吴志刚", "孙计划": "孙宏伟", "罗主任": "罗家明",
          "邓主任": "邓建华", "赵仓管": "赵国强", "冯设备": "冯立新", "马服务": "马晓峰", "钱安环": "钱永康", "郑检验": "郑凯"}

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马胡朱郭何罗高林郑梁谢唐许冯宋韩邓彭曹曾田萧潘袁蔡蒋余杜叶程魏苏吕丁沈任姚卢钟姜崔谭陆范汪廖石金贾夏方邹熊白孟秦邱侯江尹薛段雷黎史陶贺毛郝顾龚邵万覃武钱戴严莫孔向常汤康易乔赖文施洪柯庄")
MALE = list("伟强磊军洋勇杰涛明超辉刚平鹏飞华宇浩凯健斌波俊峰帆鑫龙亮海成宁博文晨昊阳然东旭立志嘉睿哲豪鸿彬晖晟毅航铭松林森栋楠骏琦钧锋")
FEMALE = list("静敏丽娟艳芳娜秀英慧颖婷雪琳倩晶燕玲洁欣怡佳萱悦雯露蕾璐瑶琪彤晴妍岚薇楠莹菲茜蓉霞凤梅兰洋璇诗梦雅馨涵")


class Names:
    def __init__(self, taken: set[str]):
        self.taken = set(taken)

    def make(self, gender: str) -> str:
        pool = MALE if gender == "M" else FEMALE
        for _ in range(1000):
            n = RNG.choice(SURNAMES) + RNG.choice(pool) + (RNG.choice(pool) if RNG.random() < 0.72 else "")
            if n not in self.taken and len(set(n[1:])) == len(n[1:]):
                self.taken.add(n)
                return n
        raise RuntimeError("名字用完了")


# ---------------------------------------------------------------- 部门、岗位

NEW_DEPTS = [  # 编码, 名称, 上级, 成本归集, 排序
    ("SEC", "证券部", "GM", "ADMIN", 2), ("AUD", "审计部", "GM", "ADMIN", 3), ("LEG", "法务部", "GM", "ADMIN", 4), ("STR", "战略投资部", "GM", "ADMIN", 5),
    ("MKT", "市场部", "GM", "SALES", 21), ("IT-APP", "应用与数据组", "IT", "ADMIN", 51), ("IT-INF", "基础架构与安全组", "IT", "ADMIN", 52),
    ("RND-MD", "电机设计部", "RND", "RND", 91), ("RND-SW", "驱控软件部", "RND", "RND", 92), ("RND-HW", "硬件电子部", "RND", "RND", 93),
    ("RND-TV", "测试验证部", "RND", "RND", 94), ("RND-PM", "项目管理部", "RND", "RND", 95),
    ("PMC", "计划物控部", "PRD", "PRODUCTION", 111), ("PE", "工艺工程部", "PRD", "PRODUCTION", 112)]

NEW_POSITIONS = [  # 编码, 名称, 职级, 是否管理岗
    ("CHAIR", "董事长", "M5", 1), ("VP", "副总经理", "M4", 1), ("CFO", "财务总监", "M4", 1), ("BODSEC", "董事会秘书", "M4", 1), ("LEADER", "主管", "M1", 1),
    ("ASSIST", "总经理助理", "P4", 0), ("SECREP", "证券事务代表", "P4", 0), ("AUDITOR", "审计专员", "P3", 0), ("LEGAL", "法务专员", "P3", 0),
    ("ANALYST", "投资分析师", "P4", 0), ("MKTSP", "市场专员", "P2", 0), ("BRAND", "品牌经理", "P4", 0), ("PMM", "产品经理", "P4", 0),
    ("SWE", "软件工程师", "P3", 0), ("HWE", "硬件工程师", "P3", 0), ("ME", "电机设计工程师", "P3", 0), ("TE", "测试工程师", "P3", 0),
    ("PMGR", "项目经理", "P4", 0), ("ARCH", "系统架构师", "P5", 0), ("PEENG", "工艺工程师", "P3", 0), ("MC", "物控专员", "P2", 0),
    ("DATA", "数据分析师", "P3", 0), ("DEV", "开发工程师", "P3", 0), ("OPS", "运维工程师", "P3", 0), ("SECENG", "信息安全工程师", "P4", 0),
    ("HRBP", "人力资源业务伙伴", "P3", 0), ("RECRUIT", "招聘专员", "P2", 0), ("CNB", "薪酬绩效专员", "P3", 0), ("CASHIER", "出纳", "P2", 0),
    ("TAXACC", "税务会计", "P3", 0), ("COSTACC", "成本会计", "P3", 0), ("BUYER", "采购工程师", "P3", 0), ("SQE", "供应商质量工程师", "P3", 0),
    ("SALESREP", "销售代表", "P2", 0), ("KAM", "大客户经理", "P4", 0), ("FAE", "应用工程师", "P3", 0), ("CS", "客服专员", "P2", 0),
    ("EHSENG", "安全工程师", "P3", 0), ("RECEPT", "前台", "P1", 0), ("DRIVER", "司机", "O2", 0)]

# 每个部门新增：负责人（姓名, 性别, 岗位编码, 可为 None 表示沿用现有负责人）+ 员工（岗位编码, 人数）
STAFF = {
    "GM": (None, [("ASSIST", 2), ("SPEC", 3)]),
    "SEC": (("顾文澜", "F", "BODSEC"), [("SECREP", 1), ("SPEC", 2)]),
    "AUD": (("邓思远", "M", "MGR"), [("AUDITOR", 5)]),
    "LEG": (("韩雪", "F", "MGR"), [("LEGAL", 3)]),
    "STR": (("宋立衡", "M", "MGR"), [("ANALYST", 3)]),
    "FIN": (None, [("ACC", 7), ("CASHIER", 2), ("TAXACC", 2), ("COSTACC", 5), ("SPEC", 4)]),
    "HR": (None, [("HRBP", 3), ("RECRUIT", 3), ("CNB", 2), ("SPEC", 2), ("CLERK", 1)]),
    "ADM": (None, [("SPEC", 4), ("RECEPT", 2), ("DRIVER", 2), ("CLERK", 1)]),
    "IT": (None, [("SPEC", 1)]),
    "IT-APP": (("钟逸飞", "M", "LEADER"), [("DEV", 8), ("DATA", 3), ("ARCH", 1), ("SPEC", 3)]),
    "IT-INF": (("彭浩然", "M", "LEADER"), [("OPS", 5), ("SECENG", 2), ("TECH", 2)]),
    "PUR": (None, [("BUYER", 6), ("SPEC", 3), ("SQE", 2)]),
    "SAL": (None, [("SALESREP", 14), ("KAM", 5), ("SALES", 4), ("SPEC", 3), ("FAE", 4)]),
    "EXP": (None, [("SALESREP", 5), ("SPEC", 2)]),
    "SVC": (None, [("CS", 5), ("FAE", 2)]),
    "MKT": (("苏晴", "F", "MGR"), [("BRAND", 1), ("PMM", 4), ("MKTSP", 6)]),
    "RND": (None, [("ARCH", 1), ("SPEC", 1)]),
    "RND-MD": (("杜明哲", "M", "MGR"), [("ME", 16), ("ENG_SR", 3)]),
    "RND-SW": (("曾睿", "M", "MGR"), [("SWE", 16), ("ENG_SR", 3)]),
    "RND-HW": (("袁博文", "M", "MGR"), [("HWE", 13), ("ENG_SR", 2)]),
    "RND-TV": (("潘慧", "F", "MGR"), [("TE", 11)]),
    "RND-PM": (("蒋思远", "M", "MGR"), [("PMGR", 6)]),
    "QA": (None, [("QE", 9), ("INSP", 6), ("SPEC", 3)]),
    "PRD": (None, [("SPEC", 2)]),
    "PMC": (("谭立新", "M", "MGR"), [("PLAN", 8), ("MC", 5)]),
    "PE": (("覃志强", "M", "MGR"), [("PEENG", 13)]),
    "LOG": (None, [("WH", 4), ("SPEC", 2)]),
    "EQP": (None, [("TECH", 3), ("ENG", 2)]),
    "EHS": (None, [("EHSENG", 3)]),
}
EXECS = [("罗振邦", "M", "CHAIR", "GM"), ("唐国栋", "M", "VP", "GM")]          # 另外两位：总经理林建国（原占位名）、财务总监陈思敏（原占位名，改任财务总监岗）
HQ = {"GM", "SEC", "AUD", "LEG", "STR", "FIN", "HR", "IT", "IT-APP", "IT-INF", "SAL", "EXP", "MKT", "RND", "RND-MD", "RND-SW", "RND-HW", "RND-TV", "RND-PM"}

SALARY = {"O1": (4500, 6000), "O2": (5000, 6800), "O3": (6000, 8000), "P1": (5000, 6500), "P2": (6500, 9500), "P3": (9000, 15000), "P4": (14000, 22000),
          "P5": (20000, 30000), "P6": (26000, 38000), "M1": (12000, 18000), "M2": (16000, 26000), "M3": (25000, 38000), "M4": (38000, 60000), "M5": (60000, 90000)}
AGE = {"O1": (20, 50), "O2": (24, 50), "O3": (26, 50), "P1": (21, 30), "P2": (22, 34), "P3": (24, 40), "P4": (28, 44), "P5": (32, 50), "P6": (35, 52),
       "M1": (28, 45), "M2": (30, 50), "M3": (35, 54), "M4": (40, 58), "M5": (48, 62)}
EDU = {"O": [("中专", 4), ("高中", 4), ("大专", 2)], "P1": [("大专", 6), ("本科", 4)], "P2": [("大专", 3), ("本科", 7)],
       "P": [("本科", 7), ("硕士", 3)], "P5": [("本科", 4), ("硕士", 5), ("博士", 1)], "M": [("本科", 6), ("硕士", 4)]}
SCHOOLS = ["湖南大学", "中南大学", "湘潭大学", "长沙理工大学", "湖南科技大学", "华南理工大学", "深圳大学", "广东工业大学", "南方科技大学", "哈尔滨工业大学",
           "华中科技大学", "武汉理工大学", "西安交通大学", "东南大学", "合肥工业大学", "江西理工大学", "桂林电子科技大学", "湖南工业大学", "永州职业技术学院", "深圳职业技术大学"]
MAJORS = {"RND": ["电气工程及其自动化", "电机与电器", "机械设计制造及其自动化", "自动化", "电子信息工程", "软件工程", "控制科学与工程"],
          "IT": ["计算机科学与技术", "软件工程", "信息管理与信息系统", "网络工程", "数据科学与大数据技术"],
          "FIN": ["会计学", "财务管理", "审计学", "金融学"], "SAL": ["市场营销", "国际经济与贸易", "工商管理", "电气工程及其自动化"],
          "HR": ["人力资源管理", "行政管理", "心理学"], "LEG": ["法学"], "PRD": ["机械设计制造及其自动化", "工业工程", "材料成型及控制工程", "物流管理"],
          "QA": ["质量管理工程", "机械工程", "材料科学与工程"], "*": ["工商管理", "行政管理", "汉语言文学", "英语", "物流管理"]}
PROVINCES = [("湖南", ["永州", "长沙", "衡阳", "邵阳", "郴州", "株洲"]), ("广东", ["深圳", "广州", "湛江", "梅州", "茂名"]), ("江西", ["赣州", "吉安", "南昌"]),
             ("广西", ["桂林", "柳州", "玉林"]), ("湖北", ["武汉", "荆州", "黄冈"]), ("四川", ["成都", "南充"]), ("河南", ["信阳", "南阳", "周口"])]
SZ = [("南山区", ["粤海街道", "桃源街道", "西丽街道", "南头街道"]), ("宝安区", ["西乡街道", "新安街道", "福永街道"]), ("福田区", ["梅林街道", "香蜜湖街道", "莲花街道"]),
      ("龙华区", ["民治街道", "龙华街道", "大浪街道"]), ("罗湖区", ["东门街道", "笋岗街道"])]
QY = ["浯溪街道", "龙山街道", "长虹街道", "黎家坪镇", "观音滩镇", "白水镇", "大忠桥镇"]
ROADS = ["学府路", "创业路", "科苑大道", "沙河西路", "留仙大道", "宝安大道", "新湖路", "梅林路", "民治大道", "人民北路", "滨江路", "建设路", "陶铸路", "祁阳大道"]
ESTATES = ["阳光花园", "碧海名苑", "翠湖山庄", "锦绣家园", "金域华府", "滨江华庭", "书香苑", "丽景湾", "御景豪庭", "怡景新村", "春华里", "和美家园"]
RELATION = ["配偶", "父亲", "母亲", "兄弟", "姐妹"]
REGION = {"湖南": "4311", "广东": "4403", "江西": "3607", "广西": "4503", "湖北": "4201", "四川": "5101", "河南": "4115"}


def pick(weighted):
    total = sum(w for _, w in weighted)
    r = RNG.uniform(0, total)
    for v, w in weighted:
        r -= w
        if r <= 0:
            return v
    return weighted[-1][0]


def id_card(region6: str, birth: date, seq: int, gender: str) -> str:
    """GB 11643 校验位；顺序码奇数为男、偶数为女"""
    s3 = seq % 999 + 1
    if (s3 % 2 == 1) != (gender == "M"):
        s3 = s3 + 1 if s3 < 999 else s3 - 1
    body = f"{region6}{birth:%Y%m%d}{s3:03d}"
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return body + "10X98765432"[sum(int(a) * b for a, b in zip(body, w)) % 11]


def phone() -> str:
    return RNG.choice(["138", "139", "135", "136", "150", "151", "158", "186", "187", "177", "199", "133", "189"]) + f"{RNG.randrange(10**8):08d}"


def home_address(hq: bool) -> str:
    if hq:
        dist, streets = RNG.choice(SZ)
        return f"广东省深圳市{dist}{RNG.choice(streets)}{RNG.choice(ROADS)}{RNG.randint(1, 399)}号{RNG.choice(ESTATES)}{RNG.randint(1, 18)}栋{RNG.randint(2, 32)}{RNG.randint(1, 8):02d}室"
    if RNG.random() < 0.75:
        return f"湖南省永州市祁阳市{RNG.choice(QY)}{RNG.choice(ROADS)}{RNG.randint(1, 280)}号{RNG.choice(ESTATES)}{RNG.randint(1, 12)}栋{RNG.randint(1, 18)}{RNG.randint(1, 6):02d}室"
    prov, cities = RNG.choice(PROVINCES)
    return f"{prov}{'' if prov == '广西' else '省'}{RNG.choice(cities)}市{RNG.choice(['城关镇', '新华街道', '解放街道', '东升镇'])}{RNG.choice(['光明村', '新民村', '红星组', '和平社区'])}{RNG.randint(1, 120)}号"


def email(name: str, used: set[str]) -> str:
    base = "".join(lazy_pinyin(name)).lower()
    e, i = base, 1
    while e in used:
        i += 1
        e = f"{base}{i}"
    used.add(e)
    return f"{e}@{DOMAIN}"


# ---------------------------------------------------------------- 主流程

def main() -> None:
    db = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user="root", password=os.environ["ROOTPW"], database="minierp",
                         charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    cur = db.cursor()

    # ---------- 1. 占位名 → 正常姓名（所有引用的地方一起改） ----------
    cur.execute("SELECT name FROM hr_employee")
    existing = {r["name"] for r in cur.fetchall()}
    clash = [v for k, v in RENAME.items() if v in existing and k not in existing]
    if clash:
        raise SystemExit(f"新名字和现有员工重名：{clash}")
    cur.execute("SELECT c.table_name t, c.column_name c FROM information_schema.columns c JOIN information_schema.tables t ON t.table_schema = c.table_schema "
                "AND t.table_name = c.table_name WHERE c.table_schema = 'minierp' AND t.table_type = 'BASE TABLE' "
                "AND c.data_type IN ('varchar','char','text','mediumtext','longtext') AND c.table_name <> 'crm_contact'")
    cols = cur.fetchall()
    changed = 0
    for col in cols:                                             # 先用一条查询看这一列有没有占位名，有才逐个替换
        t, c = col["t"], col["c"]
        cur.execute(f"SELECT 1 FROM `{t}` WHERE " + " OR ".join(f"`{c}` LIKE %s" for _ in RENAME) + " LIMIT 1", [f"%{k}%" for k in RENAME])
        if not cur.fetchone():
            continue
        for old, new in sorted(RENAME.items(), key=lambda kv: -len(kv[0])):
            changed += cur.execute(f"UPDATE `{t}` SET `{c}` = REPLACE(`{c}`, %s, %s) WHERE `{c}` LIKE %s", (old, new, f"%{old}%"))
    print(f"占位名替换：{changed} 处")

    names = Names(existing | set(RENAME.values()) | {n for n, *_ in EXECS} | {v[0] for v in [s[0] for s in STAFF.values()] if v})

    # 客户联系人：国内客户中文名，海外客户按国家起英文名
    western = {"美国": "US", "德国": "DE", "意大利": "IT", "法国": "FR", "英国": "GB", "西班牙": "ES", "土耳其": "TR", "韩国": "KR", "日本": "JP", "越南": "VN",
               "印度": "IN", "墨西哥": "MX", "巴西": "BR", "泰国": "TH"}
    first = {"US": ["Michael", "Jennifer", "David", "Sarah", "Kevin", "Emily"], "DE": ["Thomas", "Katrin", "Stefan", "Julia", "Markus"],
             "IT": ["Marco", "Giulia", "Luca", "Francesca"], "FR": ["Julien", "Camille", "Nicolas", "Claire"], "GB": ["James", "Charlotte", "Oliver", "Emma"],
             "ES": ["Javier", "Lucía", "Carlos"], "TR": ["Mehmet", "Ayşe", "Emre"], "KR": ["Min-jun", "Seo-yeon", "Ji-ho"], "JP": ["Takashi", "Yuki", "Kenji"],
             "VN": ["Minh", "Lan", "Tuan"], "IN": ["Rahul", "Priya", "Arjun"], "MX": ["Alejandro", "Sofía"], "BR": ["Rafael", "Camila"], "TH": ["Somchai", "Nicha"]}
    last = {"US": ["Miller", "Johnson", "Brown", "Davis", "Wilson"], "DE": ["Müller", "Schmidt", "Weber", "Fischer", "Wagner"], "IT": ["Rossi", "Bianchi", "Romano"],
            "FR": ["Martin", "Bernard", "Dubois"], "GB": ["Smith", "Taylor", "Clarke"], "ES": ["García", "Fernández", "López"], "TR": ["Yılmaz", "Kaya", "Demir"],
            "KR": ["Kim", "Lee", "Park"], "JP": ["Sato", "Suzuki", "Tanaka"], "VN": ["Nguyen", "Tran", "Le"], "IN": ["Sharma", "Patel", "Gupta"],
            "MX": ["Hernández", "Martínez"], "BR": ["Silva", "Santos"], "TH": ["Srisai", "Wongsa"]}
    cur.execute("SELECT ct.id, ct.name, m.country, m.is_domestic FROM crm_contact ct JOIN md_customer m ON m.id = ct.customer_id "
                "WHERE ct.name REGEXP '经理$|工$|总$|Manager|Engineer|Purchasing' ORDER BY ct.id")              # 只改还是占位名的
    contacts = cur.fetchall()
    for ct in contacts:
        if ct["is_domestic"] or ct["country"] in (None, "中国"):
            n = names.make(RNG.choice("MMMMF"))
        else:
            cc = western.get(ct["country"], "US")
            n = f"{RNG.choice(first[cc])} {RNG.choice(last[cc])}"
        cur.execute("UPDATE crm_contact SET name = %s WHERE id = %s", (n, ct["id"]))
    print(f"客户联系人：{len(contacts)} 人改成正常姓名")
    cur.execute("SELECT e.name FROM hr_employee e JOIN org_position p ON p.id = e.position_id WHERE p.code = 'LEAD' AND e.status <> 'LEFT'")
    leads = [r["name"] for r in cur.fetchall()]
    cur.execute("SELECT id FROM eam_work_order WHERE reporter = '班组长'")
    for r in cur.fetchall():
        cur.execute("UPDATE eam_work_order SET reporter = %s WHERE id = %s", (leads[r["id"] % len(leads)], r["id"]))

    # ---------- 2. 部门、岗位 ----------
    for code, name, grade, mgr in NEW_POSITIONS:
        cur.execute("INSERT INTO org_position (code, name, grade, is_manager) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE name = VALUES(name), grade = VALUES(grade)",
                    (code, name, grade, mgr))
    cur.execute("SELECT id, code, grade FROM org_position")
    pos = {r["code"]: r for r in cur.fetchall()}
    cur.execute("SELECT id, code FROM org_dept")
    dept = {r["code"]: r["id"] for r in cur.fetchall()}
    for code, name, parent, cost, sort in NEW_DEPTS:
        if code not in dept:
            cur.execute("INSERT INTO org_dept (code, name, parent_id, cost_type, sort_no) VALUES (%s,%s,%s,%s,%s)", (code, name, dept[parent], cost, sort))
            dept[code] = cur.lastrowid
    cur.execute("UPDATE hr_employee SET position_id = %s WHERE name = '陈思敏'", (pos["CFO"]["id"],))       # 原「陈总」任财务总监

    # ---------- 3. 新员工 ----------
    cur.execute("SELECT emp_no FROM hr_employee")
    seq: dict[str, int] = {}
    for r in cur.fetchall():
        yy, n = r["emp_no"][1:3], int(r["emp_no"][3:])
        seq[yy] = max(seq.get(yy, 0), n)
    cur.execute("SELECT COUNT(*) n FROM hr_employee WHERE emp_no IN (SELECT emp_no FROM hr_employee) AND name = '罗振邦'")
    if cur.fetchone()["n"]:
        print("新员工已经生成过，跳过第 3 步（只刷新补充信息）")
        new_people = []
    else:
        new_people = []

        def hire(name, gender, pcode, dcode, manager_id=None, status=None):
            grade = pos[pcode]["grade"]
            lo, hi = AGE[grade]
            age = RNG.randint(lo, hi)
            birth = date(TODAY.year - age, RNG.randint(1, 12), RNG.randint(1, 28))
            earliest = max(date(2012, 4, 1), date(birth.year + 22, 7, 1))
            if status == "PROBATION" or (status is None and RNG.random() < 0.06):
                hd, st = TODAY - timedelta(days=RNG.randint(20, 85)), "PROBATION"
            else:
                span = max(30, (date(2026, 7, 1) - earliest).days)
                hd, st = earliest + timedelta(days=RNG.randint(0, span)), "ACTIVE"
            yy = f"{hd.year % 100:02d}"
            seq[yy] = seq.get(yy, 0) + 1
            emp_no = f"E{yy}{seq[yy]:04d}"
            sal = round(RNG.uniform(*SALARY[grade]) / 100) * 100
            cur.execute("INSERT INTO hr_employee (emp_no, name, gender, birth_date, dept_id, position_id, manager_id, employment_type, wage_type, base_salary, "
                        "social_base, hire_date, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'FULL_TIME','MONTHLY',%s,%s,%s,%s)",
                        (emp_no, name, gender, birth, dept[dcode], pos[pcode]["id"], manager_id, sal, min(sal, 31938), hd, st))
            eid = cur.lastrowid
            new_people.append(eid)
            return eid

        cur.execute("SELECT id FROM hr_employee WHERE name = '林建国'")
        gm_id = cur.fetchone()["id"]
        chair = hire("罗振邦", "M", "CHAIR", "GM", status="ACTIVE")
        cur.execute("UPDATE hr_employee SET manager_id = %s WHERE id = %s", (chair, gm_id))
        hire("唐国栋", "M", "VP", "GM", gm_id, "ACTIVE")
        cur.execute("SELECT d.code, d.manager_emp_id FROM org_dept d")
        heads = {r["code"]: r["manager_emp_id"] for r in cur.fetchall()}
        parent_of = {c: p for c, _, p, _, _ in NEW_DEPTS}
        for dcode, (head, staff) in STAFF.items():
            head_id = heads.get(dcode)
            if head:
                hn, hg, hp = head
                names.taken.add(hn)
                up = heads.get(parent_of.get(dcode, "GM")) or gm_id
                head_id = hire(hn, hg, hp, dcode, up, "ACTIVE")
                cur.execute("UPDATE org_dept SET manager_emp_id = %s WHERE code = %s", (head_id, dcode))
                heads[dcode] = head_id
            for pcode, n in staff:
                for _ in range(n):
                    g = "F" if RNG.random() < (0.6 if pcode in ("ACC", "CASHIER", "HRBP", "RECRUIT", "CNB", "RECEPT", "CS", "MKTSP", "CLERK") else 0.3) else "M"
                    hire(names.make(g), g, pcode, dcode, head_id)
        print(f"新员工：{len(new_people)} 人")

    # ---------- 4. 补充信息（所有员工） ----------
    cur.execute("""CREATE TABLE IF NOT EXISTS hr_employee_profile (
      employee_id BIGINT NOT NULL PRIMARY KEY, email VARCHAR(80), education VARCHAR(10), major VARCHAR(40), school VARCHAR(40),
      native_place VARCHAR(30), marital_status VARCHAR(6), work_location VARCHAR(20), office_phone VARCHAR(30)
    ) COMMENT '员工补充信息（模拟数据）'""")
    for col, ddl in (("home_address", "VARCHAR(120) COMMENT '家庭住址（模拟）'"), ("emergency_contact", "VARCHAR(20)"),
                     ("emergency_relation", "VARCHAR(6)"), ("emergency_phone", "VARCHAR(11)")):
        cur.execute("SELECT COUNT(*) n FROM information_schema.columns WHERE table_schema = 'minierp' AND table_name = 'hr_employee_pii' AND column_name = %s", (col,))
        if not cur.fetchone()["n"]:
            cur.execute(f"ALTER TABLE hr_employee_pii ADD COLUMN {col} {ddl}")
    cur.execute("SELECT e.id, e.name, e.gender, e.birth_date, d.code dcode, p.grade FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id "
                "JOIN org_position p ON p.id = e.position_id ORDER BY e.id")
    everyone = cur.fetchall()
    used_mail: set[str] = set()
    rng_state = RNG.getstate()
    for e in everyone:
        RNG.seed(f"profile-{e['id']}")                                   # 每个人的补充信息只由 id 决定，重复执行结果一样
        grade, dcode = e["grade"], e["dcode"]
        office = grade[0] in "PM"
        hq = dcode in HQ or (office and RNG.random() < 0.3)
        edu = pick(EDU["O"] if grade[0] == "O" else EDU.get(grade, EDU["M"] if grade[0] == "M" else EDU["P"]))
        group = next((k for k in MAJORS if dcode.startswith(k)), "*")
        prov, cities = RNG.choice(PROVINCES if RNG.random() < 0.6 else PROVINCES[:1])
        city = RNG.choice(cities)
        birth = e["birth_date"] or date(1990, 1, 1)
        age = (TODAY - birth).days // 365
        cur.execute("INSERT INTO hr_employee_profile (employee_id, email, education, major, school, native_place, marital_status, work_location, office_phone) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE email = VALUES(email), education = VALUES(education), major = VALUES(major), "
                    "school = VALUES(school), native_place = VALUES(native_place), marital_status = VALUES(marital_status), work_location = VALUES(work_location), "
                    "office_phone = VALUES(office_phone)",
                    (e["id"], email(e["name"], used_mail) if office else None, edu,
                     RNG.choice(MAJORS[group]) if edu in ("大专", "本科", "硕士", "博士") else None,
                     RNG.choice(SCHOOLS) if edu in ("本科", "硕士", "博士") else (RNG.choice(SCHOOLS[-4:]) if edu == "大专" else None),
                     f"{prov}{city}", "已婚" if age >= 30 and RNG.random() < 0.8 or age >= 26 and RNG.random() < 0.3 else "未婚",
                     "深圳总部" if hq else "祁阳生产基地",
                     (f"0755-8{RNG.randint(1000000, 9999999)}" if hq else f"0746-3{RNG.randint(100000, 999999)}") if office else None))
        g = e["gender"]
        rel = RNG.choice(RELATION if age >= 28 else ["父亲", "母亲", "兄弟", "姐妹"])
        if rel == "配偶":
            cg = "F" if g == "M" else "M"
            cname = RNG.choice(SURNAMES) + RNG.choice(FEMALE if cg == "F" else MALE) + (RNG.choice(FEMALE if cg == "F" else MALE) if RNG.random() < 0.6 else "")
        else:
            cg = "M" if rel in ("父亲", "兄弟") else "F"
            cname = e["name"][0] + RNG.choice(FEMALE if cg == "F" else MALE) + (RNG.choice(FEMALE if cg == "F" else MALE) if RNG.random() < 0.6 else "")
        region = REGION.get(prov, "4311") + f"{RNG.randint(21, 29):02d}"
        cur.execute("INSERT INTO hr_employee_pii (employee_id, id_card, bank_account, phone, home_address, emergency_contact, emergency_relation, emergency_phone) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE home_address = VALUES(home_address), emergency_contact = VALUES(emergency_contact), "
                    "emergency_relation = VALUES(emergency_relation), emergency_phone = VALUES(emergency_phone)",      # 老员工原有的身份证号、卡号、手机号不动
                    (e["id"], id_card(region, birth, e["id"], g), f"622848{RNG.randrange(10**13):013d}", phone(), home_address(hq), cname, rel, phone()))
    RNG.setstate(rng_state)
    print(f"补充信息：{len(everyone)} 人")

    # ---------- 5. 花名册视图 ----------
    cur.execute("""CREATE OR REPLACE VIEW v_employee_roster AS
SELECT e.id, e.emp_no, e.name, IF(e.gender = 'M', '男', '女') AS gender, e.birth_date, TIMESTAMPDIFF(YEAR, e.birth_date, CURDATE()) AS age,
       e.dept_id, d.name AS dept_name, pd.name AS parent_dept, p.name AS position_name, p.grade, e.hire_date,
       TIMESTAMPDIFF(MONTH, e.hire_date, COALESCE(e.leave_date, CURDATE())) AS tenure_months, e.status, e.employment_type,
       pr.email, pr.education, pr.major, pr.school, pr.native_place, pr.marital_status, pr.work_location, pr.office_phone,
       CONCAT(LEFT(pi.phone, 3), '****', RIGHT(pi.phone, 4)) AS phone_masked,
       CONCAT(LEFT(pi.id_card, 6), '********', RIGHT(pi.id_card, 4)) AS id_card_masked,
       CONCAT(LEFT(pi.home_address, 9), '******') AS address_masked,
       pi.emergency_contact, pi.emergency_relation, CONCAT(LEFT(pi.emergency_phone, 3), '****', RIGHT(pi.emergency_phone, 4)) AS emergency_phone_masked,
       CONCAT(LEFT(pi.bank_account, 4), ' **** **** ', RIGHT(pi.bank_account, 4)) AS bank_masked,
       pi.phone, pi.id_card, pi.home_address, pi.emergency_phone, pi.bank_account
FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id LEFT JOIN org_dept pd ON pd.id = d.parent_id JOIN org_position p ON p.id = e.position_id
LEFT JOIN hr_employee_profile pr ON pr.employee_id = e.id LEFT JOIN hr_employee_pii pi ON pi.employee_id = e.id""")
    cur.execute("""CREATE OR REPLACE VIEW v_employee_pii AS
SELECT e.id AS employee_id, e.emp_no, e.name, e.dept_id, d.name AS dept_name, e.status, p.id_card, p.bank_account, p.phone,
       p.home_address, p.emergency_contact, p.emergency_relation, p.emergency_phone
FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id LEFT JOIN hr_employee_pii p ON p.employee_id = e.id""")
    for grantee in ("'copilot_ro'@'%'",):
        try:
            cur.execute(f"GRANT SELECT ON minierp.v_employee_roster TO {grantee}")
        except Exception as ex:
            print("授权跳过：", ex)
    db.commit()
    cur.execute("SELECT COUNT(*) n FROM hr_employee WHERE status <> 'LEFT'")
    active = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) n FROM hr_employee e JOIN org_position p ON p.id = e.position_id WHERE e.status <> 'LEFT' AND p.grade NOT LIKE 'O%%'")
    office = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) n FROM org_dept")
    print(f"完成：部门 {cur.fetchone()['n']} 个，在职 {active} 人，其中职能和研发人员 {office} 人")
    json.dump(RENAME, open(os.path.join(os.path.dirname(__file__), "..", "output", "rename_map.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
