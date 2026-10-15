"""多平台识别与元数据层（B站 / 小红书 / 抖音 / 快手 / 微博 …）。

定位：介于「下载层」与「流水线」之间的一层薄适配。
    - 下载层（wx_download.MCPClient）只认 URL，不认平台；
    - 流水线只需要知道：这条链接属于哪个平台、下下来的产物是什么形态（视频/图文）、
      可读标题是什么。

本模块不新增任何下载依赖：全部复用已在跑的 wx_video_download MCP（端口 2022）。
非视频号 http 链接在 `download_content` 里本就是通用平台下载，这里只是补上
「产品形态判定」与「标题元数据」两件事。

用法：
    from modules import platforms
    key = platforms.detect(url)                 # "bilibili" / "xiaohongshu" / ...
    info = platforms.fetch_profile(url)         # 走 MCP，拿标题/作者/形态
    platforms.write_title_sidecar(video, info["title"])
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# 平台识别
# ---------------------------------------------------------------------------
# 键与下载器 get_platform_status 的 platform key 对齐；值为域名特征。
PLATFORM_HOSTS: dict[str, tuple[str, ...]] = {
    "bilibili": ("bilibili.com", "b23.tv", "biligame.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "kuaishou": ("kuaishou.com", "gifshow.com", "chenzhongtech.com"),
    "weibo": ("weibo.com", "weibo.cn"),
    "zhihu": ("zhihu.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("twitter.com", "x.com"),
    "instagram": ("instagram.com",),
    "cctv": ("cctv.com", "cntv.cn", "cctv.cn"),
    # 飞书云文档 / 知识库（走专门的「文档复刻」通道，见 modules/lark_doc.py）
    "lark": ("feishu.cn", "feishu.net", "larksuite.com", "larkoffice.com"),
}

# 中文名（写日志 / 归档目录用）
PLATFORM_LABELS: dict[str, str] = {
    "bilibili": "B站",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "weibo": "微博",
    "zhihu": "知乎",
    "youtube": "YouTube",
    "x": "X",
    "instagram": "Instagram",
    "cctv": "央视频",
    "wxchannels": "视频号",
    "wxmp": "公众号",
    "lark": "飞书",
    "generic": "通用",
    "local": "本地文件",
}

# 短链域名：解析前需要先跟随跳转拿真实 URL
SHORT_LINK_HOSTS: tuple[str, ...] = (
    "xhslink.cn", "xhslink.com", "v.douyin.com", "v.kuaishou.com",
    "b23.tv", "bili2233.cn", "t.cn", "url.cn",
)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts", ".m4v",
              ".avi", ".wmv", ".m2ts", ".mpg", ".mpeg", ".3gp"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
TEXT_EXTS = {".txt", ".md", ".json", ".srt", ".ass", ".vtt", ".lrc"}


def label(key: str) -> str:
    return PLATFORM_LABELS.get(key, key or "")


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/?#]+)", (url or "").strip(), re.I)
    return (m.group(1).lower().split(":")[0] if m else "")


def detect(url: str) -> str:
    """识别链接平台。本地路径返回 local，视频号返回 wxchannels，认不出返回 generic。"""
    s = (url or "").strip()
    if not s.startswith("http"):
        return "local"
    h = host_of(s)
    if not h:
        return "generic"
    # 公众号必须先判：mp.weixin.qq.com 也 endswith("weixin.qq.com")
    if h == "mp.weixin.qq.com" or h.endswith(".mp.weixin.qq.com"):
        return "wxmp"
    if "weixin.qq.com" in h or "channels.weixin.qq.com" in h:
        return "wxchannels"
    for key, hosts in PLATFORM_HOSTS.items():
        for d in hosts:
            if h == d or h.endswith("." + d):
                return key
    return "generic"


def is_short_link(url: str) -> bool:
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in SHORT_LINK_HOSTS)


def resolve_redirect(url: str, timeout: int = 10, ua: str = "") -> str:
    """跟随短链跳转，返回最终 URL；失败原样返回。

    小红书/抖音的分享短链必须展开才能拿到 xsec_token 等鉴权参数，
    直接喂短链给部分解析器会拿不到笔记详情。

    注意：小红书对桌面 UA 的短链会 302 到登录页（丢掉目标路径），
    调用方需要传移动端 UA（见 xhs_note.resolve_short）。
    """
    if not url or not url.startswith("http"):
        return url
    if not is_short_link(url):
        return url
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.geturl() or url
    except Exception:
        return url


# ---------------------------------------------------------------------------
# 标题清洗
# ---------------------------------------------------------------------------

def clean_title(raw: str, max_len: int = 40) -> str:
    """把平台返回的「标题/描述」收拾成适合做文件名的短标题。

    抖音/小红书的 title 常是整段正文（含换行、话题标签），直接当文件名会爆长。
    规则：取第一行 → 砍掉 # 话题 → 砍句末标点后的内容 → 截断。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    # 先取第一行（去掉各平台描述里的软换行）
    t = t.replace("\r", "\n").split("\n")[0]
    for sep in ("#", "｜", "|", "【", "〔"):
        if sep in t:
            t = t.split(sep)[0]
    t = re.sub(r"https?://\S+", "", t)
    t = t.strip(" \t　·-—~、,，。.!！?？:：;；")
    if len(t) > max_len:
        t = t[:max_len].rstrip(" \t　·-—、,，。")
    return t


