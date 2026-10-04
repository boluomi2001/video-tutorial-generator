"""公众号文章通道：抓取 mp.weixin.qq.com 文章 → 保真 Markdown。

与视频通道的区别：
    视频  → 转录 + 抽帧 + 视觉分析 + AI 生成笔记（改写/加工）
    文章  → **只做原文归档，不改写一字**，仅还原标题/加粗/列表/图片等结构

实测（2026-10-04）：纯 HTTP 抓取即可，无需登录 / Cookie / 签名，无反爬拦截。

用法：
    from modules import wx_article
    info = wx_article.fetch(url)          # 抓取 + 解析
    md   = wx_article.to_markdown(info)   # 保真转 md
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 本模块只认这两种公众号域名
ARTICLE_HOSTS = ("mp.weixin.qq.com", "mp.weixin.qq.com/s")


class ArticleError(RuntimeError):
    pass


def is_article_url(source: str) -> bool:
    """是否公众号文章链接。"""
    s = (source or "").strip().lower()
    return s.startswith("http") and "mp.weixin.qq.com/s" in s


def _opener():
    # 显式禁用代理，避免本机代理导致 403/超时
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_raw(url: str, timeout: int = 30) -> str:
    """抓取文章 HTML 原文。失败抛 ArticleError。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with _opener().open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ArticleError(f"HTTP {e.code} 抓取失败: {url}") from e
    except Exception as e:
        raise ArticleError(f"抓取失败: {e}") from e

    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        raise ArticleError("无法解码页面（非 utf-8/gbk）")

    if "环境异常" in text and "js_content" not in text:
        raise ArticleError("页面被风控拦截（环境异常），请稍后重试或换网络")
    return text


def _clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return s.replace("\xa0", " ").replace("\u200b", "").strip()


