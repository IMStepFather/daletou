"""大乐透选号脚本 CLI 入口。

子命令
------
generate   生成「下一个尚未开奖的开奖日」预测号码；支持 --target 补生成
           指定开奖日、--offline 跳过期号抓取；同日期幂等，漏跑自动补齐
record     手动补录某期开奖号码，按固定奖低档计算收益（浮动奖需 fetch 重算）
fetch      抓取与预测日期对应的开奖结果（薄封装，转交独立脚本
           fetch_results.py 执行；两个入口共用同一实现）
report     从 records.csv 重新渲染 results.md / results.xlsx / results.html
           渲染前自动离线补算历史「待计算」期次
recalc     离线重算所有「已有开奖号码但奖金为空」的历史期次
notify     手动推送最新一期状态到微信（兼作 SENDKEY 连通性测试）
rules      校验官网开奖规则是否与本地固化规则一致
selftest   自测（生成结构/均匀性/星期 + 中奖规则 13 条件）

通知说明：generate / record / fetch 成功后自动推送微信（Server酱，
SENDKEY 取环境变量 SERVERCHAN_SENDKEY 或 config.py）；generate 可用
--no-notify 跳过，recalc 默认不推送。推送失败仅告警，不影响退出码。
"""
import argparse
import sys
from collections import Counter
from datetime import date, datetime
from typing import List

from config import (
    BACK_COUNT,
    BACK_MAX,
    BACK_MIN,
    CN_TZ,
    COST_PER_TICKET,
    DRAW_WEEKDAYS,
    FRONT_COUNT,
    FRONT_MAX,
    FRONT_MIN,
    PREDICT_WEEKDAYS,
)
from fetcher import (
    fetch_latest,
    next_draw_date,
    next_issue,
    previous_draw_date,
)
from generator import generate_ticket, is_draw_day
from notify import build_compare_message, build_generate_message, send_wechat
from prize_rules import (
    determine_level,
    evaluate,
    rule_snapshot,
    verify_remote_rules,
)
from report import render_all
from storage import (
    accuracy_summary,
    append_ticket,
    hits,
    load_records,
    parse_zone,
    profit_summary,
    recalc_unsettled,
    set_actual,
)

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def format_zone(nums: List[int]) -> str:
    return " ".join(f"{n:02d}" for n in nums)


DRAW_CUTOFF_HOUR = 21   # 开奖约 21:25；此后不再把当天视为「待开奖」


