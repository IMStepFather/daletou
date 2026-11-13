"""报表渲染模块：records.csv -> results.md / results.xlsx / results.html。

三种格式结构一致：每期两行（预测行 + 开奖对比行），按日期从近到远排列；
「奖级」「成本 / 奖金/收益」两列仅填在预测行，开奖行留空；累计正确率与
收益统计置于表格之前；表格仅展示最近 DISPLAY_MAX_PERIODS 期，统计仍按
全部历史累计（records.csv 保留全量数据真相）。

results.html 为自包含单文件：CSS 与站点 logo（money-bag.svg）均内联，
不引用任何外部资源，便于 Cloudflare Pages 只上传一个 HTML 文件。

openpyxl 未安装时自动跳过 xlsx 渲染并给出提示，不影响 md / html 输出。
"""
from datetime import datetime
from html import escape
from typing import List
from urllib.parse import quote

from config import (
    COST_PER_TICKET,
    DISPLAY_MAX_PERIODS,
    LOGO_COLOR,
    LOGO_SVG,
    RESULTS_HTML,
    RESULTS_MD,
    RESULTS_XLSX,
)
from storage import accuracy_summary, hits, profit_of, profit_summary

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]

# 理论期望：单号命中率 = 选取个数 / 号池容量
FRONT_EXPECT = "14.3%"
BACK_EXPECT = "16.7%"

HEADERS = [
    "日期", "期号", "类型", "前区", "后区", "命中",
    "奖级", "成本 / 奖金/收益",
]

# 奖级列允许的取值：未中奖，或一至七等奖；其余（待计算/待重算/空）留白。
_VALID_LEVELS = {"未中奖"} | {f"{n}等奖" for n in "一二三四五六七"}


def _fmt(nums) -> str:
    return " ".join(f"{n:02d}" for n in nums) if nums else "--"


def _weekday(dstr: str) -> str:
    y, m, d = (int(x) for x in dstr.split("-"))
    return WEEKDAY_CN[datetime(y, m, d).weekday()]


def _rate(hit: int, picked: int) -> str:
    if picked == 0:
        return "--"
    return f"{hit / picked * 100:.1f}%"


def _issue_text(record: dict) -> str:
    """期号统一为「第XXX期」：已开奖取实际期号，未开奖回退推算期号。

    不再附加「（预计）」等后缀；预测行与开奖行共用同一格式。
    """
    issue = (record.get("issue") or "").strip() or (
        record.get("expected_issue") or ""
    ).strip()
    return f"第{issue}期" if issue else "--"


def _money(value: int) -> str:
    """统计区金额展示：负数写成 -¥3，避免 ¥-3 这种写法。"""
    return f"-¥{abs(value)}" if value < 0 else f"¥{value}"


def _level_text(record: dict) -> str:
    """奖级列：仅「未中奖」或「X等奖」，不附加任何其他说明。

    未开奖、或浮动奖/旧数据尚未判定时返回空串（不用「待计算」等文案）。
    """
    if not record.get("actual_front"):
        return ""
    level = (record.get("prize_level") or "").strip()
    return level if level in _VALID_LEVELS else ""


def _cost_prize_profit_text(record: dict) -> str:
    """成本 / 奖金/收益列：只含数字，按列标题顺序用 / 分隔。

    已结算 -> 「成本/奖金/收益」，例如 3/0/-3；
    浮动奖未结算（prize 为 None）-> 仅成本一个数字，例如 3；
    未开奖 -> 空串。
    """
    if not record.get("actual_front"):
        return ""
    parts = [str(record.get("cost", COST_PER_TICKET))]
    prize = record.get("prize")
    if prize is not None:
        parts.append(str(prize))
        parts.append(str(profit_of(record)))
    return "/".join(parts)


def _display_records(records: List[dict]) -> List[dict]:
    """表格数据：按日期从近到远取最近 N 期。

    统计仍基于传入的全量 records（累计语义不变）；本函数只裁剪表格行，
    不改动 records.csv，也不为被裁掉的历史数据另建持久化产物。
    """
    ordered = sorted(records, key=lambda r: r["date"], reverse=True)
    return ordered[:DISPLAY_MAX_PERIODS]


