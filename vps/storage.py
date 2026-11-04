"""持久化存储模块：records.csv 的读写、开奖号回填与命中统计。

数据真相唯一存放在 records.csv（追加式、临时文件 + os.replace 原子写入）；
results.md / results.xlsx / results.html 均为由 CSV 渲染的产物，可随时重建。

CSV 字段
------
date,front,back,actual_front,actual_back,issue,source

号码以空格分隔的两个十进制字符串存储（如 "03 11 17 25 33"）；
actual_* 为空表示该期尚未录入开奖号码；issue/source 为抓取留痕，
旧版 CSV 缺少这两列时按空值读取（向后兼容）。
"""
import csv
import os
from typing import List, Optional

from config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
    RECORDS_CSV,
)

FIELDS = [
    "date", "front", "back", "actual_front", "actual_back",
    "issue", "source",
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


def _to_record(row: dict) -> dict:
    """CSV 行 -> 记录 dict（号码转 List[int]，空转 None）。"""
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
        "source": (row.get("source") or "").strip(),
    }


def load_records() -> List[dict]:
    """读取全部记录，按日期从近到远返回；文件不存在返回空表。"""
    if not RECORDS_CSV.exists():
        return []
    with RECORDS_CSV.open("r", encoding="utf-8", newline="") as f:
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
) -> Optional[dict]:
    """为指定日期回填开奖号码与来源留痕；日期不存在返回 None。

    仅在 actual_* 为空时由调用方决定是否覆盖（本函数本身不强制，
    由 fetch/record 的调用约束保证幂等）。
    """
    records = load_records()
    for r in records:
        if r["date"] == draw_date:
            r["actual_front"] = sorted(front)
            r["actual_back"] = sorted(back)
            r["issue"] = issue
            r["source"] = source
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
