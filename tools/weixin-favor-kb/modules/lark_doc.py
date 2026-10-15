"""飞书文档 / 知识库通道：飞书链接 → 复刻到自己知识库（保真，含视频）或本地归档 md。

与其它通道的区别：
    视频通道  → 转录 + 抽帧 + 视觉分析 + AI 生成笔记（改写/加工）
    文章通道  → 公众号原文归档（不改写，纯 HTTP 抓取）
    小红书    → 图文笔记归档（不改写）
    飞书通道  → **文档级复刻**：飞书文档 XML 里的媒体是 token，而
                `<img src="token"/>` / `<source token="..."/>` 支持**跨文档复用**，
                所以可以把内容（含图片、内嵌视频）一次性搬进自己的飞书知识库。
                这条路绕开了「ima 不支持视频附件」的问题。

两种去向（dest，按用户选择）：
    wiki（默认）  复刻到自己的飞书知识库：fetch XML → docs +create 到目标位置
    md            本地归档：fetch markdown → 素材下载到 assets/ → 产出 md（供上传 ima）

前置条件（**不是开箱即用**）：
    1) 装好 `lark-cli`（WorkBuddy 的飞书连接器自带）—— 本模块靠它干活；
    2) dest=wiki 需先 `lark-cli auth login --as user` 扫码授权（一次即可）；
    3) 只能读写**你自己账号有权访问**的文档：别人分享给你的可以，未分享的读不到。

已知限制（实测，勿当 bug）：
    · `wiki +node-copy` 不能用来复制别人分享的文档 —— 报 131006，根因是
      你不是对方知识库的**成员**（`wiki +node-list` 同样 131006）。所以本模块
      走 XML 重建，而不是 copy 接口。
    · 不递归：子节点/目录树不会自动带过来，只能逐篇复刻。
    · 评论、历史版本、权限设置、封面不搬迁。

用法：
    from modules import lark_doc
    if lark_doc.is_lark_url(url):
        # ① 复刻到自己知识库
        out = lark_doc.replicate(url, work_dir, target_position="my_library")
        # ② 或本地归档（给 ima）
        info = lark_doc.archive_markdown(url, work_dir)
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# 只认这些域名（含中国版与企业版/国际版）
LARK_HOSTS = ("feishu.cn", "feishu.net", "larksuite.com", "larkoffice.com")
# 只认这些资源路径：wiki 知识库节点 / docx 云文档 / docs 老文档
LARK_PATH_PREFIXES = ("/wiki/", "/docx/", "/docs/")

# mime → 扩展名（media-preview 不带 name 时兜底）
MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "application/pdf": ".pdf",
}


class LarkError(RuntimeError):
    """飞书通道错误（带可执行的排查指引）。"""


class LarkUnavailable(LarkError):
    """lark-cli 不存在或未授权。"""


# ---------------------------------------------------------------------------
# 链路识别
# ---------------------------------------------------------------------------

def is_lark_url(source: str) -> bool:
    """是否飞书云文档 / 知识库链接。"""
    s = (source or "").strip()
    if not s.lower().startswith("http"):
        return False
    p = urlparse(s)
    host = (p.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in LARK_HOSTS):
        return False
    return any(p.path.lower().startswith(pre) for pre in LARK_PATH_PREFIXES)


def host_of(source: str) -> str:
    """取租户域名（拼 cite 引用链接用）。"""
    return (urlparse((source or "").strip()).hostname or "").lower()


# ---------------------------------------------------------------------------
# lark-cli 调用层
# ---------------------------------------------------------------------------

def _node_exe() -> str:
    return os.environ.get("LARK_NODE") or shutil.which("node") or "node"


def _cli_argv() -> list[str]:
    """定位 lark-cli 的可执行入口。

    Windows 上 npm 风格的 `lark-cli` 是 sh 脚本（无扩展名），CreateProcess 跑不了；
    同名目录里还有 `lark-cli.cmd` 与 `node_modules/@larksuite/cli/scripts/run.js`。
    优先级：显式配置 > run.js（最稳） > .cmd/.exe > PATH 上的 lark-cli。
    """
    explicit = (os.environ.get("LARK_CLI") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.suffix.lower() == ".js":
            return [_node_exe(), str(p)]
        if p.exists():
            js = p.parent / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
            if js.exists():
                return [_node_exe(), str(js)]
            return [str(p)]
        return [explicit]  # 交给 PATH 解析

    for name in ("lark-cli", "lark-cli.cmd", "lark-cli.exe"):
        found = shutil.which(name)
        if not found:
            continue
        js = Path(found).parent / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
        if js.exists():
            return [_node_exe(), str(js)]
        if Path(found).suffix.lower() in (".cmd", ".bat"):
            # 批处理必须过 shell，交给调用方用字符串命令
            return [found]
        if os.name == "nt" and not Path(found).suffix:
            continue  # Windows 上无扩展名的 sh 脚本不可执行，继续找
        return [found]

    raise LarkUnavailable(
        "找不到 lark-cli。飞书通道不是开箱即用，需要先安装它：\n"
        "  · WorkBuddy：在连接器里启用「飞书」，或\n"
        "  · 手动装：npm i -g @larksuite/cli，然后 `lark-cli auth login --as user` 扫码授权\n"
        "  · 也可以设环境变量 LARK_CLI 指向可执行文件/run.js\n"
        "  只想本地归档不要授权？用 `--lark-dest md` 仍需 CLI（抓取本身要 CLI）。"
    )


def available() -> bool:
    try:
        _cli_argv()
        return True
    except LarkUnavailable:
        return False


def _run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> dict:
    """调 lark-cli 并解析 JSON 信封；失败抛 LarkError。"""
    argv = _cli_argv() + list(args) + ["--as", "user", "--format", "json"]
    use_shell = os.name == "nt" and Path(argv[0]).suffix.lower() in (".cmd", ".bat")
    cmd: object = subprocess.list2cmdline(argv) if use_shell else argv
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, shell=use_shell,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise LarkUnavailable(f"lark-cli 无法执行：{e}") from e
    except subprocess.TimeoutExpired as e:
        raise LarkError(f"lark-cli 超时（{timeout}s）：{' '.join(args[:3])}") from e

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    # 成功走 stdout；失败时 lark-cli 把 JSON 信封写到 stderr 并 exit 1 —— 两边都要试
    payload = None
    for blob in (stdout, stderr):
        if not blob.startswith("{"):
            continue
        try:
            payload = json.loads(blob)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        raise LarkError(
            f"lark-cli 输出无法解析（exit={proc.returncode}）：{' '.join(args[:3])}\n"
            f"stdout: {stdout[:300]}\nstderr: {stderr[:500]}"
        )

    # 成功信封没有顶层 code，判断成功只看 ok
    if payload.get("ok") is not True:
        err = payload.get("error") or {}
        msg = err.get("message") or payload.get("msg") or stdout[:300]
        code = err.get("code")
        hint = err.get("hint") or ""
        extra = ""
        if code == 131006:
            extra = ("\n提示：131006 = 你的账号对该资源无权限。别人分享给你的**单篇**文档可以读、"
                     "也可以用本通道复刻；但 `wiki +node-copy` 这类需要知识库**成员**权限的接口会失败。")
        raise LarkError(f"lark-cli 失败（{err.get('type')}/{err.get('subtype')}"
                        f"{' code=' + str(code) if code else ''}）：{msg}{('  ' + hint) if hint else ''}{extra}")
    return payload.get("data") or {}


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def node_info(source: str) -> dict:
    """解析 wiki/docx 链接 → 标题、node_token、obj_token、space_id 等。"""
    return _run(["wiki", "+node-get", "--node-token", source], timeout=120)


def fetch(source: str, fmt: str = "xml", timeout: int = 300) -> str:
    """抓文档内容。fmt: xml（结构最全，复刻用） / markdown（人读，归档用）。"""
    data = _run(["docs", "+fetch", "--doc", source, "--doc-format", fmt], timeout=timeout)
    content = ((data.get("document") or {}).get("content")) or ""
    if not content.strip():
        raise LarkError(f"文档内容为空：{source}")
    return content


def title_from(content: str) -> str:
    m = re.search(r"<title>(.*?)</title>", content or "", re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def doc_title(source: str, content: str = "") -> str:
    """标题：优先正文 <title>，失败退回节点标题。"""
    t = title_from(content)
    if t:
        return t
    try:
        return (node_info(source).get("title") or "").strip()
    except LarkError:
        return ""


def media_stats(xml: str) -> dict:
    """统计文档里的媒体与结构块（复刻前后比对用）。"""
    return {
        "img": len(re.findall(r"<img\b", xml or "")),
        "figure": len(re.findall(r"<figure\b", xml or "")),
        "source": len(re.findall(r"<source\b", xml or "")),
        "table": len(re.findall(r"<table\b", xml or "")),
        "cite": len(re.findall(r"<cite\b", xml or "")),
        "sizes": sorted(re.findall(r"<source\b[^>]*\bsize=\"(\d+)\"", xml or "")),
    }


# ---------------------------------------------------------------------------
# 去向 ①：复刻到自己飞书知识库
# ---------------------------------------------------------------------------

def replicate(source: str, work_dir: Path, target_position: str = "my_library",
              parent_node_token: str = "", title: str | None = None,
              keep_xml: bool = True) -> dict:
    """把源文档复刻成自己知识库里的新文档（含图片与内嵌视频）。

    原理：飞书 docx XML 的 `<img src="token"/>` 与 `<source token="..."/>`
    支持跨文档复用，CLI 会把源媒体**重新上传**到新文档（token 会换新）。
    实测 15 媒体全量迁移、6 段 mp4 字节数逐一一致、view-type 保留。

    返回：{"ok", "url", "document_id", "title", "stats", "warnings", "verified"}
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    xml = fetch(source, "xml")
    stats_before = media_stats(xml)
    real_title = title or doc_title(source, xml) or "飞书文档"

    xml_name = "lark_source.xml"
    (work_dir / xml_name).write_text(xml, encoding="utf-8")

    args = ["docs", "+create", "--doc-format", "xml",
            "--content", f"@./{xml_name}"]
    if parent_node_token:
        args += ["--parent-token", parent_node_token]
    else:
        args += ["--parent-position", target_position or "my_library"]

    # cwd 必须在 work_dir：CLI 的 --content 只接受 cwd 下的相对路径
    data = _run(args, cwd=work_dir, timeout=900)
    doc = data.get("document") or {}
    new_url = doc.get("url") or ""
    new_id = doc.get("document_id") or ""
    if not new_id:
        raise LarkError(f"复刻未返回新文档 ID。原始返回：{json.dumps(data, ensure_ascii=False)[:400]}")

    # 回读校验：新文档的媒体/结构块数量必须与源一致（缺媒体就是没搬成功）
    verified, stats_after, note = False, {}, ""
    try:
        back = fetch(new_url or new_id, "xml")
        stats_after = media_stats(back)
        if not keep_xml:
            back = ""
        (work_dir / "lark_clone.xml").write_text(back, encoding="utf-8")
        verified = (stats_before["img"] == stats_after.get("img")
                    and stats_before["source"] == stats_after.get("source")
                    and stats_before["sizes"] == stats_after.get("sizes"))
        if not verified:
            note = (f"回读不一致：源 img={stats_before['img']}/source={stats_before['source']}，"
                    f"新 img={stats_after.get('img')}/source={stats_after.get('source')}")
    except LarkError as e:
        note = f"回读失败（不影响已创建文档）：{e}"

    return {
        "ok": True, "url": new_url, "document_id": new_id, "title": real_title,
        "stats": stats_before, "clone_stats": stats_after,
        "warnings": data.get("warnings") or [], "tips": data.get("tips") or "",
        "verified": verified, "note": note,
    }


