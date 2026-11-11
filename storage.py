"""持久化存储模块：records.csv 的读写、开奖号回填与命中/收益统计。

数据真相唯一存放在 records.csv（追加式、临时文件 + os.replace 原子写入）；
results.md / results.xlsx / results.html 均为由 CSV 渲染的产物，可随时重建。

CSV 字段
------
date,front,back,actual_front,actual_back,issue,expected_issue,
pool,prize_level,prize,cost,source

号码以空格分隔的十进制字符串存储（如 "03 11 17 25 33"）；
actual_* 为空表示该期尚未录入开奖号码。

字段说明
------
issue          开奖回填时写入的实际期号
expected_issue 生成预测时按网页最新期号推算的目标期号（预测留痕）
pool           当期奖池金额（元），用于判定固定奖档位
prize_level    奖级名称；未中奖为「未中奖」；未计算为空
prize          中奖金额（元）；None 表示浮动奖数据缺失、尚未计算
cost           单期成本（元），默认取 config.COST_PER_TICKET（3 元）
source         数据来源（500.com / manual）

旧版 CSV（4 列或 7 列）缺少的字段按空值 / 默认成本读取（向后兼容）。
"""
import csv
import os
from typing import List, Optional

from config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    COST_PER_TICKET,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
    RECORDS_CSV,
)

FIELDS = [
    "date", "front", "back", "actual_front", "actual_back",
    "issue", "expected_issue", "pool", "prize_level", "prize",
    "cost", "source",
]


