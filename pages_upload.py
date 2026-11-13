"""Cloudflare Pages Direct Upload —— 纯标准库实现，无需 Node.js / Wrangler。

用法（VPS 上可独立执行，无需 run.sh 包裹）：
    python3 pages_upload.py --selftest        # 仅自检 BLAKE3 与 multipart，不联网
    python3 pages_upload.py --stage DIR       # 把 DIR 内的静态文件部署到 Pages
    python3 pages_upload.py --stage DIR --dry-run   # 只打印 manifest，不联网

凭证从同目录 .env 读取（不覆盖已有环境变量）：
    CLOUDFLARE_API_TOKEN   自定义 API 令牌，权限 Account -> Cloudflare Pages -> Edit
    CLOUDFLARE_ACCOUNT_ID  32 位账户 ID
    PAGES_PROJECT          项目名，默认 daletou
    PAGES_BRANCH           生产分支，默认 main（须与项目 production_branch 一致）

设计约束
--------
- 只用 Python 标准库；路径基于 __file__；无交互、固定超时、明确退出码；
- 不写业务数据，不修改 records.csv / results.*；失败时不写任何状态文件；
- 所有外部请求集中在 _request()，便于离线 mock 测试；
- BLAKE3 为内嵌纯 Python 实现（unkeyed / 32 字节输出），附官方向量自检。
  Cloudflare 的 asset key 不是文件字节的 SHA-256，而是
  blake3(base64(file_bytes) + extension).hex()[:32]；哈希错误会导致部署成功
  但所有页面 404，且 API 不报错，因此 --selftest 是上线前必跑项。

协议（Cloudflare Pages Direct Upload，四个阶段）：
    1 POST/GET /accounts/{account}/pages/projects/{project}/upload-token  (API 令牌)
    2 POST     /pages/assets/upload                                       (短期 JWT)
    3 POST     /pages/assets/upsert-hashes                                (短期 JWT)
    4 POST     /accounts/{account}/pages/projects/{project}/deployments   (API 令牌)
阶段 2/3 的路径不带 /accounts 前缀，JWT 已携带账户范围。

退出码：0 成功（含跳过）/ 1 请求或协议失败 / 2 凭证或输入缺失。
"""
import argparse
import base64
import json
import os
import secrets
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# 允许用 PAGES_API_BASE 指向本地 mock 做离线协议测试；默认始终为官方端点。
API_BASE = os.environ.get(
    "PAGES_API_BASE", "https://api.cloudflare.com/client/v4"
).rstrip("/")
TIMEOUT = 30
DEFAULT_PROJECT = "daletou"
DEFAULT_BRANCH = "main"

# 这些文件由 Cloudflare 作为部署元数据消费，不进 asset manifest。
SPECIAL_FILES = frozenset(
    ("_headers", "_redirects", "_routes.json", "_worker.js", "_worker.bundle")
)

# ---------- BLAKE3（unkeyed hash，纯 Python） ----------

_IV = (
    0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
    0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19,
)
_MSG_PERM = (2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8)
_CHUNK_START = 1
_CHUNK_END = 2
_PARENT = 4
_ROOT = 8
_MASK32 = 0xFFFFFFFF


def _rotr32(x: int, n: int) -> int:
    return ((x >> n) | (x << (32 - n))) & _MASK32


def _g(v, a, b, c, d, mx, my):
    v[a] = (v[a] + v[b] + mx) & _MASK32
    v[d] = _rotr32(v[d] ^ v[a], 16)
    v[c] = (v[c] + v[d]) & _MASK32
    v[b] = _rotr32(v[b] ^ v[c], 12)
    v[a] = (v[a] + v[b] + my) & _MASK32
    v[d] = _rotr32(v[d] ^ v[a], 8)
    v[c] = (v[c] + v[d]) & _MASK32
    v[b] = _rotr32(v[b] ^ v[c], 7)


def _round(v, m):
    _g(v, 0, 4, 8, 12, m[0], m[1])
    _g(v, 1, 5, 9, 13, m[2], m[3])
    _g(v, 2, 6, 10, 14, m[4], m[5])
    _g(v, 3, 7, 11, 15, m[6], m[7])
    _g(v, 0, 5, 10, 15, m[8], m[9])
    _g(v, 1, 6, 11, 12, m[10], m[11])
    _g(v, 2, 7, 8, 13, m[12], m[13])
    _g(v, 3, 4, 9, 14, m[14], m[15])


