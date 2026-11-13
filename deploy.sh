#!/usr/bin/env bash
# 大乐透脚本 VPS 一键部署（Ubuntu 20.04 / 22.04 / 24.04）
# 用法：bash deploy.sh [安装目录]     默认安装目录 /opt/daletou
#
# 两种部署结构均支持：
#   1) 代码在 ~/daletou（git checkout），运行目录 /opt/daletou：拷贝文件 + 维护
#   2) /opt/daletou 本身就是 git 工作树：原地模式，跳过拷贝，只做维护检查
#      （此时 git pull 已更新全部文件，本脚本并非必需）
# 兼容 root 与普通用户运行；
# 自动修复 CRLF 行尾（Windows 传输引入会导致 shebang 与 cron 失效）；
# 依赖安装走 apt 系统包优先、pip 兜底（兼容 Ubuntu 24.04 的 PEP 668 限制）。
# 不自动写入 crontab，避免覆盖用户既有任务。
# Guard 1: sourcing this file would apply 'set -e' to the caller's shell
# and terminate the interactive SSH session. Reject before any shell option.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf '[ERROR] Run as: bash deploy.sh [APP_DIR]. Do not source this file.\n' >&2
  return 1
fi
set -euo pipefail

APP_DIR="${1:-/opt/daletou}"
PY="${PYTHON:-python3}"

# Guard 2: when the git checkout IS the install dir, "cp" would fail with
# "are the same file". Switch to in-place maintenance instead of aborting.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -d "$APP_DIR" ]]; then
  APP_DIR_REAL="$(cd "$APP_DIR" && pwd -P)"
else
  APP_DIR_REAL="$APP_DIR"
fi
IN_PLACE=0
if [[ "$SRC_DIR" == "$APP_DIR_REAL" ]]; then
  IN_PLACE=1
fi

echo "[部署] 目标目录：$APP_DIR"
if [[ "$IN_PLACE" -eq 1 ]]; then
  echo "[部署] 原地模式：当前目录即 git 工作树，跳过拷贝，仅做维护检查"
fi
echo "[部署] Python：$($PY --version 2>&1)"

MYUID="$(id -u)"
if [[ "$MYUID" -eq 0 ]]; then
  SUDO=""
  APP_USER="${SUDO_USER:-root}"
  echo "[部署] 以 root 运行（VPS 常见场景），无需 sudo"
else
  SUDO="sudo"
  APP_USER="${USER:-$(id -un)}"
  echo "[部署] 以 $APP_USER 运行，需要提权的步骤会自动 sudo"
fi

# 1) 目录与日志；原地模式不 chown，避免把 .git 改属主后触发
#    git "dubious ownership" 保护，导致后续 git pull 失败。
$SUDO mkdir -p "$APP_DIR/logs"
if [[ "$IN_PLACE" -eq 0 ]]; then
  if [[ "$MYUID" -eq 0 ]]; then
    chown -R "$APP_USER" "$APP_DIR" 2>/dev/null || true
  else
    $SUDO chown -R "$APP_USER" "$APP_DIR" 2>/dev/null || true
  fi
fi
mkdir -p "$APP_DIR/logs"

# 2) 拷贝脚本（原地模式下只核对文件是否齐全）
FILES=(config.py generator.py storage.py report.py fetcher.py notify.py lotto.py
       fetch_results.py prize_rules.py pages_setup.py requirements.txt
       money-bag.svg run.sh sync_pages.sh daletou.cron .env.example)
copied=0
if [[ "$IN_PLACE" -eq 1 ]]; then
  for f in "${FILES[@]}"; do
    if [[ -f "$APP_DIR/$f" ]]; then
      copied=$((copied + 1))
    else
      echo "[警告] 原地模式缺少 $f（请确认 git pull 是否完整）"
    fi
  done
else
  for f in "${FILES[@]}"; do
    if [[ -f "$f" ]]; then
      cp "$f" "$APP_DIR/"
      copied=$((copied + 1))
    else
      echo "[警告] 当前目录缺少 $f（跳过）"
    fi
  done