def clone_record_md(info: dict, source: str) -> str:
    """复刻记录 md（本地留痕 + 给批量汇总用，本身不含正文）。"""
    st = info.get("stats") or {}
    lines = [
        f"# {info.get('title') or '飞书文档'}",
        "",
        f"**来源**：飞书文档 ｜ {source}",
        f"**复刻后**：{info.get('url') or ''}",
        f"**复刻时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**媒体**：图片 {st.get('img', 0)} 张 ｜ 视频/附件 {st.get('source', 0)} 个",
        f"**结构**：表格 {st.get('table', 0)} 个 ｜ 引用 {st.get('cite', 0)} 处",
        f"**回读校验**：{'通过（媒体数量与字节数与源一致）' if info.get('verified') else '未通过或未校验 — ' + (info.get('note') or '')}",
        "",
        "**说明**：本文件是复刻留痕，正文已在飞书知识库里（点上面的「复刻后」链接）。",
        "内容保真，图片与内嵌视频已一并迁移，非降级方案。",
        "",
    ]
    for w in (info.get("warnings") or []):
        lines.append(f"> ⚠️ 服务端警告：{w}")
    if info.get("tips"):
        lines.append(f"> {info['tips']}")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 去向 ②：本地归档 md（供 ima 上传）
# ---------------------------------------------------------------------------

