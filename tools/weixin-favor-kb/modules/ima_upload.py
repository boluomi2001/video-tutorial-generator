"""ima 知识库上传层。

链路（三步，由 ima MCP 定义）：
    1. create_media   → 拿 media_id + COS 临时凭证
    2. PUT 到 COS     → 把文件二进制传到腾讯云
    3. add_knowledge  → 凭 media_id 入库

⚠️ 本模块只负责「上传」与「回读校验」。
   调用方必须先用 verify_uploaded() 确认知识真的进了 ima，才能删本地文件。

MCP 端点通过环境变量 IMA_MCP_URL 覆盖（默认本机 127.0.0.1:8931）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

IMA_MCP_URL = os.environ.get("IMA_MCP_URL", "").rstrip("/")
# ima 知识库 ID 属于**使用者个人**信息，不硬编码。
# 取值优先级：环境变量 IMA_KB_ID > config.yaml 的 ima.knowledge_base_id > 空。
IMA_KB_ID = os.environ.get("IMA_KB_ID", "")


def _kb_from_config() -> str:
    """没给 kb_id 时，尝试从 config.yaml 的 ima 段读。"""
    try:
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parents[1]
                              / "config.yaml").read_text(encoding="utf-8")) or {}
        return str((cfg.get("ima") or {}).get("knowledge_base_id") or "").strip()
    except Exception:
        return ""


_KB_CACHE = IMA_KB_ID


def default_kb_id() -> str:
    """当前生效的知识库 ID（可能为空 = 未配置）。"""
    global _KB_CACHE
    if not _KB_CACHE:
        _KB_CACHE = _kb_from_config()
    return _KB_CACHE


class ImaNotConfigured(RuntimeError):
    """未配置 ima 知识库 —— 明确报错，不要静默跳过或猜一个 ID。"""

# ima MIME 对照表（取自 MCP schema，拒绝猜测）
MIME_MAP = {
    "md": "text/markdown", "markdown": "text/markdown",
    "txt": "text/plain", "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv", "html": "text/html", "epub": "application/epub+zip",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "mp3": "audio/mpeg", "m4a": "audio/x-m4a",
    "wav": "audio/wav", "aac": "audio/aac", "xmind": "application/x-xmind",
}


class ImaError(RuntimeError):
    pass


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------------------
# MCP 调用（Streamable HTTP，JSON-RPC 2.0）
# ---------------------------------------------------------------------------

class _MCP:
    def __init__(self, url: str) -> None:
        self.url = url
        self._inited = False
        self._id = 1

    def _rpc(self, method: str, params: dict | None, rid: int | None) -> dict:
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if rid is not None:
            body["id"] = rid
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
        )
        with _opener().open(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "ignore")
        if not raw.strip():
            return {}
        # SSE 格式：可能带 "event: message\ndata: {...}"
        if raw.lstrip().startswith("event:") or "\ndata:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw[:400]}

    def _ensure(self) -> None:
        if self._inited:
            return
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "weixin-favor-kb", "version": "1"},
        }, rid=self._id)
        self._id += 1
        self._rpc("notifications/initialized", {}, rid=None)
        self._inited = True

    def call(self, name: str, arguments: dict) -> dict:
        self._ensure()
        res = self._rpc("tools/call", {"name": name, "arguments": arguments},
                        rid=self._id)
        self._id += 1
        if "error" in res:
            raise ImaError(f"MCP {name} 错误: {res['error']}")
        content = (res.get("result") or {}).get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"text": joined}


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def available() -> bool:
    """ima MCP 是否可用（有地址即视为可用，真正可用性由调用时报错判定）。"""
    return bool(IMA_MCP_URL)


def resolve_kb_id(kb_id: str = "") -> str:
    """确定知识库 ID；没有就明确报错（不猜、不静默）。"""
    kid = (kb_id or default_kb_id() or "").strip()
    if not kid:
        raise ImaNotConfigured(
            "未配置 ima 知识库 ID。请二选一：\n"
            "  · config.yaml 增加：\n"
            "      ima://n"
            "        knowledge_base_id: \"<你的知识库ID>\"\n"
            "  · 或设置环境变量 IMA_KB_ID=<你的知识库ID>\n"
            "知识库 ID 在 ima 客户端的知识库链接里；不打算用 ima 的话，"
            "把待上传清单当普通文件看即可，不影响出笔记。")
    return kid


def upload_md(local_path: Path, kb_id: str = "",
              folder_id: str = "", mcp_url: str = "") -> dict:
    """把本地 md 上传到 ima，返回 {media_id, title, kb_id}。

    失败抛 ImaError。注意：本函数返回成功仅代表 MCP 三步都返回了 200，
    真正的「已入库」必须再用 verify_uploaded() 回读确认。
    """
    kb_id = resolve_kb_id(kb_id)
    local_path = Path(local_path)
    if not local_path.exists():
        raise ImaError(f"文件不存在: {local_path}")
    ext = local_path.suffix.lstrip(".").lower()
    ctype = MIME_MAP.get(ext)
    if not ctype:
        raise ImaError(f"不支持的扩展名: .{ext}（ima MIME 表未收录）")

    url = mcp_url or IMA_MCP_URL
    if not url:
        raise ImaError("IMA_MCP_URL 未配置")
    mcp = _MCP(url)
    size = local_path.stat().st_size

    # 1) create_media
    created = mcp.call("create_media", {
        "knowledge_base_id": kb_id,
        "file_name": local_path.name,
        "file_ext": ext,
        "content_type": ctype,
        "file_size": size,
    })
    media_id = created.get("media_id")
    cred = created.get("cos_credential") or {}
    if not media_id or not cred:
        raise ImaError(f"create_media 失败: {str(created)[:200]}")

    # 2) 上传到 COS
    _put_cos(local_path, cred, ctype)

    # 3) add_knowledge
    add_args = {
        "knowledge_base_id": kb_id,
        "media_id": media_id,
        "duplicate_name_strategy": "DUPLICATE_NAME_STRATEGY_REPLACE",
    }
    if folder_id:
        add_args["folder_id"] = folder_id
    mcp.call("add_knowledge", add_args)

    return {"media_id": media_id, "title": local_path.name, "kb_id": kb_id}


def _put_cos(local_path: Path, cred: dict, ctype: str) -> None:
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise ImaError("缺少 cos-python-sdk-v5，请先 pip install cos-python-sdk-v5")
    cfg = CosConfig(
        Region=cred["region"],
        SecretId=cred["secret_id"],
        SecretKey=cred["secret_key"],
        Token=cred.get("token") or None,
        Scheme="https",
    )
    cli = CosS3Client(cfg)
    with open(local_path, "rb") as f:
        resp = cli.put_object(
            Bucket=cred["bucket_name"], Body=f,
            Key=cred["cos_key"], ContentType=ctype,
        )
    if not resp.get("ETag"):
        raise ImaError("COS 上传未返回 ETag")


def verify_uploaded(title: str, kb_id: str = "",
                    expect_media_id: str = "", mcp_url: str = "",
                    retries: int = 5, interval: float = 3.0) -> dict | None:
    """回读校验：确认 title 真的在 ima 知识库里。

    返回命中的 knowledge 字典，未命中返回 None。
    命中判定：标题完全一致，或 media_id 一致。
    """
    kb_id = resolve_kb_id(kb_id)
    url = mcp_url or IMA_MCP_URL
    if not url:
        return None
    mcp = _MCP(url)
    stem = title.rsplit(".", 1)[0]
    query = stem[:60] or title

    for attempt in range(retries):
        try:
            res = mcp.call("search_knowledge", {
                "knowledge_base_id": kb_id, "query": query, "cursor": "",
            })
        except Exception:
            time.sleep(interval)
            continue
        items = res.get("searched_knowledge_list") or []
        for it in items:
            k = it.get("knowledge") or {}
            k_title = (k.get("title") or "").strip()
            if expect_media_id and k.get("media_id") == expect_media_id:
                return k
            if k_title == title or (stem and stem in k_title):
                # 解析中不算成功，避免文件还没落库就删本地
                if k.get("media_state") in (2, 3) or k.get("parse_progress") == 100:
                    return k
                return k  # 已入库（解析中），视为上传成功
        time.sleep(interval)
    return None
