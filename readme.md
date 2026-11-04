# 大乐透选号与开奖对比

每周一、三、六生成一组号码（前区 1–35 选 5、后区 1–12 选 2，两区独立、无放回抽取），开奖后抓取与预测日期对应的开奖结果，回填对比并统计前区/后区命中率。结果输出 `results.md` / `results.xlsx` / `results.html`，并可推送微信。

> 开奖是独立随机事件，任何算法都无法提高中奖概率。本项目只做随机选号、结果留痕与事后统计。

## 组成

- `lotto.py` — CLI 入口：`generate` / `record` / `fetch` / `report` / `notify` / `selftest`
- `fetch_results.py` — 独立抓取入口，cron 实际调用，可单独执行
- `generator.py`、`storage.py`、`fetcher.py`、`report.py`、`notify.py`、`config.py` — 各功能层
- `run.sh` — cron 包装（加载 `.env` 并转发）；`daletou.cron` — 定时任务；`deploy.sh` — VPS 一键部署

`records.csv` 是唯一数据真相，`results.*` 为可重建的渲染产物；它们与 `.env` 均不纳入版本库。

## 本地使用

Python 3.10+，零必需第三方依赖（openpyxl 仅用于生成 xlsx，缺失时自动跳过）。

```bash
cp .env.example .env          # 填入 Server酱 SENDKEY；留空则不推送
./run.sh selftest
./run.sh generate --force     # 正式运行由 cron 在开奖日 09:05 触发，无需 --force
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

## 关键约定

- 时区统一 `Asia/Shanghai`；`.sh` / `.cron` 必须为 LF（由 `.gitattributes` 强制）。
- 开奖数据取自 500 彩票网，经批次校验后**仅回填与预测日期严格相等**的期次，其余期次仅作内存索引。
- 定时任务：开奖日 09:05 生成；21:35 / 22:30 / 23:30 三次抓取重试（幂等，不会重复推送）。