def _rows(records: List[dict]) -> list:
    """返回 [(预测行, 开奖行), ...]，每行 8 列，供三种格式复用。

    最后两列（奖级、成本 / 奖金/收益）仅填在预测行；开奖行留空，
    只承担开奖号码对比。
    """
    out = []
    for r in records:
        h = hits(r)
        hit_text = "--" if h is None else f"前区 {h[0]}/5 · 后区 {h[1]}/2"
        af = _fmt(r["actual_front"]) if r["actual_front"] else "待开奖"
        ab = _fmt(r["actual_back"]) if r["actual_back"] else "待开奖"
        date_text = f"{r['date']} 周{_weekday(r['date'])}"
        out.append((
            [
                date_text, _issue_text(r),
                "预测", _fmt(r["front"]), _fmt(r["back"]), hit_text,
                _level_text(r), _cost_prize_profit_text(r),
            ],
            [
                date_text, _issue_text(r),
                "开奖", af, ab, "", "", "",
            ],
        ))
    return out


def _summary_lines(s: dict, p: dict) -> list:
    lines = [
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
        "## 收益统计（仅已开奖且奖金已计算的期次）",
        "",
        f"已结算 {p['periods']} 期，其中中奖 {p['winning_periods']} 期；"
        f"累计投入 ¥{p['total_cost']}，累计奖金 ¥{p['total_prize']}，"
        f"净收益 {_money(p['net'])}。",
    ]
    if p["unsettled"]:
        lines.append(f"> 另有 {p['unsettled']} 期浮动奖数据缺失，未计入奖金合计。")
    lines.extend([
        "",
        f"> 表格仅展示最近 {DISPLAY_MAX_PERIODS} 期；以上统计按全部 "
        f"{s['total']} 期历史累计。",
        "> 口径：单式 1 注按追加投注（3 元/期，基本 2 元 + 追加 1 元）；"
        "浮动奖取 500.com 公布的基本单注奖金 ×1.8，固定奖按奖池 8 亿元档位。",
        "> 开奖为独立随机事件，正确率长期收敛于理论期望；彩票返奖率 51%，"
        "净收益长期期望为负，本表仅作记录。",
    ])
    return lines


