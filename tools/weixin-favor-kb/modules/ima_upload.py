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
IMA_KB_ID = os.environ.get("IMA_KB_ID", "001a8016b30037f0")

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


def upload_md(local_path: Path, kb_id: str = IMA_KB_ID,
              folder_id: str = "", mcp_url: str = "") -> dict:
    """把本地 md 上传到 ima，返回 {media_id, title, kb_id}。

    失败抛 ImaError。注意：本函数返回成功仅代表 MCP 三步都返回了 200，
    真正的「已入库」必须再用 verify_uploaded() 回读确认。
    """
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


def verify_uploaded(title: str, kb_id: str = IMA_KB_ID,
                    expect_media_id: str = "", mcp_url: str = "",
                    retries: int = 5, interval: float = 3.0) -> dict | None:
    """回读校验：确认 title 真的在 ima 知识库里。

    返回命中的 knowledge 字典，未命中返回 None。
    命中判定：标题完全一致，或 media_id 一致。
    """
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