def _compress(cv, block_words, counter, block_len, flags):
    """返回 16 个 32-bit word：前 8 个是 chaining value，全部用于 root output。"""
    state = list(cv) + list(_IV[:4]) + [
        counter & _MASK32, (counter >> 32) & _MASK32, block_len, flags,
    ]
    v = state[:]
    m = list(block_words)
    for rnd in range(7):
        _round(v, m)
        if rnd != 6:
            m = [m[p] for p in _MSG_PERM]
    out = [v[i] ^ v[i + 8] for i in range(8)]
    out += [v[i + 8] ^ cv[i] for i in range(8)]
    return out


def _words(data: bytes):
    return list(struct.unpack("<16I", data.ljust(64, b"\x00")))


def _chunk_cv(chunk: bytes, counter: int):
    """返回 (chaining_value, last_output)。last_output 供单 chunk 的 ROOT 使用。"""
    cv = list(_IV)
    blocks = [chunk[i:i + 64] for i in range(0, len(chunk), 64)] or [b""]
    last = None
    for i, block in enumerate(blocks):
        flags = 0
        if i == 0:
            flags |= _CHUNK_START
        if i == len(blocks) - 1:
            flags |= _CHUNK_END
        bw = _words(block)
        out = _compress(cv, bw, counter, len(block), flags)
        last = (cv, bw, counter, len(block), flags, out)
        cv = out[:8]
    return cv, last


def _left_len(n: int) -> int:
    """小于 n 的最大 2 的幂（BLAKE3 左平衡树）。"""
    return 1 << ((n - 1).bit_length() - 1)


def _subtree_cv(cvs):
    if len(cvs) == 1:
        return cvs[0]
    left = _left_len(len(cvs))
    l = _subtree_cv(cvs[:left])
    r = _subtree_cv(cvs[left:])
    return _compress(list(_IV), l + r, 0, 64, _PARENT)[:8]


def blake3_bytes(data: bytes) -> bytes:
    """32 字节 BLAKE3 摘要。"""
    chunks = [data[i:i + 1024] for i in range(0, len(data), 1024)] or [b""]
    if len(chunks) == 1:
        _, last = _chunk_cv(chunks[0], 0)
        cv, bw, counter, block_len, flags, _ = last
        words = _compress(cv, bw, counter, block_len, flags | _ROOT)
    else:
        cvs = [_chunk_cv(chunk, i)[0] for i, chunk in enumerate(chunks)]
        left = _left_len(len(cvs))
        l = _subtree_cv(cvs[:left])
        r = _subtree_cv(cvs[left:])
        words = _compress(list(_IV), l + r, 0, 64, _PARENT | _ROOT)
    return struct.pack("<16I", *words)[:32]


def blake3_hex(data: bytes) -> str:
    return blake3_bytes(data).hex()


def cf_asset_hash(data: bytes, filename: str) -> str:
    """Cloudflare Pages asset key（见模块注释）：扩展名不带点。"""
    ext = Path(filename).suffix.lstrip(".").encode("ascii", errors="ignore")
    return blake3_hex(base64.b64encode(data) + ext)[:32]


# 官方 BLAKE3 test_vectors：输入为 bytes(i % 251 for i in range(n))，
# 这里记录默认 32 字节输出（官方 hash 字段是 128 字节 XOF，取前 32）。
_SELFTEST_VECTORS = (
    (0, "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"),
    (1, "2d3adedff11b61f14c886e35afa036736dcd87a74d27b5c1510225d0f592e213"),
    (2, "7b7015bb92cf0b318037702a6cdd81dee41224f734684c2c122cd6359cb1ee63"),
    (3, "e1be4d7a8ab5560aa4199eea339849ba8e293d55ca0a81006726d184519e647f"),
    (63, "e9bc37a594daad83be9470df7f7b3798297c3d834ce80ba85d6e207627b7db7b"),
    (64, "4eed7141ea4a5cd4b788606bd23f46e212af9cacebacdc7d1f4c6dc7f2511b98"),
    (65, "de1e5fa0be70df6d2be8fffd0e99ceaa8eb6e8c93a63f2d8d1c30ecb6b263dee"),
    (1023, "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11"),
    (1024, "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7"),
    (1025, "d00278ae47eb27b34faecf67b4fe263f82d5412916c1ffd97c8cb7fb814b8444"),
    (2048, "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a"),
    (31744, "62b6960e1a44bcc1eb1a611a8d6235b6b4b78f32e7abc4fb4c6cdcce94895c47"),
    (102400, "bc3e3d41a1146b069abffad3c0d44860cf664390afce4d9661f7902e7943e085"),
)


