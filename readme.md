# 大乐透选号与开奖对比

每周一、三、六开奖；**开奖日次日 09:00（周二/四/日）生成下一期预测**（前区 1–35 选 5、后区 1–12 选 2，两区独立、无放回抽取）。开奖后抓取与预测日期对应的开奖结果，判定奖级、计算中奖金额与预估收益，输出 `results.md` / `results.xlsx` / `results.html` 并推送微信。

> 开奖是独立随机事件，任何算法都无法提高中奖概率。彩票返奖率 51%，净收益长期期望为负；本项目只做随机选号、结果留痕与事后统计。

## 组成

- `lotto.py` — CLI 入口：`generate` / `record` / `fetch` / `report` / `notify` / `rules` / `selftest`
- `fetch_results.py` — 独立抓取入口，cron 实际调用，可单独执行
- `prize_rules.py` — 中奖规则引擎（2026 新规，7 奖级 / 13 中奖条件 / 奖池两档 / 追加 80%）
- `generator.py`、`storage.py`、`fetcher.py`、`report.py`、`notify.py`、`config.py` — 各功能层
- `money-bag.svg` — 站点 logo 源文件；`report.py` 把它内联进 `results.html` 并生成 favicon，换 logo 只需替换该文件后重跑 `./run.sh report`
- `sync_pages.sh` — 独立同步入口，把 `results.html` 幂等发布到 Cloudflare Pages
- `pages_upload.py` — 纯标准库 Direct Upload（内嵌 BLAKE3，四步 API），不依赖 Node.js / Wrangler；`--selftest` 可离线自检
- `pages_setup.py` — 一次性配置 Pages 项目与自定义域名（纯标准库 API 调用）
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
cp .env.example .env                                        # 填入 Server酱 SENDKEY；留空则不推送
./run.sh selftest                                           # 含中奖规则 13 条件与两档奖金校验
./run.sh rules                                              # 校验官网规则是否与本地一致
./run.sh generate --target 2026-11-11 --offline --no-notify   # 补生成指定开奖日
./run.sh recalc --no-notify                                 # 离线重算历史「待计算」期次
./run.sh fetch --dry-run                                    # 预览待回填记录
./run.sh report                                             # 重渲染三份报表（渲染前自动重算）
```

手动执行请走 `./run.sh <子命令>`：直接 `python3 lotto.py ...` 不会加载 `.env`，微信推送会失效。`generate` 支持 `--target`（补生成指定开奖日）/ `--offline`（无网时跳过期号抓取）/ `--no-notify`；`fetch` 支持 `--dry-run` / `--no-notify` / `--force-correct`，退出码 `0 成功 / 1 失败 / 2 无待办`；`generate` / `fetch` / `record` 有数据变化时会推送微信，`recalc` 默认不推送。

## VPS 部署（Ubuntu）

**两种目录结构都支持**：

- **A（当前 VPS 实际结构）**：直接把仓库 clone 到 `/opt/daletou`，该目录既是 git 工作树又是运行目录。`git pull --ff-only` 直接更新运行中的文件，**不需要 `deploy.sh` 拷贝**；在此结构下 `deploy.sh` 自动进入「原地模式」，跳过拷贝，只核对文件、行尾、权限、依赖与自检。
- **B**：代码在 `~/daletou`，由 `deploy.sh` 把文件拷贝到 `/opt/daletou` 运行。

下面是 B 结构的首次部署流程；A 结构把 clone 目标改为 `/opt/daletou` 并跳过 `bash deploy.sh` 即可。

```bash
git clone https://github.com/IMStepFather/daletou.git ~/daletou && cd ~/daletou
bash deploy.sh                       # 安装到 /opt/daletou，已存在的 .env 不覆盖
vi /opt/daletou/.env                 # SERVERCHAN_SENDKEY=...
crontab -l > ~/crontab.bak 2>/dev/null || true
( crontab -l 2>/dev/null | sed 's/\r$//' | grep -v -e 'daletou' -e '^CRON_TZ=' -e '^SHELL=' -e '^PATH=' -e '^#'; cat /opt/daletou/daletou.cron ) | crontab -
cd /opt/daletou && ./run.sh fetch --dry-run
```

私有仓库在 VPS 上需配置只读 SSH deploy key 或 fine-grained PAT。crontab **必须追加安装**，不要用 `crontab daletou.cron` 整体覆盖（会抹掉腾讯云 stargate 等既有任务）。上面命令会先移除旧的 daletou 任务块、环境变量行与注释行（避免 `sync_pages.sh` 被重复追加），其他任务行原样保留。

日常更新（按结构二选一）：

```bash
# A. /opt/daletou 本身就是 git 仓库（推荐，也是当前 VPS 的结构）
cd /opt/daletou && git pull --ff-only && ./run.sh report
# 可选：bash deploy.sh   # 原地模式：核对文件/行尾/权限/依赖，不拷贝、不动 .git

