"""视频号下载层：优先走 wx_channel 本地服务 API，失败则等待手动下载。

wx_channel 是本地 HTTP 服务（默认 127.0.0.1:2025），外部脚本可直接调用，无鉴权。
解析接口 /api/channels/parse_sph 依赖以下二者之一：
  1. config.yaml 中配置了 cloudflare.sphHostname / cloudflare.sphCookie
  2. GUI 内置浏览器已登录视频号并作为 client 连接
两者都不满足时，脚本进入 watch 模式：等待用户在 wx_channel 里下载完成，
文件落盘后自动接管后续流程。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from modules.paths import DOWNLOADER_DIRS, REPO_ROOT

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 2025          # 旧版 wx_channel 本地服务端口
TIMEOUT = 15

WX_EXE = DOWNLOADER_DIR.parent / "wx_channel" / "wx_channel.exe"
WX_CWD = WX_EXE.parent
# GUI 下载默认落盘目录
WX_DOWNLOADS = WX_CWD / "downloads"


class DownloadError(RuntimeError):
    pass


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(path: str, data: dict | None = None, method: str | None = None,
             timeout: int = TIMEOUT) -> tuple[int, str]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        f"http://{SERVICE_HOST}:{SERVICE_PORT}{path}",
        data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)


def service_status() -> dict | None:
    """返回服务状态字典，服务不可用时返回 None。"""
    code, body = _request("/api/status", timeout=5)
    if code != 200:
        return None
    try:
        return json.loads(body).get("data", {})
    except Exception:
        return None


def ensure_service(wait_sec: int = 25) -> dict | None:
    """确保本地服务在跑；没跑就尝试拉起（脱离进程）。"""
    st = service_status()
    if st is not None:
        return st
    if not WX_EXE.exists():
        return None
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [str(WX_EXE)], cwd=str(WX_CWD), creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(1)
        st = service_status()
        if st is not None:
            return st
    return None


def is_sph_url(text: str) -> bool:
    return ("weixin.qq.com" in text) or ("channels.weixin.qq.com" in text)


def parse_sph(url: str) -> dict:
    """解析视频号链接，返回视频信息。失败抛 DownloadError。"""
    for path in ("/api/channels/parse_sph", "/api/search/parse_sph"):
        code, body = _request(path, {"url": url})
        if code == 200:
            try:
                data = json.loads(body)
            except Exception:
                continue
            if data.get("code", 0) == 0 or data.get("success"):
                return data.get("data") or data
        if code in (400, 404):
            last = body
            continue
    msg = locals().get("last", "未知错误")
    raise DownloadError(f"解析失败: {msg[:200]}")


def submit_download(video_info: dict) -> bool:
    """把解析结果提交到下载队列。"""
    videos = video_info if isinstance(video_info, list) else [video_info]
    code, body = _request("/api/v1/queue", {"videos": videos})
    if code == 200:
        return True
    code, body = _request("/api/v1/downloads", {"videos": videos})
    return code == 200


def _snapshot() -> set[str]:
    if not WX_DOWNLOADS.exists():
        return set()
    return {str(p) for p in WX_DOWNLOADS.rglob("*.mp4")}


def wait_for_manual_download(timeout_sec: int = 1800,
                             on_progress=None) -> Path | None:
    """等待用户在 wx_channel GUI 里下载完成，返回新出现的 mp4 路径。"""
    before = _snapshot()
    deadline = time.time() + timeout_sec
    waited = 0
    while time.time() < deadline:
        now = _snapshot()
        new = now - before
        if new:
            newest = max(new, key=lambda p: Path(p).stat().st_mtime)
            # 等文件写完（大小稳定）
            stable = _wait_stable(Path(newest))
            if stable:
                return Path(newest)
        if on_progress:
            on_progress(waited, timeout_sec)
        time.sleep(3)
        waited += 3
    return None


def _wait_stable(path: Path, checks: int = 3, interval: float = 2.0) -> bool:
    last = -1
    for _ in range(checks):
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == last and size > 0:
            return True
        last = size
        time.sleep(interval)
    return last > 0


def fetch_direct(url: str, dest_dir: Path, filename: str | None = None) -> Path:
    """普通直链下载（非视频号）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = filename or (urllib.parse.urlparse(url).path.rsplit("/", 1)[-1] or "video.mp4")
    target = dest_dir / name
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _opener().open(req, timeout=120) as resp, open(target, "wb") as f:
        f.write(resp.read())
    return target


