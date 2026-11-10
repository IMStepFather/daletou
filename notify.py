"""通知模块：Server酱微信推送。

SENDKEY 读取顺序：环境变量 SERVERCHAN_SENDKEY -> config.SERVERCHAN_SENDKEY
（建议用环境变量，避免密钥入库）。

发送失败仅告警、不中断主流程：通知为辅助能力，生成与存储不可因
推送故障而失败。仅用标准库 urllib。
"""
import json
import os
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from config import COST_PER_TICKET, SERVERCHAN_API, SERVERCHAN_SENDKEY

TIMEOUT = 15


def _get_sendkey() -> str:
    env = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    return env or SERVERCHAN_SENDKEY


def send_wechat(title: str, markdown: str) -> Tuple[bool, str]:
    """推送 Markdown 消息到微信，返回 (是否成功, 提示信息)。

    title 最长 32 字（Server酱限制）；desp 为 Markdown 正文。
    """
    key = _get_sendkey()
    if not key:
        return False, (
            "未配置 SENDKEY（设环境变量 SERVERCHAN_SENDKEY，"
            "或在 config.py 填 SERVERCHAN_SENDKEY）"
        )
    url = f"{SERVERCHAN_API}/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": markdown}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": "lotto-script/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return False, f"推送请求失败: {exc}"
    if result.get("code") == 0:
        return True, "已推送微信"
    return False, f"Server酱返回错误: {result.get('message') or result}"


def build_generate_message(record: dict, weekday_cn: str) -> Tuple[str, str]:
    """生成「下一期预测」推送内容（含推算期号与单期成本）。"""
    front = " ".join(f"{n:02d}" for n in record["front"])
    back = " ".join(f"{n:02d}" for n in record["back"])
    issue = (record.get("expected_issue") or "").strip()
    issue_text = f"第{issue}期" if issue else "期号待定"
    cost = record.get("cost", COST_PER_TICKET)
    title = f"大乐透预测 {record['date']}"
    body = (
        f"### {record['date']}（周{weekday_cn}）{issue_text}预测号码\n\n"
        f"**前区**：{front}\n\n"
        f"**后区**：{back}\n\n"
        f"单期成本 ¥{cost}（基本 2 元 + 追加 1 元）。"
        f"开奖当晚 21:30 后自动抓取开奖号，计算奖级、奖金与预估收益，"
        f"详见 results.html。"
    )
    return title, body


def build_compare_message(
    record: dict,
    front_hit: int,
    back_hit: int,
    front_count: int,
    back_count: int,
    weekday_cn: Optional[str] = None,
) -> Tuple[str, str]:
    """生成「开奖对比」推送内容（含奖级、奖金、成本与预估收益）。"""
    pf = " ".join(f"{n:02d}" for n in record["front"])
    pb = " ".join(f"{n:02d}" for n in record["back"])
    af = " ".join(f"{n:02d}" for n in record["actual_front"])
    ab = " ".join(f"{n:02d}" for n in record["actual_back"])
    issue = (record.get("issue") or "").strip()
    actual_label = f"开奖 第{issue}期" if issue else "开奖"
    level = (record.get("prize_level") or "").strip() or "待计算"
    prize = record.get("prize")
    cost = record.get("cost", COST_PER_TICKET)
    profit = None if prize is None else prize - cost
    prize_text = "待重算" if prize is None else f"¥{prize}"
    profit_text = (
        "--" if profit is None
        else (f"-¥{abs(profit)}" if profit < 0 else f"¥{profit}")
    )

    title = f"大乐透开奖对比 {record['date']}"
    lines = [f"### {record['date']} 开奖对比\n"]
    lines.append("| 项目 | 前区 | 后区 | 命中 |")
    lines.append("|:---|:---|:---|:---|")
    lines.append(
        f"| 预测 | {pf} | {pb} | "
        f"前区 {front_hit}/{front_count} · 后区 {back_hit}/{back_count} |"
    )
    lines.append(f"| {actual_label} | {af} | {ab} |  |")
    lines.append("")
    lines.append(f"**奖级**：{level}　**中奖金额**：{prize_text}")
    lines.append(f"**成本**：¥{cost}　**预估收益**：{profit_text}")
    lines.append("")
    lines.append(
        "> 口径：单式 1 注按追加投注计；浮动奖取网页基本单注奖金 ×1.8，"
        "固定奖按奖池 8 亿元档位。"
    )
    lines.append("完整历史、累计正确率与收益见 results.html。")
    return title, "\n".join(lines)
