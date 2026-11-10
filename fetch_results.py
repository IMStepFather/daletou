"""独立开奖结果抓取脚本（VPS 上可单独执行，不依赖 lotto.py）。

用法
----
    python3 fetch_results.py                    抓取并回填
    python3 fetch_results.py --dry-run          只校验与预览，不落库、不推送
    python3 fetch_results.py --no-notify        回填但不推送微信
    python3 fetch_results.py --force-correct    允许覆盖已回填记录（纠错用）

退出码
------
    0  成功
    1  失败（数据源不可用 / 批次校验拒绝 / 并发锁占用）
    2  无待办（无待抓取的预测日期）

设计约束
--------
- 纯标准库；所有路径基于 __file__（pathlib）；无交互，可被 cron 直接调用；
- 自加载同目录 .env，故独立执行也能完成微信推送；
- fcntl 非阻塞文件锁，Windows 无 fcntl 时降级为不加锁；
- 业务日期统一 Asia/Shanghai；
- 仅回填与预测日期严格相等的期次，其余期次只作内存索引，绝不落库；
- 批次校验失败整批拒绝，不写脏数据。
"""
import argparse
import contextlib
import os
import sys
import time
from datetime import date, datetime
from typing import List, Optional

try:
    import fcntl
except ImportError:
    fcntl = None  # Windows 开发环境无 fcntl，降级为不加锁

from config import (
    BACK_COUNT,
    BASE_DIR,
    CN_TZ,
    DRAW_WEEKDAYS,
    FETCH_LOCK,
    FETCH_MAX_ISSUES,
    FRONT_COUNT,
)
from fetcher import (
    estimate_limit,
    fetch_draws,
    find_draw_by_date,
    validate_batch,
)
from notify import build_compare_message, send_wechat
from prize_rules import evaluate
from report import render_all
from storage import load_records, set_actual

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_WORK = 2

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0


def _load_dotenv() -> None:
    """独立执行时加载同目录 .env；不覆盖已存在的环境变量。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _today_cn() -> date:
    """统一按上海时区取业务日期；缺 tzdata 时回退本地时间。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(CN_TZ)).date()
    except Exception:
        return date.today()


@contextlib.contextmanager
def _lock():
    """非阻塞文件锁，防止 cron 与手动执行并发写同一 CSV。"""
    if fcntl is None:
        yield True
        return
    with FETCH_LOCK.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _fetch_with_retry(limit: int) -> List[dict]:
    """抓取失败按指数退避重试；最终仍失败则抛出最后一次异常。"""
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fetch_draws(limit)
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(
                    f"[重试] 第 {attempt}/{RETRY_ATTEMPTS} 次抓取失败：{exc}；"
                    f"{delay:.0f}s 后重试"
                )
                time.sleep(delay)
    raise last_exc


def _fmt(nums) -> str:
    return " ".join(f"{n:02d}" for n in nums)


def _is_draw_date(dstr: str) -> bool:
    """日期字符串合法且确为周一/三/六。"""
    try:
        return datetime.strptime(dstr, "%Y-%m-%d").weekday() in DRAW_WEEKDAYS
    except ValueError:
        return False


def run(
    no_notify: bool = False,
    force_correct: bool = False,
    dry_run: bool = False,
) -> int:
    """执行一次完整的开奖结果回填，返回退出码（供 CLI 与其他入口复用）。"""
    _load_dotenv()
    today = _today_cn()
    today_s = today.isoformat()

    with _lock() as locked:
        if not locked:
            print("[跳过] 另一个 fetch 正在运行，本次跳过（幂等保护）。")
            return EXIT_FAIL
        return _body(today, today_s, no_notify, force_correct, dry_run)