# B. 代码在 ~/daletou、运行目录在 /opt/daletou
cd ~/daletou && git pull --ff-only && bash deploy.sh && cd /opt/daletou && ./run.sh report
```

> A 结构下旧版 `deploy.sh` 遇到「源目录 == 安装目录」会以退出码 1 拒绝执行，从而中断整条 `&&` 命令链，使后面的 `./run.sh report` 不执行；现已改为原地维护模式，不会再打断更新流程。

## 定时任务与关键约定

- **每天 09:00** 幂等检查并生成「下一个尚未开奖」的预测（实际仍落在开奖日次日）：`date` 写目标开奖日（周一/三/六），`expected_issue` 由网页上一开奖日期号 +1 推算；已有记录则跳过，cron 漏跑时下次自动补齐，已开奖的日期不会补生成。
- 开奖日 **21:35 / 22:30 / 23:30** 三次抓取重试（幂等，不会重复推送），回填实际期号、奖池、奖级与奖金。
- 开奖结果**仅按日期严格相等**回填，期号只作留痕与展示，不作匹配键（休市/改期时可安全降级）。
- 时区统一 `Asia/Shanghai`；`.sh` / `.cron` 必须为 LF（由 `.gitattributes` 强制）。

## 补生成与历史重算

**补生成漏跑期次**：`generate` 每次执行都检查「下一个尚未开奖的开奖日」是否已有记录，缺失即补齐；也可用 `--target` 显式指定（必须是周一/三/六，同日期幂等）。已过去的开奖日不会补生成，避免在开奖后伪造预测。

```bash
./run.sh generate --target 2026-11-11 --offline --no-notify
```

**历史「待计算」期次**：11-04 升级 12 列 CSV 之前的记录没有 `prize` 列，报表会显示「待计算 / 待重算」且不进入收益统计。`recalc` 按已有开奖号码离线重算：未中奖与固定奖可确定算出；浮动奖（一、二等奖）缺少页面公布的基本单注奖金时保持待重算。`report` 与 `fetch` 在刷新报表前也会自动兜底，反复执行幂等。

```bash
./run.sh recalc --no-notify                     # 离线补算奖级 / 奖金 / 收益
./run.sh fetch --force-correct --no-notify      # 联网补齐期号 / 奖池 / 浮动奖奖金
./run.sh report
```

`fetch --force-correct` 会把全部已开奖期次纳入重算，用 500.com 的 15 列数据补齐 `issue` / `pool` 与奖金，建议配合 `--no-notify` 避免重复推送。`--dry-run` 严格只读，不会触发重算写库。报表刷新后，Pages 的 5 分钟同步任务会自动更新公开页面。

## Cloudflare Pages 报表同步（可选）

**架构：VPS 仍是唯一执行与数据源，Pages 只是 `results.html` 的公开只读镜像。** `records.csv`、`.env`、抓取与计算逻辑均不上云、不入库；同步失败不会影响生成/抓取/推送主流程。

整套链路零第三方运行时依赖：**项目与域名配置**走 Cloudflare 公开 API，**报表上传**走 `pages_upload.py` 实现的 Direct Upload 四步协议（纯标准库，内嵌 BLAKE3）。因此 VPS 不需要 Node.js、npm 或 Wrangler；`Wrangler` 仅作为可选的备用通道保留。业务 Python 代码零改动。

### 1. Cloudflare 侧准备（一次性）

1. 确认 `680868.xyz` 已作为 zone 托管在当前 Cloudflare 账户，且注册商处的 NS 已改为 Cloudflare 分配的两个地址：`dig NS 680868.xyz +short` 应返回 `*.ns.cloudflare.com`。NS 未切换时 Custom domains 无法绑定。
2. **清理同名 DNS 记录**：在 zone 的 DNS → Records 中搜索 `daletou`，若已存在手工创建的 A / AAAA / CNAME（例如指向旧服务器的 A 记录），先删除。Pages 绑定只接管自己创建的记录，遇到手工记录会报 `already has externally managed DNS records` 或直接不生效；绑定成功后 Cloudflare 会自动创建指向项目的代理记录。此删除在控制台手工完成，不需要给 API 令牌授予 DNS 写权限。
3. 创建 API Token：My Profile → API Tokens → Create Token → Custom token → Get started：
   - Permissions：`Account` / `Cloudflare Pages` / `Edit`（即 Pages Write）
   - Account Resources：`Include` → 仅该账户
   - 不要授予 Zone / DNS / Workers 权限；zone 在 CF 时绑定域名会自动创建 CNAME，不需要 DNS 写权限。
4. 复制控制台右侧栏的 **Account ID**（32 位十六进制串）。Token 仅显示一次。

### 2. VPS 一次性配置

```bash
cd /opt/daletou && git pull --ff-only
python3 /opt/daletou/pages_upload.py --selftest   # 离线自检，13 项向量须全部通过
vi /opt/daletou/.env                 # 填入 CLOUDFLARE_ACCOUNT_ID 与 CLOUDFLARE_API_TOKEN
python3 /opt/daletou/pages_setup.py  # create 项目 -> bind 域名 -> 输出状态
/opt/daletou/sync_pages.sh           # 首次部署，生成 index.html
curl -I https://daletou.680868.xyz/
```

**`vi` 这一步要单独执行**：把 `.env.example` 里两个 Cloudflare 凭证键填上真实值，并确认 `PAGES_DOMAIN=daletou.680868.xyz`，保存退出后再执行后续命令；不要和下面的命令整段一起粘贴。

`pages_upload.py --selftest` 校验内嵌 BLAKE3 的 13 组官方向量与 multipart 构造。Cloudflare 的 asset key 是 `blake3(base64(文件内容) + 扩展名).hex()[:32]`，哈希算错时 API 仍返回成功、但所有页面 404，因此首次部署前必须自检通过；`sync_pages.sh` 在首次部署时也会自动跑一次。

`pages_setup.py` 幂等：项目/域名已存在时跳过。子命令 `create` / `bind` / `status` 可单独执行，`status` 为只读。`sync_pages.sh` 也可随时手动触发。

上传通道由 `.env` 的 `PAGES_UPLOADER` 决定：`auto`（默认）在检测到 `wrangler` 可执行文件时用它，否则用纯标准库通道；`python` 强制纯标准库；`wrangler` 强制 Wrangler（须自行安装并可用 `WRANGLER_BIN` 指定路径）。**不会自动调用 `npx wrangler`**：Wrangler 4.87+ 要求 Node.js 22，在 Node 18 环境冷启动下载后必然失败。若纯标准库通道与 Cloudflare API 出现兼容问题，安装 Node.js 22 + Wrangler 后设 `PAGES_UPLOADER=wrangler` 即可切回官方通道，两条路径共用同一份 `.env` 与同一个幂等状态文件。

> 若 API 创建的项目被 wrangler 判定类型不符，请在控制台 Workers & Pages → Create → Pages → **Drag and drop your files** 手动创建同名 `daletou` 项目（必须选 Direct Upload，不能选 Connect to Git），再用 `pages_setup.py bind` 绑定域名。Direct Upload 项目后续无法切换为 Git 集成。

`*.pages.dev` 子域是全局唯一命名空间，`daletou.pages.dev` 已被第三方「大乐透摇奖」站点占用，不是本项目。因此**正式入口只用自定义域名** `https://daletou.680868.xyz`；项目创建后实际分配的回退地址可能带后缀，以 `python3 /opt/daletou/pages_setup.py status` 输出的 `subdomain` 为准，不要按项目名猜测。

