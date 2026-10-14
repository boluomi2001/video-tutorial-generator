"""小红书通道：笔记抓取 → 保真归档（图文笔记 / 视频笔记）。

与其它两条通道的关系：
    视频号/B站/抖音视频 → 转录 + 抽帧 + 视觉分析 + Agent 写笔记（加工/复刻）
    公众号文章        → 原文归档，一字不改          （wx_article.py）
    小红书笔记        → 原文归档 + 图片本地化        （本模块）
                        其中「视频笔记」额外把视频喂给视频流水线（转录 + 抽帧）

⚠️ 前置条件（2026-10-15 实测更正 —— 之前写错了）
    **不需要登录、不需要 Cookie。** 真正的前提是链接里必须带 `xsec_token`：
      · 带 xsec_token 的分享链接（App 里「分享 → 复制链接」得到的）
        → **匿名即可解析**，图文/视频详情都能拿到（已实测）
      · 裸链接 `/explore/<id>`、浏览器地址栏直接复制的、token 过期的
        → 拿不到详情
    另外 **UA 必须移动端**：笔记详情内联在移动端页面的
    `noteData.data.noteData`；桌面端 `__INITIAL_STATE__` 目前只有 feed。

    什么时候才真的需要登录 Cookie：私密笔记、仅关注可见、以及 token 失效且
    无法重新分享的情况。此时可配 `xhs.cookie` 或跑 `scripts/login_xhs.py` 扫码。

用法：
    from modules import xhs_note
    note = xhs_note.fetch(url)                   # 一般情况：不用传 cookie
    note = xhs_note.fetch(url, cookie=...)       # 私密/受限笔记才需要
    files = xhs_note.download_images(note, dest) # 图文：下图片
    md = xhs_note.to_markdown(note, files)       # 保真归档 md
"""

from __future__ import annotations

import html
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# 移动端 UA：短链展开必须用它，桌面 UA 会被 302 到登录页（丢掉目标路径）
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")

XHS_HOSTS = ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "rednote.com")


class XhsError(RuntimeError):
    pass


class XhsUnavailable(XhsError):
    """拿不到笔记详情。

    ⚠️ 实测更正（2026-10-15）：**这不是「需要登录」**。
       真正的前提是链接里必须带 `xsec_token`（即「分享出来的链接」）：
         · 带 xsec_token 的分享链接 → **无 Cookie 也能解析**（已验证图文/视频各一条）
         · 裸链接 `/explore/<id>`（无 token） → 拿不到详情
       小红书 App 里「分享 → 复制链接」产出的就是带 token 的链接。
       只有私密笔记 / 仅关注可见等场景才真的需要登录 Cookie。
    """


# 兼容旧名字（早期误判时取的），新代码请用 XhsUnavailable
XhsNeedCookie = XhsUnavailable


# ---------------------------------------------------------------------------
# 链接判定 / 短链展开
# ---------------------------------------------------------------------------

def is_xhs_url(source: str) -> bool:
    s = (source or "").strip().lower()
    if not s.startswith("http"):
        return False
    host = re.sub(r"^https?://", "", s).split("/")[0].split(":")[0]
    return any(host == d or host.endswith("." + d) for d in XHS_HOSTS)


def extract_note_id(url: str) -> str | None:
    m = re.search(r"/(?:explore|discovery/item|item)/([0-9a-zA-Z]{16,})", url or "")
    return m.group(1) if m else None


def has_xsec_token(url: str) -> bool:
    """链接里是否带 xsec_token（= 是不是「分享出来的链接」）。

    这是能否匿名拿到笔记详情的**真正前提**，和登录无关。
    小红书 App「分享 → 复制链接」产出的链接天然带 token；
    浏览器地址栏直接复制、或别人手打的裸链接通常没有。
    """
    return bool(re.search(r"[?&]xsec_token=[^&\s]+", url or ""))


def resolve_short(url: str, cookie: str = "", timeout: int = 10) -> str:
    """展开 xhslink 短链，拿带 xsec_token 的真实 URL。

    用移动端 UA：小红书对桌面 UA 的短链会 302 到 /login 并把目标路径塞进
    redirectPath，展开反而拿不到笔记地址。展开结果若仍是登录页，退回原始 URL。
    """
    from modules.platforms import is_short_link, resolve_redirect
    if not is_short_link(url):
        return url
    final = resolve_redirect(url, timeout=timeout, ua=UA_MOBILE)
    if "/login" in final:
        # 尝试从 redirectPath 里把真实地址捞回来
        m = re.search(r"[?&]redirectPath=([^&]+)", final)
        if m:
            cand = urllib.parse.unquote(m.group(1))
            if cand.startswith("http"):
                return cand
        return url
    return final


# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------

