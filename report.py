"""报表渲染模块：records.csv -> results.md / results.xlsx / results.html。

三种格式结构一致：每期两行（预测行 + 开奖对比行），按日期从近到远
排列；文末附累计正确率统计（命中号码数 / 选取号码总数），并标注
理论期望值作为参照基准。

openpyxl 未安装时自动跳过 xlsx 渲染并给出提示，不影响 md / html 输出。
"""
from datetime import datetime
from html import escape
from typing import List

from config import RESULTS_HTML, RESULTS_MD, RESULTS_XLSX
from storage import accuracy_summary, hits

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]

# 理论期望：单号命中率 = 选取个数 / 号池容量
FRONT_EXPECT = "14.3%"
BACK_EXPECT = "16.7%"


def _fmt(nums) -> str:
    return " ".join(f"{n:02d}" for n in nums) if nums else "--"


def _weekday(dstr: str) -> str:
    y, m, d = (int(x) for x in dstr.split("-"))
    return WEEKDAY_CN[datetime(y, m, d).weekday()]


def _rate(hit: int, picked: int) -> str:
    if picked == 0:
        return "--"
    return f"{hit / picked * 100:.1f}%"


def _summary_lines(s: dict) -> list:
    return [
        "## 累计统计",
        "",
        f"共生成 {s['total']} 期，已对比 {s['compared']} 期。",
        "",
        "| 区域 | 命中/选取 | 正确率 | 理论期望 |",
        "|:---|:---|:---|:---|",
        f"| 前区 | {s['front_hit']}/{s['front_picked']} | "
        f"{_rate(s['front_hit'], s['front_picked'])} | {FRONT_EXPECT}（5/35） |",
        f"| 后区 | {s['back_hit']}/{s['back_picked']} | "
        f"{_rate(s['back_hit'], s['back_picked'])} | {BACK_EXPECT}（2/12） |",
        "",
        "> 理论期望为随机基线：开奖为独立随机事件，正确率长期收敛于期望值，",
        "> 偏离属正常统计波动，不构成预测能力。",
    ]