def compose_title(account: str, title: str, max_len: int = 50) -> str:
    """作者 + 标题 的组合标题（与视频号 `author：title` 命名习惯保持一致）。"""
    a = (account or "").strip()
    t = (title or "").strip()
    if a and t and t not in a:
        out = f"{a}：{t}"
    else:
        out = t or a
    if len(out) > max_len:
        out = out[:max_len].rstrip(" \t　·-—、,，。")
    return out


def write_title_sidecar(media: Path, title: str) -> Path | None:
    """把可读标题写成 <media>.title.txt 旁路文件，供流水线命名 md。

    约定与视频号通道一致：`video.with_suffix(".title.txt")`，即替换最后一个后缀。
    例：`xxx_quality_1080x1920.mp4` → `xxx_quality_1080x1920.title.txt`。
    """
    title = (title or "").strip()
    if not title:
        return None
    try:
        p = media.with_suffix(".title.txt")
        p.write_text(title, encoding="utf-8")
        return p
    except Exception:
        return None


def read_title_sidecar(media: Path) -> str:
    p = media.with_suffix(".title.txt")
    try:
        return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# MCP fetch_content 元数据
# ---------------------------------------------------------------------------

def _pick_content(payload: dict) -> dict:
    """download_info.Content / content 两处都可能有，取更完整的一份。"""
    if not isinstance(payload, dict):
        return {}
    cands = []
    di = payload.get("download_info")
    if isinstance(di, dict):
        for k in ("Content", "content"):
            if isinstance(di.get(k), dict):
                cands.append(di[k])
    if isinstance(payload.get("content"), dict):
        cands.append(payload["content"])
    best, best_n = {}, -1
    for c in cands:
        n = sum(1 for v in c.values() if v not in (None, "", [], {}))
        if n > best_n:
            best, best_n = c, n
    return best