def html_to_markdown(body_html: str) -> str:
    """正文 HTML → 保真 Markdown。

    只还原结构，不改写任何文字。踩过的坑都在注释里，勿随意调整顺序。
    """
    b = body_html

    # ---- 图片：注册表 + ASCII 占位符（URL 含 ")" 会截断简单正则）----
    imgs: list[str] = []

    def _img_repl(m):
        imgs.append(html.unescape(m.group(1)))
        return f"\n\n\u0001IMG{len(imgs) - 1}\u0001\n\n"

    b = re.sub(r'<img[^>]*?(?:data-src|src)="([^"]+)"[^>]*>', _img_repl, b)

    # ---- 段落边界：<p> 显式变双换行，否则段间被压成一行 ----
    b = re.sub(r"</p\s*>", "\n\n", b)
    b = re.sub(r"<p[^>]*>", "\n\n", b)

    # ---- 标题 ----
    for lvl in range(1, 7):
        b = re.sub(
            rf"<h{lvl}[^>]*>(.*?)</h{lvl}>",
            lambda m, l=lvl: f"\n\n{'#' * max(l, 2)} {_clean_text(m.group(1))}\n\n",
            b, flags=re.S,
        )

    # ---- 列表项：先打标记，保留 li 内部行内格式 ----
    b = re.sub(r"</section>|</div>|<br\s*/?>", "\n", b)
    b = re.sub(r"<li[^>]*>(.*?)</li>",
               lambda m: f"\nLISTITEM\u0002{m.group(1)}\n", b, flags=re.S)
    b = re.sub(r"</li>", "\n", b)

    # ---- 行内：strong/span 嵌套 → 必须先转 ** 再清 span，否则加粗全丢 ----
    b = re.sub(r"<strong[^>]*>(.*?)</strong>",
               lambda m: f"**{_clean_text(m.group(1))}**", b, flags=re.S)
    b = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
               lambda m: f"[{_clean_text(m.group(2))}]({m.group(1)})", b, flags=re.S)
    b = re.sub(r"</?span[^>]*>", "", b)
    b = re.sub(r"<[^>]+>", "", b)
    b = html.unescape(b)

    # ---- 行归一化 ----
    out: list[str] = []
    blank = 0
    for ln in (ln.strip() for ln in b.split("\n")):
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        if ln.startswith("LISTITEM\u0002"):
            item = ln[len("LISTITEM\u0002"):].strip()
            # 原文 li 内已带 "•" / "1." 标记 → 去重，避免 "- •"
            if item.startswith("•"):
                out.append("- " + item[1:].strip())
            elif re.match(r"^\d+[.、]\s*", item):
                out.append(re.sub(r"^(\d+)[.、]\s*", r"\1. ", item))
            else:
                out.append("- " + item)
        elif ln.startswith("•"):
            out.append("- " + ln[1:].strip())
        else:
            out.append(ln)

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    # 列表/标题前补空行
    text = re.sub(r"(?<!\n)\n(?=(?:- |\d+\. |#{2,6} ))", "\n\n", text)

    # ---- 图片回填，独立成段 ----
    text = re.sub(r"\u0001IMG(\d+)\u0001",
                  lambda m: f"\n\n![]({imgs[int(m.group(1))]})\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def fetch(url: str) -> dict:
    """抓取并解析文章，返回元信息 + 正文 md + 原始 HTML。"""
    raw = fetch_raw(url)

    def _search(*pats):
        for p in pats:
            m = re.search(p, raw, re.S)
            if m:
                return m
        return None

    m = _search(r'var msg_title\s*=\s*["\'](.+?)["\']',
                r'<h1[^>]*class="rich_media_title"[^>]*>(.*?)</h1>',
                r'<meta property="og:title" content="([^"]+)"')
    title = _clean_text(m.group(1)) if m else "未命名文章"

    m = _search(r'var nickname\s*=\s*htmlDecode\("([^"]+)"\)',
                r'var nickname\s*=\s*["\'](.+?)["\']',
                r'id="js_name"[^>]*>\s*([^<\s][^<]*?)\s*<',
                r'data-nickname="([^"]+)"')
    account = _clean_text(m.group(1)) if m else ""

    m = _search(r'property="og:article:author"\s+content="([^"]+)"',
                r'var author\s*=\s*["\'](.+?)["\']')
    author = _clean_text(m.group(1)) if m else ""

    pub = ""
    m = _search(r'var ct\s*=\s*["\'](\d+)["\']', r'"publish_time"\s*:\s*"([^"]+)"')
    if m:
        v = m.group(1)
        try:
            pub = datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pub = v

    c = re.search(
        r'<div[^>]*class="rich_media_content[^"]*"[^>]*id="js_content"[^>]*>(.*?)</div>\s*<script',
        raw, re.S)
    if not c:
        raise ArticleError("未找到正文块（js_content），页面结构可能已变")
    body_md = html_to_markdown(c.group(1))

    return {
        "url": url,
        "title": title,
        "account": account,
        "author": author,
        "publish_time": pub,
        "body_md": body_md,
        "raw_html": raw,
    }


def to_markdown(info: dict) -> str:
    """把 fetch() 结果拼成最终归档 md（含元信息头）。"""
    title = info.get("title", "未命名文章")
    meta = [f"# {title}", ""]
    line = []
    if info.get("account"):
        line.append(f'**来源**：微信公众号「{info["account"]}」')
    if info.get("author"):
        line.append(f'**作者**：{info["author"]}')
    if line:
        meta.append("｜ ".join(line))
    if info.get("publish_time"):
        meta.append(f'**发布时间**：{info["publish_time"]}')
    if info.get("url"):
        meta.append(f'**原文链接**：{info["url"]}')
    meta.append("**说明**：以下为原文归档，内容未作任何改写，仅还原标题、加粗、列表与配图等格式。")
    meta += ["", "---", ""]
    return "\n".join(meta) + info["body_md"].rstrip() + "\n"


def filename_for(title: str, max_len: int = 60) -> str:
    """安全文件名（保留中文，去掉 Windows 非法字符）。"""
    for ch in '<>:"/\\|?*\n\r\t':
        title = title.replace(ch, "")
    title = " ".join(title.split())
    return (title[:max_len].strip().rstrip(".") or "文章") + ".md"