### 3. 挂载同步任务

`daletou.cron` 已包含同步行。**现有部署不要重装整张 crontab**，只追加这一行（先备份）：

```bash
crontab -l > ~/crontab.bak 2>/dev/null || true
( crontab -l 2>/dev/null | grep -v 'sync_pages.sh'; \
  echo '*/5 * * * * /opt/daletou/sync_pages.sh >> /opt/daletou/logs/cron.log 2>&1' ) | crontab -
```

每 5 分钟检查 `results.html` 的 SHA-256；未变化静默退出（无日志），变化才上传一次。三次开奖抓取重试不会重复部署。同步延迟最多 5 分钟，对事后留痕无影响。

`sync_pages.sh` 会生成 `_headers`（`Cache-Control: no-cache, must-revalidate`），避免 CDN/浏览器缓存旧报表；上传 `index.html` 与 `results.html` 两个入口。

`results.html` 是**自包含单文件**：CSS 与 `money-bag.svg` 均内联（页面 logo + 浏览器标签 favicon），运行时不请求任何外部资源。因为 Pages 只上传上面这几个产物，**不要改成 `<img src="money-bag.svg">` 之类的外链**，域名上不存在该 SVG 会 404。品牌色与源文件名由 `config.py` 的 `LOGO_COLOR` / `LOGO_SVG` 指定；文件缺失或含脚本时静默降级为无 logo，报表渲染不会失败。