def selftest() -> int:
    ok = True
    for length, expected in _SELFTEST_VECTORS:
        data = bytes(i % 251 for i in range(length))
        got = blake3_hex(data)
        if got != expected:
            print(f"[FAIL] BLAKE3 len={length}: {got} != {expected}")
            ok = False
    print(f"[自检] BLAKE3 官方向量 {len(_SELFTEST_VECTORS)} 项："
          f"{'全部通过' if ok else '存在失败'}")
    h1 = cf_asset_hash(b"<html></html>", "index.html")
    h2 = cf_asset_hash(b"<html></html>", "results.html")
    if h1 != h2 or len(h1) != 32:
        print("[FAIL] asset hash 口径异常")
        ok = False
    else:
        print(f"[自检] asset hash 口径正常：{h1}")
    body, ctype = _multipart(
        [("manifest", '{"/index.html":"abc"}'), ("branch", "main")],
        [("_headers", "_headers", b"/*\n  Cache-Control: no-cache\n", "text/plain")],
    )
    if (b'name="manifest"' not in body or b'filename="_headers"' not in body
            or "boundary=" not in ctype or not body.endswith(b"--\r\n")):
        print("[FAIL] multipart 构造异常")
        ok = False
    else:
        print("[自检] multipart 构造正常")
    return 0 if ok else 1


# ---------- HTTP ----------

def _load_dotenv() -> None:
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


def _request(method: str, path: str, token: str, payload=None,
             raw: bytes = None, content_type: str = None):
    """调用 Cloudflare API，返回 (result, error_message)。"""
    url = API_BASE + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "daletou-pages-upload/1.0",
    }
    data = raw
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw is not None and content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        return None, f"HTTP {exc.code}: {body}"
    except (OSError, UnicodeError) as exc:
        return None, f"网络错误: {exc}"
    try:
        obj = json.loads(text)
    except ValueError:
        return None, f"响应非 JSON: {text[:200]}"
    if isinstance(obj, dict) and obj.get("success") is False:
        errors = obj.get("errors") or []
        detail = "; ".join(
            f"{e.get('code')}: {e.get('message')}" for e in errors
        )
        return None, detail or "API 返回 success=false"
    return obj.get("result") if isinstance(obj, dict) else obj, None


def _project_path(account: str, project: str) -> str:
    name = urllib.parse.quote(project, safe="")
    return f"/accounts/{account}/pages/projects/{name}"