def cookie_from_config(cfg: dict | None = None) -> str:
    """按 环境变量 > config.yaml 的 xhs.cookie 取值。"""
    env = os.environ.get("XHS_COOKIE", "").strip()
    if env:
        return env
    try:
        if cfg is None:
            import yaml
            from modules.paths import TOOL_ROOT
            cfg = yaml.safe_load((TOOL_ROOT / "config.yaml").read_text(
                encoding="utf-8")) or {}
        return str(((cfg or {}).get("xhs") or {}).get("cookie") or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 抓取 + 解析
# ---------------------------------------------------------------------------

def _opener(cookie: str = ""):
    """构造 opener。

    · 传了 cookie：显式加 Cookie 头（不挂 jar，避免出现两个 Cookie 头）
    · 没传 cookie：挂一个**进程级访客 jar** —— 服务端首次响应下发的
      `abRequestId` / `acw_tc` 等访客标识会被带上，行为更像正常浏览器，
      比每次裸请求更不容易被风控。
    """
    handlers = [urllib.request.ProxyHandler({})]
    if not cookie:
        handlers.append(urllib.request.HTTPCookieProcessor(_VISITOR_JAR))
    return urllib.request.build_opener(*handlers)


_VISITOR_JAR = http.cookiejar.CookieJar()


def fetch_raw(url: str, cookie: str = "", timeout: int = 25,
              ua: str = "") -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua or UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.xiaohongshu.com/",
        **({"Cookie": cookie} if cookie else {}),
    })
    try:
        with _opener(cookie).open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise XhsError(f"HTTP {e.code} 抓取失败: {url}") from e
    except Exception as e:
        raise XhsError(f"抓取失败: {e}") from e
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    raise XhsError("无法解码页面")


