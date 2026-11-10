"""开奖号抓取模块：解析 500 彩票网大乐透历史开奖页。

数据源
------
https://datachart.500.com/dlt/history/newinc/history.php?limit=N

返回 HTML 表格：数据行 class="t_tr1"，共 15 列，依次为
    [0] 期号（5 位，YYNNN）
    [1..5] 前区 5 个号码
    [6..7] 后区 2 个号码
    [8] 奖池奖金（元）
    [9] 一等奖注数
    [10] 一等奖基本投注单注奖金（元）
    [11] 二等奖注数
    [12] 二等奖基本投注单注奖金（元）
    [13] 总投注额（元）
    [14] 开奖日期（YYYY-MM-DD）
行序为从近到远。表头实测见 2026-11-10 探测记录。

设计要点
------
- 仅用标准库 urllib（VPS 零第三方依赖）；User-Agent 必带，否则被拦截；
- 超时 15 秒；编码 utf-8 优先、gbk 兜底；
- 扩展字段（奖池 / 奖金）为可选：列数不足或数值异常时置 None，
  不因新增字段导致整批解析失败（向后兼容）；
- 解析后做结构与取值校验，宁抛错不产出脏数据；
- 抓取失败时由调用方回退手动 record 子命令。
"""
import math
import re
import urllib.request
from datetime import date, timedelta
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


def _amount(text: str) -> Optional[int]:
    """解析「758,937,073」形式的金额；非正整数返回 None。

    0 视为无效（当期无人中奖时页面可能填 0 或占位符），避免把
    浮动奖奖金误算成 0 元。
    """
    cleaned = re.sub(r"[,\s]", "", text or "")
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if value > 0 else None


def parse_html(html: str) -> List[dict]:
    """解析开奖页，返回记录列表（从近到远）。

    每条：{"issue", "date", "front", "back",
           "pool", "first_count", "first_prize",
           "second_count", "second_prize", "sales"}
    后 6 项为可选，缺失或非法时为 None。

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

        pool = first_count = first_prize = None
        second_count = second_prize = sales = None
        if len(cells) >= 15:
            pool = _amount(cells[8])
            first_count = _amount(cells[9])
            first_prize = _amount(cells[10])
            second_count = _amount(cells[11])
            second_prize = _amount(cells[12])
            sales = _amount(cells[13])
        draws.append({
            "issue": issue, "date": draw_date,
            "front": front, "back": back,
            "pool": pool,
            "first_count": first_count, "first_prize": first_prize,
            "second_count": second_count, "second_prize": second_prize,
            "sales": sales,
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


def fetch_latest() -> dict:
    """抓取最近一期开奖数据（用于生成下一期预测时推算期号）。"""
    return fetch_draws(1)[0]


def estimate_limit(earliest: date, today: date) -> int:
    """按待对比记录的最早日期距今天数，动态估算需抓取的期数。

    大乐透每周 3 期（周一/三/六），故 期数 ≈ 天数 × 3/7；
    再追加固定缓冲，最后夹在 [FETCH_MIN_ISSUES, FETCH_MAX_ISSUES]。
    """
    days = max((today - earliest).days, 0)
    estimate = math.ceil(days * TICKETS_PER_WEEK / 7) + FETCH_BUFFER_ISSUES
    return min(FETCH_MAX_ISSUES, max(FETCH_MIN_ISSUES, estimate))


def next_draw_date(after: date) -> date:
    """返回严格晚于 after 的第一个开奖日（周一/三/六）。"""
    for delta in range(1, 8):
        candidate = after + timedelta(days=delta)
        if candidate.weekday() in DRAW_WEEKDAYS:
            return candidate
    raise ValueError("一周内未找到开奖日，请检查 DRAW_WEEKDAYS 配置")


def next_issue(latest_issue: str, target_date: date) -> str:
    """按「期号按当年开奖次数排序」推算下一期期号。

    同年：期序号 +1；跨年：新年份后两位 + "001"。
    非法期号或序号溢出返回空串，由调用方降级为不写期号。
    """
    if len(latest_issue) != 5 or not latest_issue.isdigit():
        return ""
    year_prefix = int(latest_issue[:2])
    target_prefix = target_date.year % 100
    if target_prefix != year_prefix:
        return f"{target_prefix:02d}001"
    seq = int(latest_issue[2:]) + 1
    if seq > 999:
        return ""
    return f"{year_prefix:02d}{seq:03d}"


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
    5. 期号严格递减；
    6. 扩展金额字段为非负整数或 None（防脏数据进入奖金计算）。

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
        for key in ("pool", "first_count", "first_prize",
                    "second_count", "second_prize", "sales"):
            value = d.get(key)
            if value is not None and (not isinstance(value, int) or value < 0):
                problems.append(f"第{idx}行 {key} 非法: {value!r}")
        prev_date, prev_issue = dt, issue
    return problems