# ----------------------------------------------------------------------------
# 新版下载器 MCP（v260907+，端口 2022）
# ----------------------------------------------------------------------------

MCP_URL = os.environ.get("WX_DOWNLOAD_MCP", "http://127.0.0.1:2022/mcp")
MCP_PORT = int(os.environ.get("WX_DOWNLOAD_PORT", "2022"))


def downloader_exe() -> Path | None:
    """找到 MCP 版下载器可执行文件（已解压即可，无需固定文件名）。"""
    for d in DOWNLOADER_DIRS:
        if not d.exists():
            continue
        for pattern in ("wx_video_download*.exe", "wx_channel*.exe", "*.exe"):
            for p in sorted(d.glob(pattern)):
                if "unins" in p.name.lower() or "setup" in p.name.lower():
                    continue
                return p
    return None


def _mcp_port_open(timeout: float = 2.0) -> bool:
    try:
        with _opener().open(f"http://{SERVICE_HOST}:{MCP_PORT}/", timeout=timeout):
            return True
    except Exception:
        return False


def ensure_downloader(wait_sec: int = 30) -> bool:
    """确保 MCP 下载器在跑；没跑就静默拉起（脱离进程）。

    ⚠️ 该程序启动时会调用 reg.exe 写系统代理，在沙箱中执行会被安全策略杀掉，
       表现为「进程秒退、端口连不上」。Agent 侧需要请求沙箱外执行权限。
    """
    if _mcp_port_open():
        return True

    exe = downloader_exe()
    if not exe:
        return False

    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs = {"creationflags": flags}
    else:
        kwargs = {"start_new_session": True}

    try:
        subprocess.Popen(
            [str(exe)], cwd=str(exe.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **kwargs,
        )
    except Exception:
        return False

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        time.sleep(1)
        if _mcp_port_open():
            return True
    return False


class MCPClient:
    """极简 MCP Streamable HTTP 客户端，用于驱动 wx_video_download 下载。"""

    def __init__(self, url: str = MCP_URL) -> None:
        self.url = url
        self._inited = False

    def _rpc(self, method: str, params: dict | None = None, rid: int | None = 1) -> dict:
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if rid is not None:
            body["id"] = rid
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        with _opener().open(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "ignore")
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw[:400]}

    def _ensure(self) -> None:
        if self._inited:
            return
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "weixin-favor-kb", "version": "1"}})
        # notification 不带 id
        self._rpc("notifications/initialized", {}, rid=None)
        self._inited = True

    def call(self, name: str, arguments: dict) -> dict:
        """调用工具，返回 content 里的结构化数据（尽力解析 JSON）。"""
        self._ensure()
        res = self._rpc("tools/call", {"name": name, "arguments": arguments}, rid=99)
        if "error" in res:
            raise DownloadError(f"MCP {name} 错误: {res['error']}")
        content = (res.get("result") or {}).get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"text": joined}

    def wxchannels_available(self) -> bool:
        try:
            r = self.call("get_wxchannels_status", {})
            return bool(r.get("available"))
        except Exception:
            return False

    def download_video(self, url: str, download_dir: str = "") -> dict:
        """直接下载视频号视频（需微信视频号页面已连接）。

        注意：download_wxchannels_video 只接受 url / download_dir / filename 等，
        传 wait_for_completion 会报「unknown field」——等待完成要自己轮询任务。
        """
        args = {"url": url}
        if download_dir:
            args["download_dir"] = download_dir
        return self.call("download_wxchannels_video", args)

    def download_content(self, url: str, download_dir: str = "",
                         wait: bool = True, timeout: int = 900) -> dict:
        """通用平台下载（B站/抖音/快手/微博等，无需浏览器）。"""
        args = {"url": url, "wait_for_completion": wait, "timeout_seconds": timeout}
        if download_dir:
            args["download_dir"] = download_dir
        return self.call("download_content", args)

    def tasks(self, statuses: list[int] | None = None) -> dict:
        args = {"page": 1, "page_size": 20}
        if statuses:
            args["statuses"] = statuses
        return self.call("get_download_tasks", args)
