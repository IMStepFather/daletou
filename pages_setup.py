"""Cloudflare Pages 一次性配置：创建项目、绑定自定义域名、查询状态。

用法（VPS 上可独立执行，无需 run.sh 包裹）：
    python3 pages_setup.py            # 等价于 all
    python3 pages_setup.py create     # 创建 Direct Upload 项目（已存在则跳过）
    python3 pages_setup.py bind       # 绑定 PAGES_DOMAIN（已绑定则跳过）
    python3 pages_setup.py status     # 只读：项目与域名/证书状态

凭证从同目录 .env 读取（不覆盖已有环境变量）：
    CLOUDFLARE_API_TOKEN    自定义 API Token，权限 Account -> Cloudflare Pages -> Edit
    CLOUDFLARE_ACCOUNT_ID   32 位账户 ID（控制台右侧栏）
    PAGES_PROJECT           项目名，默认 daletou
    PAGES_DOMAIN            自定义域名，默认 daletou.680868.xyz

设计约束
--------
- 仅用标准库 urllib；路径基于 __file__；无交互、固定超时、明确退出码；
- 所有写操作幂等：项目/域名已存在时跳过，不产生重复资源；
- 只做 Cloudflare 侧配置，不改动 records.csv 与任何业务数据。

退出码：0 成功（含跳过）/ 1 失败 / 2 凭证缺失。

相关官方接口：
    POST /accounts/{account_id}/pages/projects
    GET  /accounts/{account_id}/pages/projects/{project_name}
    GET/POST /accounts/{account_id}/pages/projects/{project_name}/domains
以上接口均要求 Pages Write 权限。域名所在 zone 已托管在 Cloudflare 时，
绑定会自动创建指向 <project>.pages.dev 的 CNAME；不要手工新建 CNAME 代替
绑定动作，否则会返回 522。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.cloudflare.com/client/v4"
TIMEOUT = 20

DEFAULT_PROJECT = "daletou"
DEFAULT_DOMAIN = "daletou.680868.xyz"


def _load_dotenv() -> None:
    """加载同目录 .env；不覆盖已存在的环境变量。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _request(method: str, path: str, token: str, payload=None):
    """调用 Cloudflare API，返回 (result, error_message)。"""
    url = API_BASE + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "daletou-pages-setup/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        return None, f"HTTP {exc.code}: {body}"
    except (OSError, UnicodeError) as exc:
        return None, f"网络错误: {exc}"
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, f"响应非 JSON: {raw[:200]}"
    if not obj.get("success"):
        errors = obj.get("errors") or []
        detail = "; ".join(
            f"{e.get('code')}: {e.get('message')}" for e in errors
        )
        return None, detail or "API 返回 success=false"
    return obj.get("result"), None


def _project_path(account: str, project: str) -> str:
    name = urllib.parse.quote(project, safe="")
    return f"/accounts/{account}/pages/projects/{name}"


def _auth_hint() -> str:
    return (
        "请检查 API Token 是否具备 Account -> Cloudflare Pages -> Edit "
        "（Pages Write）权限，且作用域包含该账户；账户 ID 为 32 位十六进制串。"
    )


def _get_project(account: str, project: str, token: str):
    result, err = _request("GET", _project_path(account, project), token)
    return result, err


def cmd_create(account: str, project: str, token: str) -> int:
    result, err = _get_project(account, project, token)
    if result is not None:
        sub = result.get("subdomain") or f"{project}.pages.dev"
        print(f"[项目] {project} 已存在，跳过创建。")
        print(f"[回退地址] https://{sub}")
        return 0
    if err and ("HTTP 401" in err or "HTTP 403" in err):
        print(f"[失败] 无权限读取项目：{err}")
        print(f"[提示] {_auth_hint()}")
        return 1
    print(f"[项目] 未找到 {project}，创建 Direct Upload 项目……")
    result, err = _request(
        "POST",
        f"/accounts/{account}/pages/projects",
        token,
        {"name": project, "production_branch": "main"},
    )
    if err:
        print(f"[失败] 创建项目失败：{err}")
        print("[备选] 在控制台 Workers & Pages -> Create -> Pages -> "
              "Drag and drop your files 手动创建同名项目（必须选 Direct Upload）。")
        return 1
    sub = (result or {}).get("subdomain") or f"{project}.pages.dev"
    print(f"[项目] 已创建 {result.get('name', project)}"
          f"（生产分支 {result.get('production_branch', 'main')}）。")
    print(f"[回退地址] https://{sub}")
    print("[提示] pages.dev 子域全局唯一，若已被占用会自动追加后缀；"
          "正式入口始终是自定义域名，不要按项目名猜测回退地址。")
    print("[提示] 若后续 wrangler 部署报项目类型不符，请删除该项目并改用控制台 "
          "Direct Upload 创建（API 未提供显式的 source 类型字段）。")
    return 0