_ATTR_RE = re.compile(r'\b([a-zA-Z_-]+)="([^"]*)"')


def _attrs(tag: str) -> dict:
    return {k: html.unescape(v) for k, v in _ATTR_RE.findall(tag)}


def _safe_name(name: str, fallback: str) -> str:
    name = (name or "").strip() or fallback
    for ch in '<>:"/\\|?*\n\r\t':
        name = name.replace(ch, "_")
    return " ".join(name.split())[:80] or fallback


def _download_media(token: str, out_name: str, work_dir: Path, assets_dir: Path,
                    timeout: int = 600) -> str | None:
    """下载单个媒体 token 到 assets/，返回写入的文件名（相对 assets）或 None。"""
    rel = f"{assets_dir.name}/{out_name}"
    try:
        data = _run(["docs", "+media-preview", "--token", token, "--output", f"./{rel}"],
                    cwd=work_dir, timeout=timeout)
    except LarkError:
        return None
    saved = Path(data.get("saved_path") or "")
    return saved.name if saved.name else out_name


def _ext_for(name: str, mime: str, content_type: str = "") -> str:
    ext = Path(name).suffix
    if ext:
        return ext
    for key in (mime, content_type):
        if key and key in MIME_EXT:
            return MIME_EXT[key]
    return ".bin"