def _now_cn() -> datetime:
    """统一按上海时区取当前时间；Windows 缺 tzdata 时回退本地时间。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(CN_TZ))
    except (ImportError, KeyError, ValueError):
        return datetime.now()


def _today_cn() -> date:
    """统一按上海时区取业务日期；Windows 缺 tzdata 时回退本地时间。"""
    return _now_cn().date()


def upcoming_draw_date(today: date, hour: int) -> date:
    """返回「下一个尚未开奖」的开奖日，供 generate 自愈使用。

    若今天是开奖日且尚未到开奖时段，则今天即为待开奖期；否则取
    严格晚于今天的第一个开奖日。已过去的开奖日永不补生成，避免
    在开奖后伪造预测。
    """
    if today.weekday() in DRAW_WEEKDAYS and hour < DRAW_CUTOFF_HOUR:
        return today
    return next_draw_date(today)


def _notify(title: str, body: str) -> None:
    """推送微信并打印结果；失败仅告警不改变主流程退出码。"""
    ok, msg = send_wechat(title, body)
    print(f"{'[通知]' if ok else '[通知失败]'} {msg}")


def cmd_generate(args: argparse.Namespace) -> int:
    """生成「下一个尚未开奖」的预测；支持 --target 补生成指定开奖日。

    records.csv 的 date 记目标开奖日（周一/三/六），保证 fetch 的
    「严格按日期匹配开奖结果」规则不变。每次执行先检查目标日期是否
    已有记录：缺失则补齐（cron 漏跑自愈），已存在则幂等退出。
    """
    today = _today_cn()
    target_arg = getattr(args, "target", None)

    if target_arg:
        try:
            target = datetime.strptime(target_arg, "%Y-%m-%d").date()
        except ValueError:
            print(f"[失败] 日期格式错误: {target_arg!r}，应为 YYYY-MM-DD。")
            return 1
        if not is_draw_day(target):
            print(
                f"[拒绝] {target} 周{WEEKDAY_CN[target.weekday()]} "
                f"不是开奖日（周一/三/六）。"
            )
            return 1
        mode = "补生成"
    else:
        target = upcoming_draw_date(today, _now_cn().hour)
        mode = "自愈补生成" if today.weekday() not in PREDICT_WEEKDAYS else "生成"

    if any(r["date"] == target.isoformat() for r in load_records()):
        print(f"[跳过] {target} 已存在预测记录，同日期不重复生成（幂等）。")
        return 0

    expected_issue = ""
    if not getattr(args, "offline", False):
        try:
            latest = fetch_latest()
            prev = previous_draw_date(target)
            if latest["date"] == prev.isoformat():
                expected_issue = next_issue(latest["issue"], target)
            else:
                print(
                    f"[警告] 网页最新一期为 第{latest['issue']}期（{latest['date']}），"
                    f"非上一开奖日 {prev}；本期不写预测期号。"
                )
        except (OSError, ValueError, UnicodeError, IndexError, KeyError) as exc:
            print(
                f"[警告] 抓取最新期号失败（{exc}）；仍生成号码，"
                f"期号留空，开奖回填时以实际期号补齐。"
            )
    else:
        print("[离线] 已跳过期号抓取，expected_issue 留空。")

    ticket = generate_ticket(target)
    ticket["expected_issue"] = expected_issue
    if not append_ticket(ticket):
        print(f"[拒绝] {ticket['date']} 已生成过，同日期不重复生成（幂等）。")
        return 1
    render_all(load_records())
    issue_text = f"第{expected_issue}期" if expected_issue else "期号待定"
    print(
        f"[{mode}] 目标开奖日 {ticket['date']}（周{WEEKDAY_CN[target.weekday()]}）"
        f" {issue_text} "
        f"前区: {format_zone(ticket['front'])}  "
        f"后区: {format_zone(ticket['back'])}"
    )
    print("[存储] 已写入 records.csv；报表已刷新：results.md / results.xlsx / results.html。")
    if getattr(args, "no_notify", False):
        print("[通知] 已按 --no-notify 跳过。")
    else:
        title, body = build_generate_message(
            ticket, WEEKDAY_CN[target.weekday()]
        )
        _notify(title, body)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    try:
        datetime.strptime(args.draw_date, "%Y-%m-%d")
    except ValueError:
        print(f"[失败] 日期格式错误: {args.draw_date!r}，应为 YYYY-MM-DD。")
        return 1
    try:
        front = parse_zone(
            " ".join(str(n) for n in args.front),
            FRONT_MIN, FRONT_MAX, FRONT_COUNT, "开奖前区",
        )
        back = parse_zone(
            " ".join(str(n) for n in args.back),
            BACK_MIN, BACK_MAX, BACK_COUNT, "开奖后区",
        )
    except ValueError as exc:
        print(f"[失败] {exc}")
        return 1

    existing = next(
        (r for r in load_records() if r["date"] == args.draw_date), None
    )
    if existing is None:
        print(
            f"[失败] {args.draw_date} 无生成记录，请先用 generate 生成该期号码。"
        )
        return 1

    # 手动补录无奖池与浮动奖数据：固定奖按低档保守计，浮动奖标记待重算。
    front_hit = len(set(existing["front"]) & set(front))
    back_hit = len(set(existing["back"]) & set(back))
    result = evaluate(front_hit, back_hit, None, None, None)
    record = set_actual(
        args.draw_date, front, back, issue="", source="manual",
        pool=None, prize_level=result["level_name"],
        prize=result["prize"], cost=result["cost"],
    )
    if record is None:
        print(f"[失败] {args.draw_date} 回填失败，请检查 records.csv。")
        return 1

    render_all(load_records())
    h = hits(record)
    print(
        f"[录入] {args.draw_date} 开奖号码 前区: {format_zone(front)}  "
        f"后区: {format_zone(back)}"
    )
    print(
        f"[对比] 预测 前区: {format_zone(record['front'])}  "
        f"后区: {format_zone(record['back'])}"
    )
    print(f"[命中] 前区 {h[0]}/5 · 后区 {h[1]}/2；奖级 {result['level_name']}。")
    if result["prize"] is None:
        print("[提示] 浮动奖数据缺失，中奖金额待重算：./run.sh fetch --force-correct --no-notify")
    title, body = build_compare_message(
        record, h[0], h[1], FRONT_COUNT, BACK_COUNT
    )
    _notify(title, body)
    return 0


def _print_recalc(result: dict) -> None:
    """打印离线重算明细（report 与 recalc 共用）。"""
    for item in result["fixed"]:
        print(
            f"[重算] {item['date']} 前区 {item['hits'][0]}/5 · "
            f"后区 {item['hits'][1]}/2 → {item['level']} / ¥{item['prize']}"
        )
    for item in result["pending"]:
        print(
            f"[待联网] {item['date']} {item['level']}：浮动奖数据缺失，"
            f"请执行 ./run.sh fetch --force-correct --no-notify"
        )


def cmd_report(args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        print("[提示] records.csv 为空，尚未生成任何号码。")
        return 1
    result = recalc_unsettled(records)
    if result["changed"]:
        _print_recalc(result)
        records = load_records()
        print(f"[重算] 已补算 {len(result['fixed'])} 期历史记录并写回 records.csv。")
    render_all(records)
    print(f"[报表] 已渲染 {len(records)} 期记录：results.md / results.xlsx / results.html。")
    return 0


def cmd_recalc(args: argparse.Namespace) -> int:
    """离线重算历史期次的奖级 / 奖金 / 成本 / 收益（不联网、不推送）。"""
    records = load_records()
    result = recalc_unsettled(records)
    if not result["fixed"] and not result["pending"]:
        print("[重算] 无待补算记录，所有已开奖期次均已结算。")
        return 0
    _print_recalc(result)
    if result["changed"]:
        render_all(load_records())
        print("[重算] 已写回 records.csv 并刷新 results.md / results.xlsx / results.html。")
    p = profit_summary(load_records())
    net = f"-¥{abs(p['net'])}" if p["net"] < 0 else f"¥{p['net']}"
    print(
        f"[收益] 已结算 {p['periods']} 期：投入 ¥{p['total_cost']}，"
        f"奖金 ¥{p['total_prize']}，净收益 {net}。"
    )
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """薄封装：转交独立脚本 fetch_results.py 的 run()。

    抓取逻辑只保留一份实现，避免 CLI 与独立脚本两套代码漂移。
    """
    from fetch_results import run as run_fetch

    return run_fetch(
        no_notify=getattr(args, "no_notify", False),
        force_correct=getattr(args, "force_correct", False),
        dry_run=getattr(args, "dry_run", False),
    )


def cmd_rules(args: argparse.Namespace) -> int:
    """展示固化规则，并从官网校验关键条款是否变更（只读，不自动改写）。"""
    snap = rule_snapshot()
    print(
        f"[本地规则] 版本 {snap['version']}（自 {snap['effective_date']} 起）"
        f" | {snap['levels']} 奖级 / {snap['conditions']} 中奖条件"
        f" | 奖池档位 ¥{snap['pool_threshold']:,}"
        f" | 成本 ¥{snap['cost']}/期"
    )
    print(f"[本地规则] 核实日期 {snap['verified_at']} | 来源 {snap['source']}")
    try:
        remote = verify_remote_rules()
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"[失败] 无法读取官网规则页：{exc}")
        print("[提示] 本地固化规则不受影响，可稍后重试。")
        return 1
    print(f"[官网校验] 抓取 {remote['bytes']} 字节，{snap['source']}")
    for name, ok in remote["checks"].items():
        print(f"  [{'一致' if ok else '不一致'}] {name}")
    if not remote["ok"]:
        print("[警告] 官网与本地规则存在差异，请人工核对后更新 prize_rules.py。")
        return 1
    print("[通过] 官网规则与本地固化规则一致，无需更新。")
    return 0


def _chi_square(counter: Counter, lo: int, hi: int, total_picks: int):
    """对单个区域的号码频数做卡方均匀性检验。"""
    slots = hi - lo + 1
    expected = total_picks / slots
    stat = sum(
        (counter.get(n, 0) - expected) ** 2 / expected
        for n in range(lo, hi + 1)
    )
    return stat, slots - 1, expected


def cmd_selftest(args: argparse.Namespace) -> int:
    iterations = args.iterations
    front_counter: Counter = Counter()
    back_counter: Counter = Counter()

    # 1) 结构校验：数量、唯一性、取值范围（硬性，失败即退出）
    for _ in range(iterations):
        t = generate_ticket()
        front, back = t["front"], t["back"]
        if len(front) != FRONT_COUNT or len(set(front)) != FRONT_COUNT:
            print(f"[失败] 前区数量/唯一性不符: {front}")
            return 1
        if not all(FRONT_MIN <= n <= FRONT_MAX for n in front):
            print(f"[失败] 前区越界: {front}")
            return 1
        if len(back) != BACK_COUNT or len(set(back)) != BACK_COUNT:
            print(f"[失败] 后区数量/唯一性不符: {back}")
            return 1
        if not all(BACK_MIN <= n <= BACK_MAX for n in back):
            print(f"[失败] 后区越界: {back}")
            return 1
        front_counter.update(front)
        back_counter.update(back)

    # 2) 均匀性卡方检验（信息性指标：p=0.05 下约有 5% 概率天然越界，
    #    越界仅提示复查，不判失败）
    front_stat, front_df, front_exp = _chi_square(
        front_counter, FRONT_MIN, FRONT_MAX, iterations * FRONT_COUNT
    )
    back_stat, back_df, back_exp = _chi_square(
        back_counter, BACK_MIN, BACK_MAX, iterations * BACK_COUNT
    )
    # p=0.05 卡方临界值（查统计表）：df=34 -> 48.60，df=11 -> 19.68
    front_crit, back_crit = 48.60, 19.68

    # 3) 星期校验（开奖日 + 预测生成日）
    day_cases = [
        (date(2026, 10, 26), True),   # 周一
        (date(2026, 10, 27), False),  # 周二
        (date(2026, 10, 28), True),   # 周三
        (date(2026, 10, 29), False),  # 周四
        (date(2026, 10, 31), True),   # 周六
        (date(2026, 11, 1), False),   # 周日
    ]
    for d, expect in day_cases:
        actual = is_draw_day(d)
        if actual != expect:
            print(f"[失败] 星期校验: {d} 期望 {expect} 实际 {actual}")
            return 1
    predict_cases = [
        (date(2026, 11, 8), True),    # 周日（周六开奖次日）
        (date(2026, 11, 10), True),   # 周二（周一开奖次日）
        (date(2026, 11, 12), True),   # 周四（周三开奖次日）
        (date(2026, 11, 9), False),   # 周一（开奖当日）
    ]
    for d, expect in predict_cases:
        if (d.weekday() in PREDICT_WEEKDAYS) != expect:
            print(f"[失败] 预测日校验: {d} 期望 {expect}")
            return 1

    # 4) 中奖规则：13 个中奖条件 + 5 个未中奖组合 + 奖金口径
    prize_cases = {
        (5, 2): 1, (5, 1): 2, (5, 0): 3, (4, 2): 3, (4, 1): 4,
        (4, 0): 5, (3, 2): 5, (3, 1): 6, (2, 2): 6,
        (3, 0): 7, (2, 1): 7, (1, 2): 7, (0, 2): 7,
    }
    for combo, expect in prize_cases.items():
        if determine_level(*combo) != expect:
            print(f"[失败] 奖级判定: {combo} 期望 {expect}")
            return 1
    for combo in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0)):
        if determine_level(*combo) is not None:
            print(f"[失败] 未中奖组合误判: {combo}")
            return 1
    low = evaluate(3, 0, 758937073, 6603916, 210388)
    high = evaluate(3, 0, 800000000, 6603916, 210388)
    addon = evaluate(5, 2, 758937073, 1000000, 200000)
    if low["prize"] != 5 or high["prize"] != 7:
        print("[失败] 固定奖池两档计算错误")
        return 1
    if addon["prize"] != 1800000 or addon["cost"] != COST_PER_TICKET:
        print("[失败] 追加投注浮动奖计算错误")
        return 1
    if evaluate(5, 2, 758937073, None, None)["prize"] is not None:
        print("[失败] 浮动奖数据缺失时未正确降级")
        return 1

    # ---- 汇总 ----
    print(f"[自测] 迭代 {iterations} 次，结构校验（数量/唯一性/范围）全部通过。")
    print("[自测] 星期校验通过：开奖日周一/三/六，预测日周二/四/日。")
    print(
        f"[自测] 规则校验通过：{len(prize_cases)} 个中奖条件、"
        f"5 个未中奖组合、奖池两档、追加 80%。"
    )
    print(
        f"[自测] 前区均匀性: chi2={front_stat:.2f} (df={front_df}, "
        f"临界值={front_crit}) "
        f"{'通过' if front_stat <= front_crit else '越界（约5%概率属正常波动，可复跑确认）'}"
    )
    print(
        f"[自测] 后区均匀性: chi2={back_stat:.2f} (df={back_df}, "
        f"临界值={back_crit}) "
        f"{'通过' if back_stat <= back_crit else '越界（约5%概率属正常波动，可复跑确认）'}"
    )
    print(
        f"[自测] 前区各号出现次数 min={min(front_counter.values())} "
        f"max={max(front_counter.values())} 期望约{front_exp:.0f}"
    )
    print(
        f"[自测] 后区各号出现次数 min={min(back_counter.values())} "
        f"max={max(back_counter.values())} 期望约{back_exp:.0f}"
    )
    sample = generate_ticket()
    print(
        f"[自测] 样例: {sample['date']} "
        f"前区 {format_zone(sample['front'])} 后区 {format_zone(sample['back'])}"
    )
    print("[自测] 全部通过。")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    records = load_records()
    if not records:
        print("[提示] records.csv 为空，无内容可推送。")
        return 1
    latest = records[0]
    h = hits(latest)
    if h is not None:
        title, body = build_compare_message(
            latest, h[0], h[1], FRONT_COUNT, BACK_COUNT
        )
    else:
        title, body = build_generate_message(latest, _weekday_cn(latest["date"]))
    s = accuracy_summary(records)
    p = profit_summary(records)
    body += (
        f"\n\n---\n累计：共 {s['total']} 期，已对比 {s['compared']} 期；"
        f"前区 {s['front_hit']}/{s['front_picked']}，"
        f"后区 {s['back_hit']}/{s['back_picked']}。\n"
        f"已结算 {p['periods']} 期：投入 ¥{p['total_cost']}，"
        f"奖金 ¥{p['total_prize']}，"
        f"净收益 {'-¥' + str(abs(p['net'])) if p['net'] < 0 else '¥' + str(p['net'])}。"
    )
    ok, msg = send_wechat(title, body)
    print(f"{'[通知]' if ok else '[通知失败]'} {msg}")
    return 0 if ok else 1


def _weekday_cn(dstr: str) -> str:
    y, m, d = (int(x) for x in dstr.split("-"))
    return WEEKDAY_CN[datetime(y, m, d).weekday()]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lotto", description="大乐透随机选号与结果追踪脚本"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser(
        "generate", help="生成下一期号码（幂等；漏跑自动补齐）"
    )
    p_gen.add_argument(
        "--target", metavar="YYYY-MM-DD",
        help="补生成指定开奖日的预测（须为周一/三/六，同日期幂等）",
    )
    p_gen.add_argument(
        "--offline", action="store_true",
        help="跳过网页期号抓取，expected_issue 留空（无网环境）",
    )
    p_gen.add_argument(
        "--no-notify", action="store_true", help="生成后不推送微信",
    )
    p_gen.add_argument(
        "--force", action="store_true",
        help="保留兼容参数；自愈逻辑已自动处理非预测日",
    )
    p_gen.set_defaults(func=cmd_generate)

    p_rec = sub.add_parser("record", help="补录某期开奖号码并刷新报表")
    p_rec.add_argument("draw_date", metavar="日期", help="开奖日期，YYYY-MM-DD")
    p_rec.add_argument(
        "front", metavar="前区号码", type=int, nargs=FRONT_COUNT,
        help=f"{FRONT_COUNT} 个整数（{FRONT_MIN}-{FRONT_MAX}，不可重复）",
    )
    p_rec.add_argument(
        "back", metavar="后区号码", type=int, nargs=BACK_COUNT,
        help=f"{BACK_COUNT} 个整数（{BACK_MIN}-{BACK_MAX}，不可重复）",
    )
    p_rec.set_defaults(func=cmd_record)

    p_rep = sub.add_parser("report", help="从 records.csv 重新渲染报表")
    p_rep.set_defaults(func=cmd_report)

    p_calc = sub.add_parser(
        "recalc", help="离线重算历史期次的奖级 / 奖金 / 收益"
    )
    p_calc.add_argument(
        "--no-notify", action="store_true",
        help="保留参数以统一命令风格（recalc 默认不推送）",
    )
    p_calc.set_defaults(func=cmd_recalc)

    p_fetch = sub.add_parser(
        "fetch", help="抓取与预测日期对应的开奖结果（转交独立脚本）"
    )
    p_fetch.add_argument(
        "--dry-run", action="store_true", help="只校验与预览，不落库不推送"
    )
    p_fetch.add_argument(
        "--no-notify", action="store_true", help="回填但不推送微信"
    )
    p_fetch.add_argument(
        "--force-correct", action="store_true", help="允许覆盖已回填记录"
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_rules = sub.add_parser(
        "rules", help="校验官网开奖规则是否与本地固化规则一致"
    )
    p_rules.set_defaults(func=cmd_rules)

    p_notify = sub.add_parser(
        "notify", help="手动推送最新一期状态到微信（SENDKEY 测试）"
    )
    p_notify.set_defaults(func=cmd_notify)

    p_test = sub.add_parser("selftest", help="生成模块与中奖规则自测")
    p_test.add_argument("--iterations", type=int, default=5000)
    p_test.set_defaults(func=cmd_selftest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