def _pick_account(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    di = payload.get("download_info")
    if isinstance(di, dict):
        for k in ("Account", "account"):
            if isinstance(di.get(k), dict):
                return di[k]
    return payload.get("account") or {}


def _make_client():
    """复用流水线的 MCP 客户端（懒加载，避免循环 import）。"""
    from modules.wx_download import MCPClient
    return MCPClient()


def fetch_profile(url: str, cli=None, timeout: int = 120,
                  force_refresh: bool = False) -> dict:
    """调用 MCP fetch_content 解析链接，返回规范化元数据。

    返回：{platform, title, author, kind, media_count, source_url, raw_job_id}
        kind = "video" | "images" | "unknown"
    失败抛 RuntimeError（调用方决定降级）。
    """
    cli = cli or _make_client()
    args = {"url": url, "timeout_seconds": timeout}
    if force_refresh:
        args["force_refresh"] = True
    res = cli.call("fetch_content", args)
    if not isinstance(res, dict) or res.get("text"):
        raise RuntimeError(str(res)[:200] or "fetch_content 无结构化返回")

    content = _pick_content(res)
    account = _pick_account(res)
    platform = res.get("platform") or detect(url)

    kind = "unknown"
    n = 0
    ctype = str(content.get("type") or "").lower()
    if ctype == "video":
        kind, n = "video", 1
    elif ctype in ("images", "image", "normal"):
        kind = "images"
    details = res.get("content_details") or []
    if isinstance(details, list) and details:
        kinds = {str((d.get("type") or "")).lower() for d in details if isinstance(d, dict)}
        n = len(details)
        if "video" in kinds:
            kind = "video"
        elif kinds & {"image", "images"}:
            kind = "images"
    meta = content.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if isinstance(meta, dict):
        if meta.get("content_type"):
            kind = {"video": "video", "images": "images",
                    "image": "images"}.get(str(meta["content_type"]).lower(), kind)
        if meta.get("image_count"):
            n = max(n, int(meta["image_count"]))

    return {
        "platform": platform,
        "title": clean_title(content.get("title") or content.get("description") or ""),
        "author": (account.get("nickname") or "").strip(),
        "kind": kind,
        "media_count": n,
        "source_url": content.get("source_url") or url,
        "job_id": res.get("job_id") or "",
    }


# ---------------------------------------------------------------------------
# 落盘产物检测（下载目录里新出现了什么）
# ---------------------------------------------------------------------------

_JUNK_SUFFIX = (".part", ".tmp", ".download", ".crdownload", ".!qb")
_JUNK_NAMES = {".ds_store", "thumbs.db"}


def list_media(root: Path) -> dict[str, set[str]]:
    """扫描下载目录，按视频/图片/文本分组返回绝对路径集合。"""
    out: dict[str, set[str]] = {"video": set(), "image": set(), "text": set()}
    if not root or not root.exists():
        return out
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        name = p.name.lower()
        if name in _JUNK_NAMES or name.endswith(_JUNK_SUFFIX):
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXTS:
            out["video"].add(str(p))
        elif ext in IMAGE_EXTS:
            out["image"].add(str(p))
        elif ext in TEXT_EXTS:
            out["text"].add(str(p))
    return out


def snapshot(root: Path) -> set[str]:
    """下载目录全量文件快照（用于 diff 出本次新增产物）。"""
    if not root or not root.exists():
        return set()
    files: set[str] = set()
    for p in root.rglob("*"):
        try:
            if p.is_file():
                files.add(str(p))
        except OSError:
            continue
    return files


def new_media(root: Path, before: set[str]) -> dict[str, list[Path]]:
    """相对快照，列出新增的视频/图片（按修改时间升序，老的在前）。"""
    now = list_media(root)
    out = {"video": [], "image": []}
    for kind in ("video", "image"):
        fresh = [Path(p) for p in now[kind] if p not in before]
        out[kind] = sorted(fresh, key=lambda p: p.stat().st_mtime)
    return out


def wait_stable(path: Path, checks: int = 3, interval: float = 2.0,
                min_size: int = 1024) -> bool:
    """等待文件写完（大小连续稳定）。与 wx_download._wait_stable 语义一致。"""
    last = -1
    for _ in range(checks):
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == last and size >= min_size:
            return True
        last = size
        time.sleep(interval)
    return last >= min_size


def wait_for_artifacts(root: Path, before: set[str], timeout: int = 600,
                       settle: float = 3.0, on_tick=None) -> dict[str, list[Path]]:
    """轮询等待下载产物落盘并写完。

    - 视频：等到新增视频文件且大小稳定；
    - 图片：等到至少 2 张（或 1 张 + 出现文本/元数据文件，视为图文笔记），
      且最后一张写入后 settle 秒内没有新增（避免图集下到一半就开工）。

    返回 {"video": [...], "image": [...]}，超时可能为空。
    """
    deadline = time.time() + timeout
    seen: dict[str, list[Path]] = {"video": [], "image": []}
    last_change = 0.0
    while time.time() < deadline:
        cur = new_media(root, before)
        changed = (len(cur["video"]) != len(seen["video"])
                   or len(cur["image"]) != len(seen["image"]))
        if changed:
            seen = cur
            last_change = time.time()
        if seen["video"]:
            if all(wait_stable(p, checks=2, interval=1.0) for p in seen["video"]):
                return seen
        if seen["image"] and time.time() - last_change >= settle:
            return seen
        if on_tick:
            try:
                on_tick(seen)
            except Exception:
                pass
        time.sleep(2)
    return seen
