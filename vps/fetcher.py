"""开奖号抓取模块：解析 500 彩票网大乐透历史开奖页。

数据源
------
https://datachart.500.com/dlt/history/newinc/history.php?limit=N

返回 HTML 表格：数据行 class="t_tr1"，单元格依次为期号、前区 5 个
（cfont2）、后区 2 个（cfont4）、奖池/销量等若干列，最后一列为开奖
日期（YYYY-MM-DD）。行序为从近到远。

设计要点
------
- 仅用标准库 urllib（VPS 零第三方依赖）；User-Agent 必带，否则被拦截；
- 超时 15 秒；编码 utf-8 优先、gbk 兜底；
- 解析后做结构与取值校验，宁抛错不产出脏数据；
- 抓取失败时由调用方回退手动 record 子命令。
"""
import math
import re
import urllib.request
from datetime import date
from typing import List, Optional

from config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    DRAW_WEEKDAYS,
    FETCH_BUFFER_ISSUES,
    FETCH_MAX_ISSUES,
    FETCH_MIN_ISSUES,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
    TICKETS_PER_WEEK,
)

URL = "https://datachart.500.com/dlt/history/newinc/history.php"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15

_ROW_RE = re.compile(r'<tr class="t_tr1">(.*?)</tr>', re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>|\s+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _http_get(url: str) -> str:
    """GET 并按 utf-8 / gbk 顺序解码；网络层异常直接向上抛出。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def parse_html(html: str) -> List[dict]:
    """解析开奖页，返回记录列表（从近到远）。

    每条：{"issue": "26123", "date": "2026-10-28",
           "front": [13,18,19,20,28], "back": [9,11]}
    注意：行内存在注释掉的占位单元格（<!--<td>2</td>-->），
    需先剥离注释，否则列错位后被校验拒绝。
    """
    html = _COMMENT_RE.sub("", html)
    draws: List[dict] = []
    for row in _ROW_RE.findall(html):
        cells = [_TAG_RE.sub("", c).strip() for c in _CELL_RE.findall(row)]
        need = 1 + FRONT_COUNT + BACK_COUNT + 1
        if len(cells) < need:
            continue
        issue, draw_date = cells[0], cells[-1]
        try:
            front = [int(n) for n in cells[1 : 1 + FRONT_COUNT]]
            back = [int(n) for n in cells[1 + FRONT_COUNT : 1 + FRONT_COUNT + BACK_COUNT]]
        except ValueError:
            continue
        if not _DATE_RE.fullmatch(draw_date) or not issue.isdigit():
            continue
        # 强校验：期号为 5 位数字，且日期可解析（防列错位导致的伪数据）
        if len(issue) != 5:
            continue
        try:
            date.fromisoformat(draw_date)
        except ValueError:
            continue
        if not all(FRONT_MIN <= n <= FRONT_MAX for n in front):
            continue
        if not all(BACK_MIN <= n <= BACK_MAX for n in back):
            continue
        if len(set(front)) != FRONT_COUNT or len(set(back)) != BACK_COUNT:
            continue
        draws.append({
            "issue": issue, "date": draw_date,
            "front": front, "back": back,
        })
    return draws


def fetch_draws(limit: int = 30) -> List[dict]:
    """抓取最近 limit 期开奖记录（从近到远）。

    失败（网络错误 / 解析为空）抛异常，由调用方决定兜底策略。
    """
    html = _http_get(f"{URL}?limit={limit}")
    draws = parse_html(html)
    if not draws:
        raise ValueError("页面解析结果为空，网站结构可能已变更，请人工核查")
    return draws


def estimate_limit(earliest: date, today: date) -> int:
    """按待对比记录的最早日期距今天数，动态估算需抓取的期数。

    大乐透每周 3 期（周一/三/六），故 期数 ≈ 天数 × 3/7；
    再追加固定缓冲，最后夹在 [FETCH_MIN_ISSUES, FETCH_MAX_ISSUES]。
    """
    days = max((today - earliest).days, 0)
    estimate = math.ceil(days * TICKETS_PER_WEEK / 7) + FETCH_BUFFER_ISSUES
    return min(FETCH_MAX_ISSUES, max(FETCH_MIN_ISSUES, estimate))


def find_draw_by_date(draws: List[dict], dstr: str) -> Optional[dict]:
    """按开奖日期查找对应期次记录（严格相等）。"""
    for d in draws:
        if d["date"] == dstr:
            return d
    return None


def validate_batch(draws: List[dict]) -> List[str]:
    """批次级一致性校验；返回问题描述列表（空列表表示通过）。

    校验项
    ------
    1. 批次非空；
    2. 期号为 5 位数字，且年份前缀与开奖日期年份一致
       （防解析列错位把期号与日期错配）；
    3. 日期为合法 ISO 日期，且确为开奖日（周一/三/六）；
    4. 日期唯一（同一天不得出现两期）且严格递减（从近到远）；
    5. 期号严格递减。

    注意：跨年时 26123 -> 27001 属正常跳变，故**不**要求相邻期号差 1，
    仅要求全局递减，避免跨年误报。
    """
    problems: List[str] = []
    if not draws:
        return ["抓取结果为空"]

    seen_dates = set()
    prev_date: Optional[str] = None
    prev_issue: Optional[str] = None
    for idx, d in enumerate(draws, start=1):
        issue, dt = d.get("issue", ""), d.get("date", "")
        if len(issue) != 5 or not issue.isdigit():
            problems.append(f"第{idx}行期号非法: {issue!r}")
            continue
        try:
            draw_date = date.fromisoformat(dt)
        except ValueError:
            problems.append(f"第{idx}行日期非法: {dt!r}")
            continue

        if issue[:2] != dt[2:4]:
            problems.append(
                f"第{idx}行期号 {issue} 与日期 {dt} 年份前缀不符（疑似列错位）"
            )
        if draw_date.weekday() not in DRAW_WEEKDAYS:
            problems.append(f"第{idx}行 {dt} 非开奖日（周一/三/六）")
        if dt in seen_dates:
            problems.append(f"第{idx}行日期 {dt} 重复")
        seen_dates.add(dt)
        if prev_date is not None and dt >= prev_date:
            problems.append(f"第{idx}行日期顺序异常: {prev_date} -> {dt}")
        if prev_issue is not None and int(issue) >= int(prev_issue):
            problems.append(f"第{idx}行期号顺序异常: {prev_issue} -> {issue}")
        prev_date, prev_issue = dt, issue
    return problems