def render_markdown(records: List[dict]) -> None:
    """渲染 results.md：每期预测行 + 开奖对比行，日期倒序。"""
    s = accuracy_summary(records)
    lines = [
        "# 大乐透生成记录",
        "",
        f"> 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        f"　共 {s['total']} 期（已对比 {s['compared']} 期）",
        "",
        "| 日期 | 类型 | 前区 | 后区 | 命中 |",
        "|:---|:---|:---|:---|:---|",
    ]
    for r in records:
        h = hits(r)
        hit_text = "--"
        if h:
            hit_text = f"前区 {h[0]}/5 · 后区 {h[1]}/2"
        lines.append(
            f"| {r['date']} 周{_weekday(r['date'])} | 预测 "
            f"| {_fmt(r['front'])} | {_fmt(r['back'])} | {hit_text} |"
        )
        actual_front = _fmt(r["actual_front"]) if r["actual_front"] else "待开奖"
        actual_back = _fmt(r["actual_back"]) if r["actual_back"] else "待开奖"
        label = "开奖" + (f" 第{r['issue']}期" if r.get("issue") else "")
        lines.append(
            f"|  | {label} | {actual_front} | {actual_back} |  |"
        )
    lines.append("")
    lines.extend(_summary_lines(s))
    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_xlsx(records: List[dict]) -> bool:
    """渲染 results.xlsx；openpyxl 缺失时跳过并提示，返回是否成功。"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        print(
            "[提示] 未安装 openpyxl，跳过 results.xlsx"
            "（pip install openpyxl 后重跑 report 即可补生成）。"
        )
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = "生成记录"

    header_fill = PatternFill("solid", fgColor="D9D9D9")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    bold = Font(bold=True)

    for col, title in enumerate(
        ["日期", "类型", "前区", "后区", "命中"], start=1
    ):
        cell = ws.cell(row=1, column=col, value=title)
        cell.font = bold
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border

    row = 2
    for r in records:
        h = hits(r)
        hit_text = "--"
        if h:
            hit_text = f"前区 {h[0]}/5 · 后区 {h[1]}/2"
        actual_front = _fmt(r["actual_front"]) if r["actual_front"] else "待开奖"
        actual_back = _fmt(r["actual_back"]) if r["actual_back"] else "待开奖"
        for values in (
            [
                f"{r['date']} 周{_weekday(r['date'])}",
                "预测",
                _fmt(r["front"]),
                _fmt(r["back"]),
                hit_text,
            ],
            ["", "开奖" + (f" 第{r['issue']}期" if r.get("issue") else ""),
             actual_front, actual_back, ""],
        ):
            for col, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col, value=v)
                cell.alignment = center
                cell.border = border
            row += 1

    s = accuracy_summary(records)
    row += 1
    for label, hit, picked, expect, base in (
        ("前区", s["front_hit"], s["front_picked"], FRONT_EXPECT, "5/35"),
        ("后区", s["back_hit"], s["back_picked"], BACK_EXPECT, "2/12"),
    ):
        ws.cell(row=row, column=1, value="累计统计").font = bold
        ws.cell(row=row, column=2, value=label)
        ws.cell(row=row, column=3, value=f"{hit}/{picked}")
        ws.cell(row=row, column=4, value=_rate(hit, picked))
        ws.cell(row=row, column=5, value=f"期望 {expect}（{base}）")
        row += 1
    ws.cell(
        row=row, column=1,
        value=f"共生成 {s['total']} 期，已对比 {s['compared']} 期",
    )

    for col, width in zip("ABCDE", (16, 8, 18, 14, 20)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    wb.save(RESULTS_XLSX)
    return True


def render_html(records: List[dict]) -> None:
    """渲染 results.html 静态页：自包含（内联 CSS，无外部依赖），
    适配手机浏览器与微信内置浏览器，供 VPS 直接对外服务。
    """
    s = accuracy_summary(records)
    rows_html = []
    for r in records:
        h = hits(r)
        hit_text = "--" if h is None else f"前区 {h[0]}/5 · 后区 {h[1]}/2"
        if r["actual_front"]:
            af = _ball_badges(r["actual_front"], "b-front")
            ab = _ball_badges(r["actual_back"], "b-back")
        else:
            af = ab = '<span class="pending">待开奖</span>'
        rows_html.append(
            f'<tr class="row-predict"><td>{escape(r["date"])} 周{_weekday(r["date"])}</td>'
            f'<td>预测</td>'
            f'<td>{_ball_badges(r["front"], "b-front")}</td>'
            f'<td>{_ball_badges(r["back"], "b-back")}</td>'
            f'<td>{escape(hit_text)}</td></tr>'
            f'<tr class="row-actual"><td></td><td>{escape("开奖" + (" 第" + r["issue"] + "期" if r.get("issue") else ""))}</td>'
            f"<td>{af}</td><td>{ab}</td><td></td></tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大乐透生成记录</title>
<style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  max-width:720px;margin:24px auto;padding:0 16px;color:#222;background:#fff}}
h1{{font-size:20px}} .meta{{color:#777;font-size:13px;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #d0d0d0;padding:6px 8px;text-align:center;white-space:nowrap}}
th{{background:#ececec}}
.row-actual td{{color:#555;background:#fafafa}}
.b-front,.b-back{{display:inline-block;min-width:26px;height:26px;line-height:26px;
  border-radius:50%;color:#fff;margin:2px;font-size:13px;font-weight:600}}
.b-front{{background:#c2780a}} .b-back{{background:#185fa5}}
.pending{{color:#999}} .stats{{margin-top:24px;font-size:14px}}
.hit{{color:#b03030;font-weight:600}} .note{{color:#888;font-size:12px;margin-top:12px}}
</style>
</head>
<body>
<h1>大乐透生成记录</h1>
<p class="meta">更新时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
　共 {s["total"]} 期（已对比 {s["compared"]} 期）</p>
<table>
<tr><th>日期</th><th>类型</th><th>前区</th><th>后区</th><th>命中</th></tr>
{"".join(rows_html) if rows_html else '<tr><td colspan="5">暂无记录</td></tr>'}
</table>
<div class="stats">
<b>累计统计</b>：前区 {s["front_hit"]}/{s["front_picked"]}（{_rate(s["front_hit"], s["front_picked"])}，
期望 {FRONT_EXPECT}）；后区 {s["back_hit"]}/{s["back_picked"]}（{_rate(s["back_hit"], s["back_picked"])}，
期望 {BACK_EXPECT}）
</div>
<p class="note">理论期望为随机基线：开奖为独立随机事件，正确率长期收敛于期望值，
偏离属正常统计波动，不构成预测能力。</p>
</body>
</html>
"""
    RESULTS_HTML.write_text(html, encoding="utf-8")


def _ball_badges(nums, css: str) -> str:
    return "".join(f'<span class="{css}">{n:02d}</span>' for n in nums)


def render_all(records: List[dict]) -> None:
    """渲染全部报表产物。"""
    render_markdown(records)
    render_xlsx(records)
    render_html(records)