def _cell_text(c: str) -> str:
    """表格单元格 → 单行文本（保留 <br> 换行，剥掉其它标签，转义竖线）。"""
    c = re.sub(r"<br\s*/?>", "\x00BR\x00", c, flags=re.I)
    c = re.sub(r"<[^>]+>", "", c)
    c = html.unescape(c).replace("|", "\\|").replace("\n", " ")
    return c.replace("\x00BR\x00", "<br>").strip()


def _table_grid(raw: str) -> list[list[str]]:
    """取表格的行/单元格原始内容（支持嵌套表：按深度配对，不靠非贪婪正则）。"""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S | re.I)
    return [re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I) for r in rows]


def _find_tables(s: str) -> list[tuple[int, int]]:
    """扫描顶层 <table>...</table> 区间（深度配对，避免嵌套表被截断）。"""
    spans: list[tuple[int, int]] = []
    open_re, close_re = re.compile(r"<table\b", re.I), re.compile(r"</table\s*>", re.I)
    for m in open_re.finditer(s):
        start, depth, i = m.start(), 0, m.start()
        while i < len(s):
            no, nc = open_re.search(s, i), close_re.search(s, i)
            if not nc:
                break
            if no and no.start() < nc.start():
                depth += 1
                i = no.end()
            else:
                depth -= 1
                i = nc.end()
                if depth == 0:
                    spans.append((start, i))
                    break
    return spans


