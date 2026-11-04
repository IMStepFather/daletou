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

from config import SERVERCHAN_API, SERVERCHAN_SENDKEY

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
    except Exception as exc:
        return False, f"推送请求失败: {exc}"
    if result.get("code") == 0:
        return True, "已推送微信"
    return False, f"Server酱返回错误: {result.get('message') or result}"


def build_generate_message(record: dict, weekday_cn: str) -> Tuple[str, str]:
    """生成「本期预测」推送内容。"""
    front = " ".join(f"{n:02d}" for n in record["front"])
    back = " ".join(f"{n:02d}" for n in record["back"])
    title = f"大乐透预测 {record['date']}"
    body = (
        f"### {record['date']}（周{weekday_cn}）预测号码\n\n"
        f"**前区**：{front}\n\n"
        f"**后区**：{back}\n\n"
        f"开奖当晚 21:30 后自动抓取开奖号并对比命中，详见 results.html。"
    )
    return title, body


def build_compare_message(record: dict, front_hit: int, back_hit: int,
                          front_count: int, back_count: int,
                          weekday_cn: Optional[str] = None) -> Tuple[str, str]:
    """生成「开奖对比」推送内容。"""
    pf = " ".join(f"{n:02d}" for n in record["front"])
    pb = " ".join(f"{n:02d}" for n in record["back"])
    af = " ".join(f"{n:02d}" for n in record["actual_front"])
    ab = " ".join(f"{n:02d}" for n in record["actual_back"])
    title = f"大乐透开奖对比 {record['date']}"
    lines = [f"### {record['date']} 开奖对比\n"]
    lines.append("| 项目 | 前区 | 后区 | 命中 |")
    lines.append("|:---|:---|:---|:---|")
    lines.append(
        f"| 预测 | {pf} | {pb} | "
        f"前区 {front_hit}/{front_count} · 后区 {back_hit}/{back_count} |"
    )
    lines.append(f"| 开奖 | {af} | {ab} |  |")
    lines.append("\n完整历史与累计正确率见 results.html。")
    return title, "\n".join(lines)