def _multipart(fields, files):
    boundary = "----daletou" + secrets.token_hex(16)
    out = bytearray()
    for name, value in fields:
        out += b"--" + boundary.encode() + b"\r\n"
        out += (f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode()
        out += value.encode("utf-8") + b"\r\n"
    for name, filename, data, ctype in files:
        out += b"--" + boundary.encode() + b"\r\n"
        out += (f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n').encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode()
        out += data + b"\r\n"
    out += b"--" + boundary.encode() + b"--\r\n"
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _upload_token(account: str, project: str, token: str):
    """不同 Wrangler 版本使用 POST 或 GET，两者都试，避免版本差异。"""
    last_err = ""
    for method in ("POST", "GET"):
        payload = {} if method == "POST" else None
        result, err = _request(
            method, _project_path(account, project) + "/upload-token",
            token, payload,
        )
        if err:
            last_err = err
            continue
        if isinstance(result, dict):
            jwt = result.get("jwt") or result.get("token")
        else:
            jwt = result
        if jwt:
            return jwt, None
        last_err = "upload-token 响应中未找到 jwt"
    return None, last_err or "无法获取 upload-token"


def _collect(stage: Path):
    """返回 (manifest, upload_payload, hashes, special_files)。"""
    manifest = {}
    payload = []
    hashes = []
    seen = {}
    specials = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(stage).as_posix()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"无法读取 stage 文件 {path}: {exc}") from exc
        if path.name in SPECIAL_FILES:
            specials.append((path.name, path.name, data, "text/plain"))
            continue
        key = cf_asset_hash(data, path.name)
        manifest["/" + rel] = key
        if key not in seen:
            seen[key] = True
            hashes.append(key)
            ctype = "text/html; charset=utf-8" if path.suffix == ".html" \
                else "application/octet-stream"
            payload.append({
                "key": key,
                "value": base64.b64encode(data).decode("ascii"),
                "metadata": {"contentType": ctype},
                "base64": True,
            })
    return manifest, payload, hashes, specials


def deploy(stage: Path, account: str, project: str, token: str,
           branch: str, dry_run: bool = False):
    try:
        manifest, payload, hashes, specials = _collect(stage)
    except RuntimeError as exc:
        return 1, str(exc)
    if not manifest:
        return 1, "stage 目录中没有可部署的静态文件"
    print(f"[上传] 文件 {len(manifest)} 个，唯一 asset {len(hashes)} 个，"
          f"特殊文件 {len(specials)} 个")
    if dry_run:
        print("[dry-run] manifest: " + json.dumps(manifest, ensure_ascii=False))
        return 0, None

    jwt, err = _upload_token(account, project, token)
    if err:
        return 1, f"阶段 1 获取 upload-token 失败：{err}"
    print("[上传] 阶段 1/4：已获取短期 JWT")

    _, err = _request("POST", "/pages/assets/upload", jwt, payload)
    if err:
        return 1, f"阶段 2/4 assets/upload 失败：{err}"
    print("[上传] 阶段 2/4：asset 上传完成")

    _, err = _request("POST", "/pages/assets/upsert-hashes", jwt,
                      {"hashes": hashes})
    if err:
        return 1, f"阶段 3/4 upsert-hashes 失败：{err}"
    print("[上传] 阶段 3/4：hash 登记完成")

    fields = [
        ("manifest", json.dumps(manifest, separators=(",", ":"))),
        ("branch", branch),
    ]
    body, ctype = _multipart(fields, specials)
    result, err = _request(
        "POST", _project_path(account, project) + "/deployments",
        token, raw=body, content_type=ctype,
    )
    if err:
        return 1, f"阶段 4/4 创建 deployment 失败：{err}"
    url = result.get("url") if isinstance(result, dict) else None
    file_count = result.get("file_count") if isinstance(result, dict) else None
    if file_count == 0:
        return 1, "deployment 返回 file_count=0，asset 可能未被登记（勿忽略）"
    print(f"[上传] 阶段 4/4：部署完成 {url or '(未返回 url)'}")
    return 0, None


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pages_upload",
        description="Cloudflare Pages Direct Upload（纯标准库，无 Wrangler 依赖）",
    )
    parser.add_argument("--stage", type=Path, help="待部署的静态目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印 manifest")
    parser.add_argument("--selftest", action="store_true", help="离线自检")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    _load_dotenv()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    project = os.environ.get("PAGES_PROJECT", "").strip() or DEFAULT_PROJECT
    branch = os.environ.get("PAGES_BRANCH", "").strip() or DEFAULT_BRANCH

    if args.stage is None:
        print("[失败] 需要 --stage DIR（或 --selftest）")
        return 2
    stage = args.stage if args.stage.is_absolute() else (BASE_DIR / args.stage)
    if not stage.is_dir():
        print(f"[失败] stage 目录不存在：{stage}")
        return 2
    if not args.dry_run and (not token or not account):
        print("[失败] 缺少 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID。")
        print("[提示] 填入同目录 .env（模板见 .env.example）后重跑。")
        return 2

    rc, err = deploy(stage, account, project, token, branch, args.dry_run)
    if err:
        print(f"[失败] {err}")
        print("[提示] 可设置 PAGES_UPLOADER=wrangler 改用官方 Wrangler 通道；"
              "本次失败不写状态，下次同步会自动重试。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