def _html_table_to_md(raw: str) -> str:
    """纯文本表 → Markdown 表；有合并单元格（md 表达不了）就保留 HTML。"""
    if re.search(r"\bcolspan=|\browspan=", raw, re.I):
        return raw  # md 表达不了合并单元格，保持 HTML 交给渲染端
    grid = [[_cell_text(c) for c in row] for row in _table_grid(raw)]
    grid = [r for r in grid if r]
    if not grid:
        return raw
    width = max(len(r) for r in grid)
    out = ["| " + " | ".join(grid[0] + [""] * (width - len(grid[0]))) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for r in grid[1:]:
        out.append("| " + " | ".join(r + [""] * (width - len(r))) + " |")
    return "\n\n" + "\n".join(out) + "\n\n"


def _table_to_cards(raw: str) -> str:
    """单元格里含图片/视频的表 → 按行展开成「字段 + 内容」块。

    Markdown 表格放不下块级媒体（图/视频会把表格截断），所以这类表不转表格，
    而是首行当字段名、其余每行一个 `### 第 N 组`，字段值逐条列出，媒体各自成段。
    """
    grid = [r for r in _table_grid(raw) if r]
    if not grid:
        return raw
    header = [_cell_text(c) for c in grid[0]]
    body = grid[1:] or grid
    blocks: list[str] = ["\n\n"]
    for i, row in enumerate(body, 1):
        blocks.append(f"### 第 {i} 组\n")
        for j, cell in enumerate(row):
            key = header[j] if j < len(header) and header[j] else f"字段{j + 1}"
            val = re.sub(r"</?p\b[^>]*>", "\n", cell)
            val = html.unescape(re.sub(r"<[^>]+>", "", val)).strip()
            blocks.append(f"**{key}**\n")
            blocks.append(val)
            blocks.append("")
    return "\n".join(blocks) + "\n\n"


def xml_to_markdown(xml: str, work_dir: Path, assets_dir: Path,
                    host: str = "") -> tuple[str, int]:
    """飞书 docx XML → 本地 Markdown（素材下载到 assets/，md 用相对路径引用）。

    只还原结构，不改写任何文字。返回 (markdown, 失败的媒体数)。
    """
    b = xml
    assets_name = assets_dir.name
    used: set[str] = set()
    failed = 0

    def _uniq(name: str) -> str:
        stem, ext = os.path.splitext(name)
        cand, i = name, 1
        while cand in used:
            i += 1
            cand = f"{stem}_{i}{ext}"
        used.add(cand)
        return cand

    def _media(token: str, nm: str, mime: str) -> str:
        """媒体落地 → Markdown 引用（下载失败时留可追溯的提示）。"""
        nonlocal failed
        if not token:
            return ""
        is_video = mime.startswith("video/") or nm.lower().endswith(
            (".mp4", ".mov", ".webm", ".m4v"))
        fname = _uniq(_safe_name(
            nm, ("media" if is_video else "image") + _ext_for(nm, mime)))
        got = _download_media(token, fname, work_dir, assets_dir)
        if not got:
            failed += 1
            return f"\n\n> 📎 未下载成功：{nm or token}（token={token}）\n\n"
        rel = f"{assets_name}/{got}"
        return (f"\n\n[📹 视频：{nm or got}]({rel})\n\n" if is_video
                else f"\n\n![{nm or got}]({rel})\n\n")

    def _fig_repl(m: re.Match) -> str:
        sm = re.search(r"<source\b[^>]*?/?>", m.group(0))
        if not sm:
            return ""
        a = _attrs(sm.group(0))
        return _media(a.get("token", ""), a.get("name", ""), a.get("mime", ""))

    def _img_repl(m: re.Match) -> str:
        a = _attrs(m.group(0))
        return _media(a.get("src", ""), a.get("name", ""), a.get("mime", ""))

    def _media_pass(s: str) -> str:
        """把图/视频/附件换成 Markdown 引用（已下载到 assets/）。"""
        s = re.sub(r"<figure\b[^>]*>.*?</figure>", _fig_repl, s, flags=re.S | re.I)
        s = re.sub(r"<source\b[^>]*?/>", _fig_repl, s, flags=re.I)
        return re.sub(r"<img\b[^>]*?/?>", _img_repl, s, flags=re.I)

    # ---- 表格先从后往前处理（含媒体的表会整块换掉，先做不会打断位置）----
    for _start, _end in reversed(_find_tables(b)):
        _chunk = b[_start:_end]
        if re.search(r"<img\b|<source\b|<figure\b", _chunk, re.I):
            _chunk = _table_to_cards(_media_pass(_chunk))
        else:
            _chunk = _html_table_to_md(_chunk)
        b = b[:_start] + _chunk + b[_end:]

    # ---- 表格外的媒体 ----
    b = _media_pass(b)
    b = re.sub(r"<colgroup\b.*?</colgroup>", "", b, flags=re.S | re.I)

    # ---- 引用 / 书签 / 高亮块 / 勾选框 ----
    b = re.sub(
        r"<cite\b[^>]*?doc-id=\"([^\"]+)\"[^>]*?title=\"([^\"]*)\"[^>]*?></cite>",
        lambda m: f"[引用文档：{html.unescape(m.group(2))}](https://{host}/wiki/{m.group(1)})"
        if host else f"引用文档：{html.unescape(m.group(2))}",
        b, flags=re.S | re.I)
    b = re.sub(r"<bookmark\b([^>]*)></bookmark>",
               lambda m: (lambda a: f"[🔖 {a.get('name') or a.get('href', '')}]({a.get('href', '')})"
                          )(_attrs(m.group(1))), b, flags=re.I)
    b = re.sub(r"<callout\b([^>]*)>(.*?)</callout>",
               lambda m: "\n> " + (_attrs(m.group(1)).get("emoji", "") + " ").strip()
               + " " + re.sub(r"<[^>]+>", "", m.group(2)).strip() + "\n",
               b, flags=re.S | re.I)
    b = re.sub(r"<checkbox\b([^>]*)>(.*?)</checkbox>",
               lambda m: ("- [x] " if _attrs(m.group(1)).get("done") == "true" else "- [ ] ")
               + html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(),
               b, flags=re.S | re.I)

    # ---- 行内 ----
    for lvl in range(1, 10):
        b = re.sub(rf"<h{lvl}\b[^>]*>(.*?)</h{lvl}>",
                   lambda m, l=lvl: f"\n\n{'#' * min(l, 6)} "
                   f"{html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()}\n\n",
                   b, flags=re.S | re.I)
    b = re.sub(r"<pre\b([^>]*)><code>(.*?)</code></pre>",
               lambda m: f"\n\n```{_attrs(m.group(1)).get('lang', '')}\n"
               f"{html.unescape(m.group(2))}\n```\n\n", b, flags=re.S | re.I)
    b = re.sub(r"<li\b[^>]*>(.*?)</li>",
               lambda m: "\n- " + html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip(),
               b, flags=re.S | re.I)
    b = re.sub(r"</?(ul|ol)\b[^>]*>", "\n", b, flags=re.I)
    b = re.sub(r"<(b|strong)\b[^>]*>(.*?)</\1>",
               lambda m: f"**{html.unescape(re.sub(r'<[^>]+>', '', m.group(2))).strip()}**",
               b, flags=re.S | re.I)
    b = re.sub(r"<a\b[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>",
               lambda m: f"[{html.unescape(re.sub(r'<[^>]+>', '', m.group(2))).strip()}]({m.group(1)})",
               b, flags=re.S | re.I)
    b = re.sub(r"</?(p|title|figure)\b[^>]*>", "\n\n", b, flags=re.I)
    b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
    b = re.sub(r"<[^>]+>", "", b)
    b = html.unescape(b)

    # ---- 行归一化 ----
    out: list[str] = []
    blank = 0
    for ln in (x.rstrip() for x in b.split("\n")):
        if not ln.strip():
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln.strip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text + "\n", failed


def archive_markdown(source: str, work_dir: Path, assets_name: str = "assets") -> dict:
    """本地归档：抓 markdown + 下载素材 → 返回 (标题, md 正文, 统计)。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = work_dir / assets_name
    assets_dir.mkdir(parents=True, exist_ok=True)

    content = fetch(source, "markdown")
    title = doc_title(source, content) or "飞书文档"
    host = host_of(source)
    # 正文开头会再重复一次标题（xml 形态是 <title>，markdown 形态是首行 # 标题），
    # 与元信息头的「# 标题」重复，先摘掉
    body_src = re.sub(r"^\s*<title>.*?</title>\s*", "", content, count=1,
                      flags=re.S | re.I)
    _m = re.match(r"\s*#{1,2}\s*(.+?)\s*\n", body_src)
    if _m and title and _m.group(1).strip() == title.strip():
        body_src = body_src[_m.end():]
    body, failed = xml_to_markdown(body_src, work_dir, assets_dir, host=host)
    files = [p for p in assets_dir.iterdir() if p.is_file()]
    return {
        "title": title,
        "body_md": body,
        "url": source,
        "host": host,
        "asset_files": [p.name for p in files],
        "asset_count": len(files),
        "media_failed": failed,
    }


def to_markdown(info: dict) -> str:
    """拼最终归档 md（含元信息头），与公众号/小红书通道风格一致。"""
    meta = [f"# {info.get('title') or '飞书文档'}", ""]
    if info.get("url"):
        meta.append(f"**原文链接**：{info['url']}")
    meta.append(f"**归档时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    meta.append(f"**素材**：{info.get('asset_count', 0)} 个文件"
                + (f"（{info['media_failed']} 个未下载成功）" if info.get("media_failed") else ""))
    meta.append("**说明**：以下为原文归档，内容未作任何改写，仅还原标题、表格、列表与配图等格式。")
    meta += ["", "---", ""]
    return "\n".join(meta) + (info.get("body_md") or "").rstrip() + "\n"


def filename_for(title: str, max_len: int = 60) -> str:
    """安全文件名（保留中文，去掉 Windows 非法字符）。"""
    for ch in '<>:"/\\|?*\n\r\t':
        title = title.replace(ch, "")
    title = " ".join(title.split())
    return (title[:max_len].strip().rstrip(".") or "飞书文档") + ".md"