fi
if [[ "$copied" -eq 0 ]]; then
  echo "[错误] 未找到任何脚本文件，请在本项目目录内运行本脚本"
  exit 1
fi

# 3) 规范化行尾：CRLF -> LF（否则 shebang 与 cron 均会失效）
for f in "$APP_DIR"/*.sh "$APP_DIR"/*.cron; do
  if [[ -f "$f" ]]; then
    sed -i 's/\r$//' "$f"
  fi
done
for f in "$APP_DIR/run.sh" "$APP_DIR/sync_pages.sh"; do
  if [[ -f "$f" ]]; then
    chmod +x "$f"
  fi
done
if [[ "$IN_PLACE" -eq 1 ]]; then
  echo "[部署] 原地模式：已核对 $copied 个文件并规范化行尾（CRLF -> LF）"
else
  echo "[部署] 已拷贝 $copied 个文件并规范化行尾（CRLF -> LF）"
fi

# 4) 依赖：优先 apt 系统包（Ubuntu 24.04 受 PEP 668 限制，裸 pip 会失败）
installed=0
if command -v apt-get >/dev/null 2>&1; then
  if $SUDO apt-get install -y python3-openpyxl >/dev/null 2>&1; then
    installed=1
  fi
fi
if [[ "$installed" -eq 0 && "$MYUID" -eq 0 ]]; then
  if $PY -m pip install --break-system-packages -q -r "$APP_DIR/requirements.txt" >/dev/null 2>&1; then
    installed=1
  elif $PY -m pip install -q -r "$APP_DIR/requirements.txt" >/dev/null 2>&1; then
    installed=1
  fi
fi
if [[ "$installed" -eq 0 && "$MYUID" -ne 0 ]]; then
  if $PY -m pip install --user -q -r "$APP_DIR/requirements.txt" >/dev/null 2>&1; then
    installed=1
  fi
fi
if [[ "$installed" -eq 1 ]]; then
  echo "[部署] openpyxl 已就绪（用于生成 results.xlsx）"
else
  echo "[警告] openpyxl 安装失败，results.xlsx 将被跳过（results.md / results.html 仍可用）"
fi

# 5) .env 模板
if [[ ! -f "$APP_DIR/.env" ]]; then
  if [[ -f "$APP_DIR/.env.example" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  else
    printf 'SERVERCHAN_SENDKEY=\n' > "$APP_DIR/.env"
  fi
  echo "[部署] 已生成 $APP_DIR/.env —— 请编辑填入 SERVERCHAN_SENDKEY"
else
  echo "[部署] .env 已存在，跳过（请确认 SENDKEY 已填写）"
fi

# 6) 自检：模块导入与 openpyxl 可用性
$PY - "$APP_DIR" <<'PYEOF' || true
import sys
sys.path.insert(0, sys.argv[1])
try:
    import config, generator, storage, report, fetcher, notify, fetch_results, prize_rules, pages_setup
except ImportError as exc:
    print("[警告] 模块导入失败:", exc)
    sys.exit(0)
try:
    import openpyxl
    print("[自检] 模块导入正常，openpyxl 可用")
except ImportError:
    print("[自检] 模块导入正常，openpyxl 不可用（将跳过 results.xlsx）")
PYEOF

# 7) crontab 指引（不自动安装）
echo
echo "[部署] crontab 请用追加方式安装（保留已有任务，勿整体覆盖）："
echo "    crontab -l > ~/crontab.bak 2>/dev/null || true"
echo "    ( crontab -l 2>/dev/null | sed 's/\r\$//' | grep -v -e 'daletou' -e '^CRON_TZ=' -e '^SHELL=' -e '^PATH=' -e '^#'; cat $APP_DIR/daletou.cron ) | crontab -"
echo
echo "[部署] 手动验证命令（务必用 run.sh，它会加载 .env）："
echo "    cd $APP_DIR && ./run.sh selftest"
echo "    cd $APP_DIR && ./run.sh fetch --dry-run"
echo "    cd $APP_DIR && ./run.sh report"
echo
echo "[部署] 完成。请编辑 .env 填入 SENDKEY，再安装 crontab 即可上线。"