def _initial_state(page_html: str) -> dict:
    """从页面里抠出 window.__INITIAL_STATE__ 并解析。

    页面里会出现 `undefined`（JS 字面量，不是合法 JSON），必须替换掉。
    """
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*(?:;|</script>)",
                  page_html, re.S)
    if not m:
        raise XhsError("页面里没有 __INITIAL_STATE__，可能触发了风控页")
    txt = m.group(1)
    txt = re.sub(r"\bundefined\b", "null", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        raise XhsError(f"INITIAL_STATE 解析失败: {e}") from e


def _dig(note_map: dict, note_id: str | None) -> dict:
    """桌面态：noteDetailMap 只有一层，键是笔记 id；带 firstNoteId 时优先取它。"""
    if not isinstance(note_map, dict) or not note_map:
        return {}
    if note_id and note_id in note_map:
        node = note_map[note_id].get("note") or {}
        if node:
            return node
    for v in note_map.values():
        if isinstance(v, dict) and isinstance(v.get("note"), dict) and v["note"]:
            return v["note"]
    return {}


def _extract_note(state: dict, note_id: str | None) -> dict:
    """从 __INITIAL_STATE__ 里取出笔记对象。

    实测（2026-10-14）**同一个笔记两种页面形态并存**，必须都认：
      ① 移动端（UA_MOBILE）：`noteData.data.noteData` ← **带 Cookie 时唯一可靠的一路**
         （含 title/desc/user/imageList/tagList/video）
      ② 桌面端（旧版行为）：`note.noteDetailMap[<id>].note`
      ③ 异常兜底：`noteData.errorNoteData.noteData`
      ④ 预加载兜底：`noteData.normalNotePreloadData`（只有 title/desc/imagesList）

    取不到返回空 dict；调用方据此决定是报「需登录」还是「笔记不存在」。
    """
    # ① / ③ 移动端
    nd = state.get("noteData")
    if isinstance(nd, dict):
        for key in ("data", "errorNoteData"):
            holder = nd.get(key)
            if not isinstance(holder, dict):
                continue
            node = holder.get("noteData")
            if isinstance(node, dict) and node:
                return node.get("note") if isinstance(node.get("note"), dict) else node

    # ② 桌面端
    node = _dig(((state.get("note") or {}).get("noteDetailMap") or {}), note_id)
    if node:
        return node

    # ④ 预加载
    if isinstance(nd, dict):
        pre = nd.get("normalNotePreloadData")
        if isinstance(pre, dict) and pre:
            return {"title": pre.get("title") or "", "desc": pre.get("desc") or "",
                    "imageList": [{"url": u} for u in (pre.get("imagesList") or [])]}
    return {}


def _pick_image_url(item: dict) -> str:
    """从单个 imageList 元素里挑出「最大」的那张原图。

    ⚠️ 实测（2026-10-14）：infoList 的顺序是 **H5_DTL 在前、H5_PRV 在后**，
       而 H5_PRV 是带 `!style_xxx` 的**缩略图**（实测同一张图 61,959B vs 5,495B，
       差 11 倍）。所以**不能取 infoList 末位**，必须优先 H5_DTL。

    优先级：infoList[imageScene=H5_DTL] > item.url > urlDefault > urlPre
            > infoList 里非 `!style_` 的 > infoList 末位
    （图片 URL 的 `!xxx` 后缀不能去掉，去掉会 403）
    """
    infos = [i for i in (item.get("infoList") or [])
             if isinstance(i, dict) and i.get("url")]
    for i in infos:
        if str(i.get("imageScene") or "").upper() == "H5_DTL":
            return str(i["url"])
    for key in ("url", "urlDefault", "urlPre"):
        if item.get(key):
            return str(item[key])
    for i in infos:
        if "style_" not in str(i["url"]):
            return str(i["url"])
    return str(infos[-1]["url"]) if infos else ""


def _image_urls(note: dict) -> list[str]:
    """取图集原图（顺序与笔记一致）。"""
    urls: list[str] = []
    for item in (note.get("imageList") or []):
        if isinstance(item, dict):
            u = _pick_image_url(item)
            if u:
                urls.append(u)
    return urls


def _video_url(note: dict) -> str:
    """取视频笔记的直链（h264 优先）。"""
    v = note.get("video") or {}
    media = ((v.get("media") or {}).get("stream") or {})
    for codec in ("h264", "h265", "av1"):
        arr = media.get(codec) or []
        if isinstance(arr, list) and arr:
            for cand in arr:
                if isinstance(cand, dict):
                    u = cand.get("masterUrl") or cand.get("backupUrls", [None])[0]
                    if u:
                        return u
    # 兜底：originVideoKey 可拼出 CDN 直链
    key = ((v.get("consumer") or {}).get("originVideoKey")
           or (v.get("consumer") or {}).get("origin_video_key") or "")
    if key:
        return f"https://sns-video-bd.xhscdn.com/{key}"
    return ""


def _tags(note: dict) -> list[str]:
    out: list[str] = []
    for t in (note.get("tagList") or note.get("tags") or []):
        if isinstance(t, dict):
            name = t.get("name") or t.get("title") or ""
        else:
            name = str(t)
        name = str(name).strip()
        if name:
            out.append(name if name.startswith("#") else f"#{name}")
    return out


def fetch(url: str, cookie: str = "", timeout: int = 25) -> dict:
    """抓取并解析一条小红书笔记。

    返回：{url, note_id, kind, title, desc, author, author_id, publish_time,
           tags, images, video_url, source}
        kind = "video" | "images"
    失败抛 XhsUnavailable / XhsError。

    **Cookie 是可选的**：只要链接带 `xsec_token`，匿名就能拿到图文/视频详情
    （2026-10-15 实测）。cookie 只在私密笔记、仅关注可见等场景才需要。
    """
    url = resolve_short(url, cookie=cookie)
    note_id = extract_note_id(url)

    note: dict = {}
    last_err: Exception | None = None
    # 移动端 UA 优先：笔记详情内联在移动端页面的 `noteData.data.noteData`，
    # 桌面端 `__INITIAL_STATE__` 目前只有 feed（实测）。
    for ua in (UA_MOBILE, UA):
        try:
            state = _initial_state(fetch_raw(url, cookie=cookie, timeout=timeout,
                                             ua=ua))
        except XhsError as e:
            last_err = e
            continue
        note = _extract_note(state, note_id)
        if note:
            break

    if not note:
        if last_err is not None:
            raise last_err
        if not has_xsec_token(url):
            raise XhsUnavailable(
                "链接里没有 xsec_token，拿不到笔记详情。\n"
                "请在**小红书 App** 里打开该笔记 →「分享」→「复制链接」，"
                "用复制出来的链接重试（浏览器地址栏直接复制或手打的裸链接通常不带 token）。")
        raise XhsUnavailable(
            "拿不到笔记详情：可能是笔记已删除 / 被设为私密 / 仅关注可见，"
            "或链接里的 xsec_token 已失效。\n"
            "先在 App 里重新「分享 → 复制链接」；仍失败再考虑配 xhs.cookie 或跑 "
            "login_xhs.cmd 扫码（私密内容才需要登录态）。")

    user = note.get("user") or {}
    imgs = _image_urls(note)
    vurl = _video_url(note)
    ntype = str(note.get("type") or "").lower()
    if vurl or ntype == "video":
        kind = "video"
    elif imgs:
        kind = "images"
    else:
        kind = "unknown"

    ts = note.get("time") or note.get("lastUpdateTime") or 0
    pub = ""
    try:
        if ts:
            pub = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pub = str(ts)

    return {
        "url": note.get("noteUrl") or url,
        "note_id": note_id or note.get("noteId") or "",
        "kind": kind,
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
        "author": (user.get("nickname") or user.get("nickName")
                   or user.get("name") or "").strip(),
        "author_id": user.get("userId") or user.get("id") or "",
        "publish_time": pub,
        "tags": _tags(note),
        "images": imgs,
        "video_url": vurl,
        "source": "xhs_web",
    }


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def _download(url: str, target: Path, cookie: str = "", timeout: int = 120) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.xiaohongshu.com/",
        **({"Cookie": cookie} if cookie else {}),
    })
    with _opener().open(req, timeout=timeout) as resp, open(target, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if target.stat().st_size < 512:
        raise XhsError(f"图片下载异常（<512B）: {url[:80]}")
    return target


def download_images(note: dict, dest_dir: Path, cookie: str = "") -> list[Path]:
    """把图集下载到 dest_dir，文件名 `note_01.jpg` 递增（保持原顺序）。

    ⚠️ URL 取 `images` 或 `remote_images`：
       `fetch()` 返回的是 `images`，而通道内部规范化后的笔记用的是
       `remote_images`（避免和「已落盘的本地图片路径」混淆）。
       两个都认，省得调用方来回转换。

    个别图失败不中断（返回成功的那部分），但会把失败数打到 stderr 便于排查。
    """
    # 兼容两种键名；值可能是 str（URL）也可能是 Path（已落盘）
    raw = note.get("images")
    if not raw:
        raw = note.get("remote_images") or []
    urls = [str(u) for u in raw if str(u).startswith("http")]
    if not urls:
        return []

    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    failed = 0
    for i, u in enumerate(urls, 1):
        ext = Path(urllib.parse.urlparse(u).path).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            ext = ".jpg"
        target = dest_dir / f"note_{i:02d}{ext}"
        try:
            out.append(_download(u, target, cookie=cookie))
        except Exception as e:
            failed += 1
            print(f"[xhs] 配图下载失败 {i}/{len(urls)}: {type(e).__name__} {str(e)[:80]}")
    if failed and not out:
        raise XhsError(f"{failed} 张配图全部下载失败（链接可能已过期，重跑解析即可）")
    return out


def download_video(note: dict, dest_dir: Path, cookie: str = "") -> Path:
    """视频笔记：下载视频文件。"""
    u = note.get("video_url")
    if not u:
        raise XhsError("这条笔记没有视频直链")
    dest_dir.mkdir(parents=True, exist_ok=True)
    return _download(u, dest_dir / "note_video.mp4", cookie=cookie, timeout=300)


# ---------------------------------------------------------------------------
# 归档 Markdown（保真，不改写一字）
# ---------------------------------------------------------------------------

def to_markdown(note: dict, image_files: list[Path] | None = None) -> str:
    """拼出归档 md：元信息头 + 原文正文 + 有序配图 + 标签。

    与公众号文章通道同一条原则：**只还原结构，不改写任何文字**。
    图片统一用 `![](相对文件名)` 引用（图片与 md 同目录），保证离线可读。
    """
    title = note.get("title") or (note.get("desc") or "").split("\n")[0][:30] or "小红书笔记"
    lines = [f"# {title}", ""]

    meta: list[str] = []
    if note.get("author"):
        meta.append(f'**作者**：小红书 @{note["author"]}')
    if note.get("publish_time"):
        meta.append(f'**发布时间**：{note["publish_time"]}')
    if meta:
        lines.append("｜ ".join(meta))
    if note.get("url"):
        lines.append(f'**原文链接**：{note["url"]}')
    kind_cn = "视频笔记" if note.get("kind") == "video" else "图文笔记"
    lines.append(f"**类型**：{kind_cn}")
    lines.append("**说明**：以下为原文归档，文字未作任何改写；配图已本地化保存。")
    lines += ["", "---", ""]

    desc = (note.get("desc") or "").strip()
    if desc:
        lines += [desc, ""]

    if image_files:
        lines += ["## 配图", ""]
        for p in image_files:
            # 保留调用方给的相对路径（如 images/note_01.jpg）——
            # md 在同目录、图在 images/ 子目录，只取 .name 会链接失效
            rel = p.as_posix() if isinstance(p, Path) else str(p)
            lines.append(f"![{Path(rel).stem}]({rel})")
            lines.append("")

    tags = note.get("tags") or []
    if tags:
        lines += ["## 标签", "", "　".join(tags), ""]

    return "\n".join(lines).rstrip() + "\n"


def filename_for(title: str, max_len: int = 60) -> str:
    for ch in '<>:"/\\|?*\n\r\t':
        title = title.replace(ch, "")
    title = " ".join(title.split())
    return (title[:max_len].strip().rstrip(".") or "小红书笔记") + ".md"