def _list_domains(account: str, project: str, token: str):
    result, err = _request(
        "GET", _project_path(account, project) + "/domains", token
    )
    if err:
        return [], err
    if not isinstance(result, list):
        return [], "域名列表响应结构异常（非数组）"
    return result, None


def _describe_domain(domain: dict) -> str:
    validation = (domain.get("validation_data") or {}).get("status", "--")
    verification = (domain.get("verification_data") or {}).get("status", "--")
    return (
        f"{domain.get('name', '?')} | 状态 {domain.get('status', '?')}"
        f" | 验证 {validation} | 校验 {verification}"
    )


def cmd_bind(account: str, project: str, domain_name: str, token: str) -> int:
    result, err = _get_project(account, project, token)
    if err:
        print(f"[失败] 项目 {project} 不可访问：{err}")
        print(f"[提示] 先执行 python3 pages_setup.py create；{_auth_hint()}")
        return 1
    domains, err = _list_domains(account, project, token)
    if err:
        print(f"[警告] 域名列表读取失败：{err}（仍尝试绑定）")
        domains = []
    for item in domains:
        if (item.get("name") or "").lower() == domain_name.lower():
            print(f"[域名] 已绑定，跳过：{_describe_domain(item)}")
            return 0
    print(f"[域名] 绑定 {domain_name} 到 {project} ……")
    result, err = _request(
        "POST",
        _project_path(account, project) + "/domains",
        token,
        {"name": domain_name},
    )
    if err:
        print(f"[失败] 绑定失败：{err}")
        print("[提示] 必须走 Pages 项目的 Custom domains 绑定流程；"
              "仅在 DNS 里手工加 CNAME 会导致 522。")
        print("[提示] 若错误含 externally managed DNS records / already in use："
              "先在 zone 的 DNS -> Records 删除该主机名下已有的 A/AAAA/CNAME 记录，"
              "再重跑 bind。Pages 只接管自己创建的记录，不会覆盖手工记录。")
        print("[备选] 控制台：项目 -> Custom domains -> Set up a domain，"
              "填入同一域名并确认自动生成的 CNAME。")
        return 1
    print(f"[域名] 已提交：{_describe_domain(result)}")
    print("[提示] 证书签发通常需数分钟（initializing/pending -> active）；"
          "用 python3 pages_setup.py status 复查。")
    return 0


def cmd_status(account: str, project: str, token: str) -> int:
    result, err = _get_project(account, project, token)
    if err:
        print(f"[失败] 无法读取项目：{err}")
        print(f"[提示] {_auth_hint()}")
        return 1
    print(f"[项目] {result.get('name')} | 生产分支 "
          f"{result.get('production_branch')} | "
          f"子域 {result.get('subdomain', '--')}")
    latest = (result.get("latest_deployment") or {}).get("url") if isinstance(
        result.get("latest_deployment"), dict
    ) else None
    print(f"[部署] 最近一次：{latest or '尚无部署（需先跑一次 sync_pages.sh）'}")
    domains, err = _list_domains(account, project, token)
    if err:
        print(f"[失败] 无法读取域名列表：{err}")
        return 1
    if not domains:
        print("[域名] 尚未绑定任何自定义域名。")
    for item in domains:
        print(f"[域名] {_describe_domain(item)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pages_setup",
        description="Cloudflare Pages 项目与自定义域名配置（只读/幂等写）",
    )
    parser.add_argument(
        "action", nargs="?", default="all",
        choices=("all", "create", "bind", "status"),
        help="默认 all：创建项目 -> 绑定域名 -> 输出状态",
    )
    args = parser.parse_args()

    _load_dotenv()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    project = os.environ.get("PAGES_PROJECT", "").strip() or DEFAULT_PROJECT
    domain = os.environ.get("PAGES_DOMAIN", "").strip() or DEFAULT_DOMAIN

    if not token or not account:
        print("[失败] 缺少 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID。")
        print("[提示] 请编辑同目录 .env（该文件或 .env.example 模板），"
              "填入凭证后重跑；.env 不纳入版本库。")
        return 2

    if args.action == "create":
        return cmd_create(account, project, token)
    if args.action == "bind":
        return cmd_bind(account, project, domain, token)
    if args.action == "status":
        return cmd_status(account, project, token)

    rc = cmd_create(account, project, token)
    if rc != 0:
        return rc
    rc = cmd_bind(account, project, domain, token)
    if rc != 0:
        return rc
    return cmd_status(account, project, token)


if __name__ == "__main__":
    sys.exit(main())
