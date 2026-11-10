# 大乐透选号与开奖对比

每周一、三、六开奖；**开奖日次日 09:00（周二/四/日）生成下一期预测**（前区 1–35 选 5、后区 1–12 选 2，两区独立、无放回抽取）。开奖后抓取与预测日期对应的开奖结果，判定奖级、计算中奖金额与预估收益，输出 `results.md` / `results.xlsx` / `results.html` 并推送微信。

> 开奖是独立随机事件，任何算法都无法提高中奖概率。彩票返奖率 51%，净收益长期期望为负；本项目只做随机选号、结果留痕与事后统计。

## 组成

- `lotto.py` — CLI 入口：`generate` / `record` / `fetch` / `report` / `notify` / `rules` / `selftest`
- `fetch_results.py` — 独立抓取入口，cron 实际调用，可单独执行
- `prize_rules.py` — 中奖规则引擎（2026 新规，7 奖级 / 13 中奖条件 / 奖池两档 / 追加 80%）
- `generator.py`、`storage.py`、`fetcher.py`、`report.py`、`notify.py`、`config.py` — 各功能层
- `run.sh` — cron 包装（加载 `.env` 并转发）；`daletou.cron` — 定时任务；`deploy.sh` — VPS 一键部署

`records.csv` 是唯一数据真相，`results.*` 为可重建的渲染产物；它们与 `.env` 均不纳入版本库。CSV 含 `issue`（实际期号）、`expected_issue`（预测时推算期号）、`pool`（奖池）、`prize_level`、`prize`、`cost` 等字段，旧版 CSV 自动兼容。

## 开奖规则与收益口径

规则自 **第 26014 期（2026-02-02 开奖）** 起执行：9 个奖级合并为 7 个，13 个中奖条件不变；一、二等奖为浮动奖，三至七等奖为固定奖且按奖池是否达到 **8 亿元** 分两档。来源见 `prize_rules.py` 顶部，可用 `./run.sh rules` 校验官网是否变更。

| 奖级 | 中奖条件 | 奖池 < 8 亿 | 奖池 ≥ 8 亿 |
|:--|:--|:--|:--|
| 一 | 5+2 | 浮动 | 浮动 |
| 二 | 5+1 | 浮动 | 浮动 |
| 三 | 5+0 / 4+2 | ¥5,000 | ¥6,666 |
| 四 | 4+1 | ¥300 | ¥380 |
| 五 | 4+0 / 3+2 | ¥150 | ¥200 |
| 六 | 3+1 / 2+2 | ¥15 | ¥18 |
| 七 | 3+0 / 2+1 / 1+2 / 0+2 | ¥5 | ¥7 |

单期成本 **¥3**（基本投注 ¥2 + 追加投注 ¥1）。浮动奖取 500.com 公布的基本投注单注奖金，按追加投注规则 ×1.8；固定奖按奖池档位，追加不参与分配。**预估收益 = 中奖金额 − 成本**，累计仅统计已开奖且奖金已计算的期次。金额是单式 1 注的理论值，非实际兑奖额。

## 本地使用

Python 3.10+，零必需第三方依赖（openpyxl 仅用于生成 xlsx，缺失时自动跳过）。

```bash
cp .env.example .env          # 填入 Server酱 SENDKEY；留空则不推送
./run.sh selftest             # 含中奖规则 13 条件与两档奖金校验
./run.sh rules                # 校验官网规则是否与本地一致
./run.sh generate --force     # 正式由 cron 在周二/四/日 09:00 触发，无需 --force
./run.sh fetch --dry-run      # 预览待回填记录
./run.sh report               # 从 records.csv 重渲染三份报表
```

手动执行请走 `./run.sh <子命令>`：直接 `python3 lotto.py ...` 不会加载 `.env`，微信推送会失效。`fetch` 支持 `--dry-run` / `--no-notify` / `--force-correct`，退出码 `0 成功 / 1 失败 / 2 无待办`；`generate` / `fetch` / `record` 有数据变化时会推送微信。

## VPS 部署（Ubuntu）

```bash
git clone https://github.com/IMStepFather/daletou.git ~/daletou && cd ~/daletou
bash deploy.sh                       # 安装到 /opt/daletou，已存在的 .env 不覆盖
vi /opt/daletou/.env                 # SERVERCHAN_SENDKEY=...
crontab -l > ~/crontab.bak 2>/dev/null || true
( crontab -l 2>/dev/null | sed 's/\r$//' | grep -v 'daletou/run.sh' | grep -v '^CRON_TZ='; cat /opt/daletou/daletou.cron ) | crontab -
cd /opt/daletou && ./run.sh fetch --dry-run
```

私有仓库在 VPS 上需配置只读 SSH deploy key 或 fine-grained PAT。crontab **必须追加安装**，不要用 `crontab daletou.cron` 整体覆盖（会抹掉腾讯云 stargate 等既有任务）。

日常更新：`cd ~/daletou && git pull --ff-only && bash deploy.sh`。

## 定时任务与关键约定

- 周二/四/日 **09:00** 生成下一期预测：记录的 `date` 写目标开奖日（周一/三/六），`expected_issue` 由网页最新期号 +1 推算；抓取失败时号码照常生成、期号留空。
- 开奖日 **21:35 / 22:30 / 23:30** 三次抓取重试（幂等，不会重复推送），回填实际期号、奖池、奖级与奖金。
- 开奖结果**仅按日期严格相等**回填，期号只作留痕与展示，不作匹配键（休市/改期时可安全降级）。
- 时区统一 `Asia/Shanghai`；`.sh` / `.cron` 必须为 LF（由 `.gitattributes` 强制）。
