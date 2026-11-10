"""超级大乐透中奖规则引擎（2026 新规）。

规则版本
--------
自第 26014 期（2026-02-02 开奖）起执行：9 个奖级合并为 7 个，13 个
中奖条件不变；一、二等奖为浮动奖，三至七等奖为固定奖，且固定奖
金额按奖池是否达到 8 亿元分两档。

来源：上海体彩网 2026-01-31 转载中国体彩网《中国体育彩票超级大乐透
游戏规则》全文（RULE_SOURCE_URL），并与中国体彩网大乐透首页
「奖金对照表」交叉核对；核实日期 2026-11-10。

关键条款
--------
- 第十五条：七个奖级；固定奖金额随奖池 8 亿元分档。
- 第十六条：追加投注（+1 元）仅参与浮动奖，追加单注奖金为
  当期基本投注对应单注奖金的 80%。
- 第二十五条：13 个中奖条件。
- 第二十六条：每注只兑付最高奖级，不能兼中兼得。

注意：m.lottery.gov.cn 仍保留 9 奖级旧规则页面，切勿混用。
"""
import re
import urllib.request
from typing import Dict, Optional, Tuple

from config import COST_PER_TICKET, RULE_SOURCE_URL

RULE_VERSION = "26014+"
RULE_EFFECTIVE_DATE = "2026-02-02"
RULE_VERIFIED_AT = "2026-11-10"
POOL_THRESHOLD = 800_000_000
BASIC_COST = 2
ADDON_COST = 1
ADDON_RATIO = 0.8

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15

# (奖级序号, 名称, 该奖级对应的 (前区命中数, 后区命中数) 条件)
# 顺序即优先级：从最高奖级向下匹配，命中即返回（第二十六条）。
LEVEL_RULES: Tuple[Tuple[int, str, Tuple[Tuple[int, int], ...]], ...] = (
    (1, "一等奖", ((5, 2),)),
    (2, "二等奖", ((5, 1),)),
    (3, "三等奖", ((5, 0), (4, 2))),
    (4, "四等奖", ((4, 1),)),
    (5, "五等奖", ((4, 0), (3, 2))),
    (6, "六等奖", ((3, 1), (2, 2))),
    (7, "七等奖", ((3, 0), (2, 1), (1, 2), (0, 2))),
)

# 固定奖：(奖池 < 8 亿, 奖池 >= 8 亿)，单位元
FIXED_PRIZE: Dict[int, Tuple[int, int]] = {
    3: (5000, 6666),
    4: (300, 380),
    5: (150, 200),
    6: (15, 18),
    7: (5, 7),
}


def determine_level(front_hit: int, back_hit: int) -> Optional[int]:
    """按 13 个中奖条件判定奖级；未中奖返回 None。

    front_hit / back_hit 分别为前区、后区命中个数。
    """
    for level, _name, conditions in LEVEL_RULES:
        if (front_hit, back_hit) in conditions:
            return level
    return None


def level_name(level: Optional[int]) -> str:
    """奖级序号转名称；None 表示未中奖。"""
    if level is None:
        return "未中奖"
    for num, name, _conditions in LEVEL_RULES:
        if num == level:
            return name
    return "未知"


def evaluate(
    front_hit: int,
    back_hit: int,
    pool: Optional[int],
    first_prize: Optional[int],
    second_prize: Optional[int],
    addon: bool = True,
) -> dict:
    """计算单式 1 注的中奖金额、成本与预估收益。

    参数
    ----
    pool         : 当期奖池金额（元）；None 表示未知，按 < 8 亿低档计。
    first_prize  : 页面公布的一等奖基本投注单注奖金（元）。
    second_prize : 页面公布的二等奖基本投注单注奖金（元）。
    addon        : 是否追加投注（默认 True，对应 3 元成本）。

    返回
    ----
    dict：level / level_name / prize / cost / profit / high_pool / note。
    prize 与 profit 为 None 表示浮动奖数据缺失，需 fetch 重算，不写 0。
    """
    level = determine_level(front_hit, back_hit)
    cost = COST_PER_TICKET if addon else BASIC_COST
    high_pool = pool is not None and pool >= POOL_THRESHOLD
    result = {
        "level": level,
        "level_name": level_name(level),
        "prize": 0,
        "cost": cost,
        "profit": -cost,
        "high_pool": high_pool,
        "note": "",
    }
    if level is None:
        return result

    if level in (1, 2):
        base = first_prize if level == 1 else second_prize
        if base is None:
            result["prize"] = None
            result["profit"] = None
            result["note"] = "浮动奖数据缺失，请用 fetch --force-correct 重算"
            return result
        # 第十六条：追加部分按基本单注奖金的 80% 计，按元取整（向下）。
        prize = base + int(base * ADDON_RATIO) if addon else base
        result["prize"] = prize
        result["profit"] = prize - cost
        result["note"] = "浮动奖取页面基本单注奖金" + ("，含追加 80%" if addon else "")
        return result

    # 固定奖级：追加投注不参与分配，奖金只取基本投注金额。
    low, high = FIXED_PRIZE[level]
    prize = high if high_pool else low
    result["prize"] = prize
    result["profit"] = prize - cost
    result["note"] = "奖池 ≥ 8 亿档" if high_pool else "奖池 < 8 亿档"
    if pool is None:
        result["note"] += "（奖池未知，按低档保守计）"
    return result


def rule_snapshot() -> dict:
    """返回当前固化的规则摘要（供 rules 子命令展示）。"""
    return {
        "version": RULE_VERSION,
        "effective_date": RULE_EFFECTIVE_DATE,
        "verified_at": RULE_VERIFIED_AT,
        "source": RULE_SOURCE_URL,
        "levels": len(LEVEL_RULES),
        "conditions": sum(len(c) for _n, _name, c in LEVEL_RULES),
        "pool_threshold": POOL_THRESHOLD,
        "cost": COST_PER_TICKET,
        "fixed_prize": dict(FIXED_PRIZE),
    }


def verify_remote_rules(url: str = RULE_SOURCE_URL) -> dict:
    """从官网抓取规则全文，校验关键条款是否与固化规则一致。

    只做只读校验，不自动改写本地规则（避免解析误差污染计算）。
    网络或页面结构异常时抛出原始异常，由调用方决定提示方式。
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    plain = re.sub(r"<[^>]+>", "", text)
    plain = re.sub(r"[\s\u3000]+", "", plain)
    checks = {
        "共设七个奖级": "共设七个奖级" in plain,
        "三等奖 5000/6666": "5000元" in plain and "6666元" in plain,
        "四等奖 300/380": "300元" in plain and "380元" in plain,
        "五等奖 150/200": "150元" in plain and "200元" in plain,
        "六等奖 15/18": "15元" in plain and "18元" in plain,
        "七等奖 5/7": "5元" in plain and "7元" in plain,
        "追加 80%": "80%" in plain,
        "周一三六开奖": "每周一、三、六开奖" in plain,
    }
    return {
        "url": url,
        "checks": checks,
        "ok": all(checks.values()),
        "bytes": len(raw),
    }