def parse_zone(
    text: str,
    lo: int,
    hi: int,
    count: int,
    label: str,
) -> Optional[List[int]]:
    """解析空格分隔的号码串；空串返回 None；非法值抛 ValueError。

    校验：数量、不重复、取值范围 [lo, hi]。
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        nums = [int(tok) for tok in text.split()]
    except ValueError:
        raise ValueError(f"{label}含非整数: {text!r}")
    if len(nums) != count or len(set(nums)) != count:
        raise ValueError(f"{label}需 {count} 个不重复号码: {text!r}")
    for n in nums:
        if not (lo <= n <= hi):
            raise ValueError(f"{label}越界（应在 {lo}-{hi}）: {n}")
    return sorted(nums)


def _fmt(nums: Optional[List[int]]) -> str:
    return " ".join(f"{n:02d}" for n in nums) if nums else ""


def _int_or_none(row: dict, key: str) -> Optional[int]:
    """读取可选整数字段；空串 / 非法值返回 None。"""
    text = (row.get(key) or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _to_record(row: dict) -> dict:
    """CSV 行 -> 记录 dict（号码转 List[int]，空转 None）。"""
    cost = _int_or_none(row, "cost")
    return {
        "date": row["date"],
        "front": parse_zone(row["front"], FRONT_MIN, FRONT_MAX, FRONT_COUNT, "前区"),
        "back": parse_zone(row["back"], BACK_MIN, BACK_MAX, BACK_COUNT, "后区"),
        "actual_front": parse_zone(
            row.get("actual_front", ""), FRONT_MIN, FRONT_MAX, FRONT_COUNT, "开奖前区"
        ),
        "actual_back": parse_zone(
            row.get("actual_back", ""), BACK_MIN, BACK_MAX, BACK_COUNT, "开奖后区"
        ),
        "issue": (row.get("issue") or "").strip(),
        "expected_issue": (row.get("expected_issue") or "").strip(),
        "pool": _int_or_none(row, "pool"),
        "prize_level": (row.get("prize_level") or "").strip(),
        "prize": _int_or_none(row, "prize"),
        "cost": cost if cost is not None else COST_PER_TICKET,
        "source": (row.get("source") or "").strip(),
    }


def load_records() -> List[dict]:
    """读取全部记录，按日期从近到远返回；文件不存在返回空表。"""
    if not RECORDS_CSV.exists():
        return []
    # utf-8-sig 兼容 Excel 另存 / 编辑器写入的 BOM；无 BOM 时与 utf-8 等价。
    with RECORDS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    records = [_to_record(r) for r in rows]
    records.sort(key=lambda r: r["date"], reverse=True)
    return records


def save_records(records: List[dict]) -> None:
    """全量原子写回（临时文件 + os.replace），磁盘上按日期从远到近存储。"""
    ordered = sorted(records, key=lambda r: r["date"])
    tmp = RECORDS_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for r in ordered:
            writer.writerow(
                {
                    "date": r["date"],
                    "front": _fmt(r["front"]),
                    "back": _fmt(r["back"]),
                    "actual_front": _fmt(r["actual_front"]),
                    "actual_back": _fmt(r["actual_back"]),
                    "issue": r.get("issue", ""),
                    "expected_issue": r.get("expected_issue", ""),
                    "pool": "" if r.get("pool") is None else str(r["pool"]),
                    "prize_level": r.get("prize_level", ""),
                    "prize": "" if r.get("prize") is None else str(r["prize"]),
                    "cost": r.get("cost", COST_PER_TICKET),
                    "source": r.get("source", ""),
                }
            )
    os.replace(tmp, RECORDS_CSV)


def append_ticket(ticket: dict) -> bool:
    """追加一条生成记录；同日期已存在则返回 False（幂等，防调度重试重复）。"""
    records = load_records()
    if any(r["date"] == ticket["date"] for r in records):
        return False
    records.append(
        {
            "date": ticket["date"],
            "front": ticket["front"],
            "back": ticket["back"],
            "actual_front": None,
            "actual_back": None,
            "issue": "",
            "expected_issue": ticket.get("expected_issue", ""),
            "pool": None,
            "prize_level": "",
            "prize": None,
            "cost": ticket.get("cost", COST_PER_TICKET),
            "source": "",
        }
    )
    save_records(records)
    return True


def set_actual(
    draw_date: str,
    front: List[int],
    back: List[int],
    issue: str = "",
    source: str = "",
    pool: Optional[int] = None,
    prize_level: str = "",
    prize: Optional[int] = None,
    cost: Optional[int] = None,
) -> Optional[dict]:
    """为指定日期回填开奖号码、期号与奖金结果；日期不存在返回 None。

    仅在 actual_* 为空时由调用方决定是否覆盖（本函数本身不强制，
    由 fetch / record 的调用约束保证幂等）。expected_issue 不被改写，
    保留预测时推算的期号以便与实际期号对照。
    """
    records = load_records()
    for r in records:
        if r["date"] == draw_date:
            r["actual_front"] = sorted(front)
            r["actual_back"] = sorted(back)
            r["issue"] = issue
            r["source"] = source
            r["pool"] = pool
            r["prize_level"] = prize_level
            r["prize"] = prize
            if cost is not None:
                r["cost"] = cost
            save_records(records)
            return r
    return None


def hits(record: dict) -> Optional[tuple]:
    """单条记录命中数（前区, 后区）；尚未录入开奖号返回 None。"""
    if record["actual_front"] is None or record["actual_back"] is None:
        return None
    f = len(set(record["front"]) & set(record["actual_front"]))
    b = len(set(record["back"]) & set(record["actual_back"]))
    return f, b


def profit_of(record: dict) -> Optional[int]:
    """单期预估收益 = 中奖金额 - 成本；奖金尚未计算时返回 None。"""
    prize = record.get("prize")
    if prize is None:
        return None
    return prize - record.get("cost", COST_PER_TICKET)


def accuracy_summary(records: List[dict]) -> dict:
    """累计正确率：命中号码数 / 选取号码总数；未开奖的记录不计入分母。"""
    compared = [r for r in records if hits(r) is not None]
    front_hit = sum(hits(r)[0] for r in compared)
    back_hit = sum(hits(r)[1] for r in compared)
    return {
        "total": len(records),
        "compared": len(compared),
        "front_hit": front_hit,
        "front_picked": FRONT_COUNT * len(compared),
        "back_hit": back_hit,
        "back_picked": BACK_COUNT * len(compared),
    }


def profit_summary(records: List[dict]) -> dict:
    """累计投入 / 奖金 / 净收益；口径为「仅已开奖且奖金已计算的期次」。

    浮动奖数据缺失（prize 为 None）的期次单独计入 unsettled，
    不写入奖金合计，避免把缺失误算成 0 元。
    """
    compared = [r for r in records if hits(r) is not None]
    settled = [r for r in compared if r.get("prize") is not None]
    total_cost = sum(r.get("cost", COST_PER_TICKET) for r in settled)
    total_prize = sum(r["prize"] for r in settled)
    return {
        "periods": len(settled),
        "unsettled": len(compared) - len(settled),
        "total_cost": total_cost,
        "total_prize": total_prize,
        "net": total_prize - total_cost,
        "winning_periods": sum(1 for r in settled if r["prize"] > 0),
    }


def recalc_unsettled(records: List[dict]) -> dict:
    """离线补算「已有开奖号码但 prize 为空」的历史记录。

    旧版 CSV（11-04 升级 12 列之前）没有 prize 列，升级后这些期次
    显示为「待计算 / 待重算」，且不进入收益统计。本函数按已有开奖号码
    重新判定奖级：

    - 未中奖 / 固定奖（三至七等奖）：可离线确定；pool 未知按低档保守计；
    - 浮动奖（一、二等奖）：缺少页面公布的基本单注奖金，保持 None，
      由 ``fetch --force-correct`` 联网重算。

    仅在字段实际变化时原子写回，保证 report / fetch 反复调用幂等。
    返回 {"fixed": [...], "pending": [...], "changed": bool}。
    """
    from prize_rules import evaluate

    fixed: List[dict] = []
    pending: List[dict] = []
    dirty = False
    for r in records:
        if r["actual_front"] is None or r.get("prize") is not None:
            continue
        h = hits(r)
        if h is None:
            continue
        result = evaluate(h[0], h[1], r.get("pool"), None, None)
        if (
            r.get("prize_level", "") != result["level_name"]
            or r.get("prize") != result["prize"]
            or r.get("cost") != result["cost"]
        ):
            dirty = True
        r["prize_level"] = result["level_name"]
        r["prize"] = result["prize"]
        r["cost"] = result["cost"]
        item = {
            "date": r["date"],
            "hits": h,
            "level": result["level_name"],
            "prize": result["prize"],
        }
        (fixed if result["prize"] is not None else pending).append(item)
    if dirty:
        save_records(records)
    return {"fixed": fixed, "pending": pending, "changed": dirty}