### 4. 验证与排错

```bash
python3 /opt/daletou/pages_setup.py status     # 项目、最近部署、域名/证书状态、实际 subdomain
curl -I https://daletou.680868.xyz/            # 正式入口，期望 200
# 回退地址：把 status 输出的 subdomain 代入 curl -I https://<subdomain>/
tail -n 50 /opt/daletou/logs/cron.log
```

| 现象 | 原因与处理 |
|:--|:--|
| `skip: ... not set` | `.env` 未填凭证；脚本自加载 `.env`，填完即自动生效 |
| `Project not found [8000007]` | 项目未创建；执行 `pages_setup.py create` 或按上文控制台创建 |
| HTTP 403 / `Could not route to ...` | Token 缺 `Account → Cloudflare Pages → Edit`，或 Account ID 错误 |
| `[自检] BLAKE3 官方向量 ... 存在失败` | 立即停止部署；核对 Python 版本，或设 `PAGES_UPLOADER=wrangler` 切回官方通道 |
| 部署返回成功但页面 404 | asset hash 口径不符时 CF 会静默接受却不提供文件；先跑 `pages_upload.py --selftest` |
| `wrangler not found` | 默认通道不需要它；仅当显式设了 `PAGES_UPLOADER=wrangler` 时才需安装 Node.js 22 + Wrangler 或用 `WRANGLER_BIN` 指定路径 |
| 绑定报 `externally managed DNS records` / `already in use` | 该主机名下存在手工 A/AAAA/CNAME，或域名仍挂在旧 Pages 项目上；删除同名记录 / 从旧项目解绑后重跑 `pages_setup.py bind` |
| 自定义域解析到非 Cloudflare IP | NS 未切换到 Cloudflare，或残留手工 A 记录；`dig NS 680868.xyz +short` 核对并清理 DNS |
| `daletou.pages.dev` 打开的是陌生站点 | 该全局子域已被第三方占用，与本项目无关；只验证自定义域名 |
| 自定义域返回 522 | 只手工加了 CNAME 而未走绑定流程；执行 `pages_setup.py bind` 或控制台 Custom domains |
| 证书长时间 `pending` | 等待数分钟；检查 zone 的 CAA 记录是否允许 Cloudflare 签发 |
| 访问返回 404 | 尚未首次部署；执行 `sync_pages.sh`（`index.html` 由该脚本生成） |
| 页面显示旧数据 | 强制刷新；`_headers` 已禁用缓存，仍异常时检查是否命中了旧部署 |

### 5. 安全约定

API Token 与 SENDKEY 均只存于 `/opt/daletou/.env`（已被 `.gitignore` 排除），不进入版本库、不写入报表。报表内容仅含号码与统计，可公开。仓库若改为公开，按既有约定仍需复核并轮换 Server酱 SENDKEY。