def _body(
    today: date,
    today_s: str,
    no_notify: bool,
    force_correct: bool,
    dry_run: bool,
) -> int:
    records = load_records()

    invalid = [
        r for r in records
        if r["date"] <= today_s
        and r["actual_front"] is None
        and not _is_draw_date(r["date"])
    ]
    for r in invalid:
        print(f"[排除] {r['date']} 非开奖日（周一/三/六），不参与抓取。")

    candidates = [
        r for r in records
        if r["date"] <= today_s and _is_draw_date(r["date"])
    ]
    pending = candidates if force_correct else [
        r for r in candidates if r["actual_front"] is None
    ]
    if not pending:
        print("[提示] 无待对比记录，无需抓取。")
        return EXIT_NO_WORK

    earliest = min(
        datetime.strptime(r["date"], "%Y-%m-%d").date() for r in pending
    )
    limit = estimate_limit(earliest, today)
    print(
        f"[范围] 待对比 {len(pending)} 期，最早 {earliest}，"
        f"距今 {(today - earliest).days} 天 → 抓取最近 {limit} 期"
        f"（上限 {FETCH_MAX_ISSUES}）。"
    )

    try:
        draws = _fetch_with_retry(limit)
    except Exception as exc:
        print(f"[失败] 抓取开奖数据失败（已重试 {RETRY_ATTEMPTS} 次）：{exc}")
        print("[兜底] 已保留待抓状态，下次执行自动重试；或手动 record 补录：")
        for r in sorted(pending, key=lambda x: x["date"])[:5]:
            print(
                f"  python3 lotto.py record {r['date']} "
                f"<前区{FRONT_COUNT}个> <后区{BACK_COUNT}个>"
            )
        return EXIT_FAIL

    problems = validate_batch(draws)
    if problems:
        print(f"[拒绝] 批次校验未通过（{len(problems)} 项），整批放弃，CSV 未改动：")
        for p in problems[:10]:
            print(f"  - {p}")
        if len(problems) > 10:
            print(f"  ... 另有 {len(problems) - 10} 项")
        return EXIT_FAIL

    print(
        f"[抓取] 数据源 500.com，{len(draws)} 期索引校验通过"
        f"（最新：第{draws[0]['issue']}期 {draws[0]['date']}）；"
        f"仅回填与预测日期相等的期次。"
    )

    matched = []
    for r in sorted(pending, key=lambda x: x["date"], reverse=True):
        d = find_draw_by_date(draws, r["date"])
        if d is not None:
            matched.append((r, d))

    if not matched:
        print("[提示] 索引中无与预测日期相等的期次，保持待抓状态。")
    for r, d in matched:
        print(
            f"[匹配] {r['date']} 第{d['issue']}期 "
            f"前区 {_fmt(d['front'])} 后区 {_fmt(d['back'])}"
        )
    if dry_run:
        print(f"[预览] 共 {len(matched)} 期可回填；--dry-run 未落库、未刷新报表、未推送。")
        return EXIT_OK

    filled = []
    for r, d in matched:
        predicted_issue = r.get("expected_issue", "")
        if predicted_issue and predicted_issue != d["issue"]:
            print(
                f"[提示] {r['date']} 预测期号 第{predicted_issue}期 与实际 "
                f"第{d['issue']}期 不一致（休市/改期），以实际期号为准。"
            )
        h = (
            len(set(r["front"]) & set(d["front"])),
            len(set(r["back"]) & set(d["back"])),
        )
        result = evaluate(
            h[0], h[1], d.get("pool"),
            d.get("first_prize"), d.get("second_prize"),
        )
        rec = set_actual(
            r["date"], d["front"], d["back"],
            issue=d["issue"], source="500.com",
            pool=d.get("pool"),
            prize_level=result["level_name"],
            prize=result["prize"],
            cost=result["cost"],
        )
        prize_text = "待重算" if result["prize"] is None else f"¥{result['prize']}"
        profit = result["profit"]
        profit_text = (
            "--" if profit is None
            else (f"-¥{abs(profit)}" if profit < 0 else f"¥{profit}")
        )
        print(
            f"[回填] {r['date']} 第{d['issue']}期 → "
            f"命中 前区 {h[0]}/{FRONT_COUNT} · 后区 {h[1]}/{BACK_COUNT} | "
            f"{result['level_name']} 奖金 {prize_text} "
            f"成本 ¥{result['cost']} 收益 {profit_text}"
        )
        filled.append((rec, h))

    if filled:
        render_all(load_records())
        print("[报表] 已刷新 results.md / results.xlsx / results.html。")
        if no_notify:
            print("[通知] 已按 --no-notify 跳过。")
        else:
            for rec, h in filled:
                title, body = build_compare_message(
                    rec, h[0], h[1], FRONT_COUNT, BACK_COUNT
                )
                ok, msg = send_wechat(title, body)
                print(f"{'[通知]' if ok else '[通知失败]'} {msg}")

    still = [
        r["date"] for r in load_records()
        if r["actual_front"] is None
        and r["date"] <= today_s
        and _is_draw_date(r["date"])
    ]
    for dstr in sorted(still):
        print(f"[待重试] {dstr}：未找到同日期开奖结果，下次执行自动重试。")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fetch_results",
        description="独立抓取开奖结果（仅回填与预测日期相等的期次）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只校验与预览，不落库、不刷新报表、不推送",
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="回填但不推送微信"
    )
    parser.add_argument(
        "--force-correct", action="store_true",
        help="允许覆盖已回填记录（纠错用，建议配合 --no-notify）",
    )
    args = parser.parse_args()
    return run(
        no_notify=args.no_notify,
        force_correct=args.force_correct,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