def render_markdown(records: List[dict]) -> None:
    """渲染 results.md：统计置顶，表格按日期倒序、每期两行。"""
    s = accuracy_summary(records)
    p = profit_summary(records)
    shown = _display_records(records)
    lines = [
        "# 大乐透生成记录",
        "",
        f"> 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        f"　共 {s['total']} 期（已对比 {s['compared']} 期）",
        "",
    ]
    lines.extend(_summary_lines(s, p))
    lines.extend([
        "",
        f"## 最近 {len(shown)} 期记录（表格上限 {DISPLAY_MAX_PERIODS} 期）",
        "",
        "| " + " | ".join(HEADERS) + " |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|",
    ])
    for pred, actual in _rows(shown):
        lines.append("| " + " | ".join(pred) + " |")
        lines.append("| " + " | ".join(actual) + " |")
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

    s = accuracy_summary(records)
    p = profit_summary(records)
    shown = _display_records(records)

    # 顶部统计区：位于表头之前，打开文件即优先可见。
    ws.cell(row=1, column=1, value="累计正确率").font = bold
    for i, (label, hit, picked, expect, base) in enumerate((
        ("前区", s["front_hit"], s["front_picked"], FRONT_EXPECT, "5/35"),
        ("后区", s["back_hit"], s["back_picked"], BACK_EXPECT, "2/12"),
    )):
        ws.cell(row=1 + i, column=2, value=label)
        ws.cell(row=1 + i, column=3, value=f"{hit}/{picked}")
        ws.cell(row=1 + i, column=4, value=_rate(hit, picked))
        ws.cell(row=1 + i, column=5, value=f"期望 {expect}（{base}）")
    ws.cell(row=3, column=1, value="收益统计").font = bold
    ws.cell(
        row=3, column=2,
        value=(
            f"已结算 {p['periods']} 期 | 投入 ¥{p['total_cost']} | "
            f"奖金 ¥{p['total_prize']} | 净收益 {_money(p['net'])}"
        ),
    )
    ws.cell(
        row=4, column=1,
        value=(
            f"共 {s['total']} 期（已对比 {s['compared']} 期）；"
            f"表格仅展示最近 {len(shown)} 期，统计按全部历史累计"
        ),
    )

    header_row = 6
    for col, title in enumerate(HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col, value=title)
        cell.font = bold
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border

    # 预测行（白底深字）与开奖行（浅蓝灰底柔字）保持与 HTML 相同的对比语义
    predict_fill = PatternFill("solid", fgColor="FFFFFF")
    actual_fill = PatternFill("solid", fgColor="EEF3F8")
    row = header_row + 1
    for pred, actual in _rows(shown):
        for idx, values in enumerate((pred, actual)):
            fill = predict_fill if idx == 0 else actual_fill
            color = "1F2733" if idx == 0 else "55606F"
            for col, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col, value=v)
                cell.alignment = center
                cell.border = border
                cell.fill = fill
                cell.font = Font(color=color, bold=(col == 3))
            row += 1

    for col, width in zip("ABCDEFGH", (16, 18, 8, 18, 14, 22, 18, 18)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A7"

    wb.save(RESULTS_XLSX)
    return True


def render_html(records: List[dict]) -> None:
    """渲染 results.html 静态页：自包含（内联 CSS / logo，无外部依赖），
    适配手机浏览器与微信内置浏览器，供 VPS 直接对外服务。

    累计正确率与收益统计置于表格之前；表格仅含最近
    DISPLAY_MAX_PERIODS 期，控制行数与 HTML 体积。
    """
    s = accuracy_summary(records)
    p = profit_summary(records)
    shown = _display_records(records)
    logo_html, favicon_link = _logo_assets()
    rows_html = []
    for pred, actual in _rows(shown):
        # 号码列渲染为圆点徽章，类型列渲染为语义徽章，其余列纯文本。
        # 三种格式仍读同一份 _rows() 数据，仅 HTML 追加 class / 徽章。
        rows_html.append(
            "<tr class=\"row-predict\">"
            + "".join(f"<td>{escape(str(v))}</td>" for v in pred[:2])
            + f'<td><span class="tag tag-predict">{escape(str(pred[2]))}</span></td>'
            + f"<td>{_ball_badges_from_text(pred[3], 'b-front')}</td>"
            + f"<td>{_ball_badges_from_text(pred[4], 'b-back')}</td>"
            + "".join(f"<td>{escape(str(v))}</td>" for v in pred[5:])
            + "</tr>"
        )
        rows_html.append(
            "<tr class=\"row-actual\">"
            + "".join(f"<td>{escape(str(v))}</td>" for v in actual[:2])
            + f'<td><span class="tag tag-actual">{escape(str(actual[2]))}</span></td>'
            + f"<td>{_ball_badges_from_text(actual[3], 'b-front')}</td>"
            + f"<td>{_ball_badges_from_text(actual[4], 'b-back')}</td>"
            + "".join(f"<td>{escape(str(v))}</td>" for v in actual[5:])
            + "</tr>"
        )
    header_html = "".join(f"<th>{escape(h)}</th>" for h in HEADERS)
    unsettled_note = (
        f"<br>另有 {p['unsettled']} 期浮动奖数据缺失，未计入奖金合计。"
        if p["unsettled"] else ""
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大乐透生成记录</title>
{favicon_link}
<style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  max-width:960px;margin:24px auto;padding:0 16px;color:#222;background:#fff}}
h1{{font-size:20px;margin:0}} h2{{font-size:15px;margin:0 0 6px}}
.brand{{display:flex;align-items:center;gap:12px;margin:0 0 14px}}
.brand .logo{{display:flex;align-items:center;justify-content:center;
  flex:0 0 44px;width:44px;height:44px;border-radius:12px;
  background:#fff7ea;color:{LOGO_COLOR}}}
.brand .logo svg{{display:block;width:28px;height:28px}}
.meta{{color:#777;font-size:13px;margin:2px 0 0}}
.stats{{background:#f5f7fa;border:1px solid #dde4ec;border-radius:8px;
  padding:12px 16px;margin:0 0 14px;font-size:14px;line-height:1.7}}
.stats .block+.block{{margin-top:10px}}
.tablewrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px;min-width:760px}}
th,td{{border:1px solid #d0d0d0;padding:6px 8px;text-align:center;white-space:nowrap}}
th{{background:#e2e8f0}}
.row-predict td{{background:#ffffff;color:#1f2733}}
.row-actual td{{background:#eef3f8;color:#55606f}}
.row-predict td:first-child{{box-shadow:inset 3px 0 0 #185fa5}}
.row-actual td:first-child{{box-shadow:inset 3px 0 0 #98a5b6}}
.tag{{display:inline-block;min-width:36px;padding:1px 8px;border-radius:10px;
  font-size:12px;font-weight:600;line-height:1.6}}
.tag-predict{{background:#e6f1fb;color:#185fa5}}
.tag-actual{{background:#e4e9f0;color:#55606f}}
.b-front,.b-back{{display:inline-block;box-sizing:border-box;vertical-align:middle;
  min-width:26px;height:26px;line-height:24px;border-radius:50%;color:#fff;
  margin:2px;font-size:13px;font-weight:600;border:1px solid transparent}}
.b-front{{background:#c2780a}} .b-back{{background:#185fa5}}
.row-actual .b-front{{background:#fff7ea;color:#a8630a;border-color:#c2780a}}
.row-actual .b-back{{background:#eaf3fc;color:#185fa5;border-color:#185fa5}}
.pending{{color:#999}} .scope{{color:#666;font-size:13px;margin:0 0 8px}}
.note{{color:#888;font-size:12px;margin-top:12px}}
</style>
</head>
<body>
<header class="brand">
{logo_html}
<div>
<h1>大乐透生成记录</h1>
<p class="meta">更新时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
　共 {s["total"]} 期（已对比 {s["compared"]} 期）</p>
</div>
</header>
<div class="stats">
<div class="block"><h2>累计正确率</h2>
前区 {s["front_hit"]}/{s["front_picked"]}（{_rate(s["front_hit"], s["front_picked"])}，期望 {FRONT_EXPECT}）；
后区 {s["back_hit"]}/{s["back_picked"]}（{_rate(s["back_hit"], s["back_picked"])}，期望 {BACK_EXPECT}）</div>
<div class="block"><h2>收益统计（仅已开奖且奖金已计算期次）</h2>
已结算 {p["periods"]} 期，其中中奖 {p["winning_periods"]} 期；
累计投入 ¥{p["total_cost"]}，累计奖金 ¥{p["total_prize"]}，
净收益 {_money(p["net"])}。{unsettled_note}</div>
</div>
<p class="scope">表格按时间线仅展示最近 {len(shown)} 期（上限 {DISPLAY_MAX_PERIODS} 期）；
以上统计按全部 {s["total"]} 期历史累计。</p>
<div class="tablewrap"><table>
<tr>{header_html}</tr>
{"".join(rows_html) if rows_html else '<tr><td colspan="8">暂无记录</td></tr>'}
</table></div>
<p class="note">口径：单式 1 注按追加投注（3 元/期）；浮动奖取 500.com 公布的基本单注奖金 ×1.8，
固定奖按奖池 8 亿元档位。开奖为独立随机事件，正确率长期收敛于理论期望；
彩票返奖率 51%，净收益长期期望为负，本表仅作记录。</p>
</body>
</html>
"""
    RESULTS_HTML.write_text(html, encoding="utf-8")


def _logo_assets():
    """生成内联 logo 与 favicon，返回 (logo_html, favicon_link)。

    Cloudflare Pages 只上传 results.html 单文件，外链 SVG 会 404，因此：
    - 可见 logo：直接嵌入 SVG 标签，其 currentColor 继承 CSS 的 color；
    - favicon：同一 SVG 把 currentColor 换成实体品牌色后编码为 data URI
      （favicon 不能继承页面 color，不替换会渲染成黑色）。
    SVG 缺失、损坏或含脚本时静默降级为空串，报表渲染不受影响。
    """
    try:
        svg = LOGO_SVG.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "", ""
    low = svg.lower()
    if (
        not low.startswith("<svg")
        or "<script" in low
        or "onload=" in low
        or "onerror=" in low
        or "<foreignobject" in low
    ):
        return "", ""
    logo_html = (
        '<span class="logo" aria-hidden="true" focusable="false">'
        + svg + "</span>"
    )
    favicon = "data:image/svg+xml," + quote(
        svg.replace("currentColor", LOGO_COLOR), safe=""
    )
    favicon_link = f'<link rel="icon" type="image/svg+xml" href="{favicon}">'
    return logo_html, favicon_link


def _ball_badges_from_text(text: str, css: str) -> str:
    """把 "03 13 20 25 34" 渲染为圆点；"待开奖" 等非号码文本原样转义。"""
    tokens = (text or "").split()
    if not all(tok.isdigit() for tok in tokens):
        return f'<span class="pending">{escape(text)}</span>'
    return "".join(f'<span class="{css}">{int(tok):02d}</span>' for tok in tokens)


def render_all(records: List[dict]) -> None:
    """渲染全部报表产物；传入全量记录，各渲染函数内部裁剪表格行数。"""
    render_markdown(records)
    render_xlsx(records)
    render_html(records)
