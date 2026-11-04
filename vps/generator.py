"""随机号码生成模块（大乐透）。

随机源
------
secrets 模块：调用操作系统级 CSPRNG——Linux 下读取 /dev/urandom
（混合设备中断熵，非确定性），Windows 下为 BCryptGenRandom。
无网络依赖、无第三方库依赖，是通用 VPS 上"尽可能接近真随机"的
务实实现。注意：secrets 不可被种子化，输出不可复现。

抽取算法（无放回抽取 / 拒绝采样）
------
严格按需求实现：逐个生成候选号码，与同区域内已生成的号码逐一
比较，命中重复则舍弃该候选并重新生成，直至凑满该区域所需数量。
前区与后区各自独立执行本算法，两区互不影响。

号码 1、12、35 与其他号码完全等概率，允许出现但不强制包含。
"""
import secrets
from datetime import date
from typing import List, Optional

from config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    DRAW_WEEKDAYS,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
)


def _randint(lo: int, hi: int) -> int:
    """返回 [lo, hi] 闭区间上的均匀随机整数（基于 secrets）。"""
    return lo + secrets.randbelow(hi - lo + 1)


def draw_zone(lo: int, hi: int, count: int) -> List[int]:
    """在单个区域内无放回抽取 count 个不重复号码，升序返回。

    实现：逐个生成候选 -> 与区域内已生成号码逐一比较 ->
    重复则舍弃并重新生成，直到凑满 count 个。
    """
    picked: List[int] = []
    while len(picked) < count:
        candidate = _randint(lo, hi)
        duplicated = False
        for existing in picked:
            if candidate == existing:
                duplicated = True
                break
        if not duplicated:
            picked.append(candidate)
    return sorted(picked)


def generate_ticket(draw_date: Optional[date] = None) -> dict:
    """生成一组完整号码：前区 5 个（1-35）+ 后区 2 个（1-12）。

    返回结构：
        {"date": "YYYY-MM-DD", "front": [..5..], "back": [..2..]}
    两区独立抽取、互不影响；号码升序排列仅用于展示。
    """
    return {
        "date": (draw_date or date.today()).isoformat(),
        "front": draw_zone(FRONT_MIN, FRONT_MAX, FRONT_COUNT),
        "back": draw_zone(BACK_MIN, BACK_MAX, BACK_COUNT),
    }


def is_draw_day(d: Optional[date] = None) -> bool:
    """判断给定日期（默认今天）是否为允许生成的开奖日：周一/三/六。"""
    return (d or date.today()).weekday() in DRAW_WEEKDAYS
