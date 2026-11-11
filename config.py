"""配置模块：路径常量、区域规则、调度参数。

所有路径基于本文件所在目录解析（pathlib），Windows 开发环境与
Ubuntu VPS 部署环境行为一致。
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---- 区域规则：前区 1-35 取 5 个，后区 1-12 取 2 个 ----
FRONT_MIN = 1
FRONT_MAX = 35
FRONT_COUNT = 5
BACK_MIN = 1
BACK_MAX = 12
BACK_COUNT = 2

# ---- 调度：开奖日周一/三/六；预测在开奖日次日 09:00 生成 ----
DRAW_WEEKDAYS = (0, 2, 5)       # 开奖日（周一=0，周三=2，周六=5）
PREDICT_WEEKDAYS = (1, 3, 6)    # 预测生成日（周二=1，周四=3，周日=6）

# ---- 规则与成本 ----
COST_PER_TICKET = 3             # 单式 1 注：基本投注 2 元 + 追加投注 1 元
RULE_SOURCE_URL = (
    "https://www.shsportslottery.com/shxw/1217556253.html"
)

# ---- 数据与输出文件（数据真相在 CSV，其余为渲染产物） ----
RECORDS_CSV = BASE_DIR / "records.csv"
RESULTS_MD = BASE_DIR / "results.md"
RESULTS_XLSX = BASE_DIR / "results.xlsx"
RESULTS_HTML = BASE_DIR / "results.html"

# ---- 报表展示 ----
# 表格/页面仅展示最近 N 期，避免行数与 HTML 体积随期数无限增长；
# records.csv 仍保留全部历史，累计统计也按全量计算。
DISPLAY_MAX_PERIODS = 20

# ---- 抓取参数 ----
FETCH_MIN_ISSUES = 30        # 每次最少抓取期数
FETCH_MAX_ISSUES = 200       # 每次最多抓取期数（约 466 天）
FETCH_BUFFER_ISSUES = 10     # 在估算期数之上追加的缓冲
TICKETS_PER_WEEK = 3         # 每周一/三/六，用于按天数估算期数
CN_TZ = "Asia/Shanghai"      # 业务日期统一用上海时区

# ---- 并发控制 ----
FETCH_LOCK = BASE_DIR / ".fetch.lock"

# ---- 通知配置（SENDKEY 从环境变量 SERVERCHAN_SENDKEY 或此处读取） ----
SERVERCHAN_SENDKEY = ""
SERVERCHAN_API = "https://sctapi.ftqq.com"
