"""一键视频分析流水线：给链接或文件，直接产出教学笔记。

用法:
    python auto_run.py "<视频号链接 / 公众号文章链接 / 本地文件路径>" [--keep] [--no-publish] [--wait-manual 30]

流程:
    视频号/文件 → 下载 → 转录 → 抽帧 → OCR → 分析 → 三形态笔记 → 清理
    公众号文章  → 抓取 → 保真转 Markdown（原文归档，不改写）→ 清理
进度写入 <run_dir>/progress.json，供外部轮询。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml
from loguru import logger

from modules import wx_article, wx_download
from modules.analyzer import VIDEO_TYPE_PROJECT, ContentAnalyzer
from modules.audio import extract_audio
from modules.frames import extract_keyframes, resolve_ffmpeg
from modules.ocr import OCRProcessor
from modules.transcribe import Transcriber

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
OUTPUT = ROOT / "output"
PROJECT_ROOT = ROOT.parent.parent
PUBLISH_ROOT = PROJECT_ROOT / "outputs" / "公众号视频项目"
# 公众号文章归档的 ima 目标文件夹（知识分享类 → 功能提升教学）
ARTICLE_FOLDER_HINT = "功能提升教学"

CONFIG_DEFAULT = {
    "whisper": {"model_size": "medium", "fallback_model": "small",
                "max_duration_s": 300, "device": "cpu", "compute_type": "int8",
                "language": "zh"},
    "llm": {"api_key": "", "base_url": "https://api.siliconflow.cn/v1",
            "model": "Qwen/Qwen3-VL-32B-Instruct"},
    "ocr": {"confidence_threshold": 0.5},
}


def log(msg: str) -> None:
    print(f"[auto] {msg}", flush=True)


def sanitize(name: str, max_len: int = 40) -> str:
    for c in '<>:"/\\|?*#\n\r\t':
        name = name.replace(c, "")
    return " ".join(name.split())[:max_len].strip().rstrip(".") or "video"


class Progress:
    """进度文件，供外部轮询。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {
            "stage": "init", "percent": 0, "message": "",
            "done": False, "error": None, "notes": [], "run_dir": str(path.parent),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.write()

    def update(self, stage: str, percent: int, message: str = "") -> None:
        self.data.update({
            "stage": stage, "percent": percent, "message": message,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        log(f"{percent}% [{stage}] {message}")
        self.write()

    def finish(self, notes: list[str]) -> None:
        self.data.update({
            "stage": "done", "percent": 100, "done": True,
            "notes": notes, "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.write()

    def fail(self, error: str) -> None:
        self.data.update({
            "stage": "error", "done": True, "error": error,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        log(f"FAILED: {error}")
        self.write()

    def write(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULT)
    try:
        user = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    except Exception as e:
        log(f"配置加载失败，使用默认值: {e}")
    return cfg


def _sph_worker_try_download(source: str, progress: Progress) -> Path | None:
    """用已部署的 Cloudflare Worker 云端解析+下载视频号视频。

    全程不依赖微信登录态，也不依赖本地 wx_channel 进程。
    Worker 地址取环境变量 SPH_WORKER_URL，或 wx_channel/config.yaml 的
    cloudflare.sphhostname。未配置时静默返回 None，回落后续通道。
    """
    try:
        cli = wx_download.SphWorkerClient()
    except Exception:
        return None
    if not cli.available():
        return None

    try:
        progress.update("download", 5, "云端解析中（Worker）")
        target = cli.download(source, DOWNLOADS)
        if target and target.exists() and target.stat().st_size > 1024:
            # 顺带把可读标题（作者+描述）存成旁路文件，供 run_stages 命名 md
            try:
                mt = cli.meta_title(source)
                if mt:
                    target.with_suffix(".title.txt").write_text(mt, encoding="utf-8")
            except Exception:
                pass
            progress.update("download", 20, f"云端解析完成 {target.name[:40]}")
            return target
        return None
    except Exception as e:
        progress.update("download", 4, f"云端解析失败（{str(e)[:60]}），转下一通道")
        return None


def resolve_title(video: Path, explicit: str = "") -> str:
    """确定用于命名最终 md 的标题。

    优先级：显式传入 > 下载时留下的 <stem>.title.txt > 文件名去扩展名。
    这样视频号下载不再用 `AWEUh1CSE0` 这种无意义 ID 命名。
    """
    if explicit:
        return explicit
    sidecar = video.with_suffix(".title.txt")
    if sidecar.exists():
        try:
            t = sidecar.read_text(encoding="utf-8").strip()
            if t:
                return t
        except Exception:
            pass
    return video.stem.rsplit("_", 1)[0] if "_" in video.stem else video.stem


def _mcp_try_download(source: str, progress: Progress) -> Path | None:
    """尝试用新版下载器的 MCP 直接下载，成功返回文件路径，失败返回 None。"""
    wx_download.ensure_downloader(wait_sec=25)  # 没在跑就静默拉起
    try:
        cli = wx_download.MCPClient()
    except Exception as e:
        progress.update("download", 4, f"MCP 不可用（{str(e)[:60]}），转常规流程")
        return None

    before = {str(p) for p in DOWNLOADS.rglob("*.mp4")} if DOWNLOADS.exists() else set()

    try:
        if wx_download.is_sph_url(source):
            if not cli.wxchannels_available():
                progress.update(
                    "download", 4,
                    "微信视频号页面未连接，请在微信里打开视频号后重试（随后自动接管）")
                return None
            progress.update("download", 5, "MCP 下载视频号视频")
            res = cli.download_video(source, download_dir=str(DOWNLOADS))
        else:
            progress.update("download", 5, "MCP 解析并下载")
            res = cli.download_content(source, download_dir=str(DOWNLOADS))
    except Exception as e:
        progress.update("download", 4, f"MCP 下载失败（{str(e)[:60]}），转常规流程")
        return None

    # 等待新文件落盘
    deadline = time.time() + 600
    while time.time() < deadline:
        now = {str(p) for p in DOWNLOADS.rglob("*.mp4")} if DOWNLOADS.exists() else set()
        new = now - before
        if new:
            newest = max(new, key=lambda p: Path(p).stat().st_mtime)
            if wx_download._wait_stable(Path(newest)):
                progress.update("download", 20, f"MCP 下载完成 {Path(newest).name[:40]}")
                return Path(newest)
        time.sleep(3)

    progress.update("download", 4, "MCP 已提交但未检测到文件，转常规流程")
    return None


def resolve_source(source: str, run_dir: Path, progress: Progress,
                   wait_manual: int) -> tuple[Path, bool]:
    """返回 (视频路径, 是否为本次下载的临时文件)。"""
    p = Path(source)
    if p.exists() and p.is_file():
        progress.update("download", 5, f"使用本地文件 {p.name}")
        return p, False

    if not source.startswith("http"):
        raise RuntimeError(f"无法识别的输入: {source}")

    # ① 视频号链接：优先走云端 Worker 解析（免登录态、免本地进程、全自动）
    if wx_download.is_sph_url(source):
        got = _sph_worker_try_download(source, progress)
        if got is not None:
            return got, True

    # ② 优先走新版下载器的 MCP（视频号需微信页面已连接，其他平台直接可用）
    got = _mcp_try_download(source, progress)
    if got is not None:
        return got, True

    if not wx_download.is_sph_url(source):
        progress.update("download", 5, "直链下载中")
        target = wx_download.fetch_direct(source, DOWNLOADS)
        return target, True

    # 视频号链接
    progress.update("download", 3, "检查 wx_channel 服务")
    st = wx_download.ensure_service()
    if st is None:
        raise RuntimeError(
            "wx_channel 服务未运行且无法拉起。请手动启动 tools/wx_channel/wx_channel.exe"
        )
    log(f"服务状态: clients={st.get('clients')} ready={st.get('ready_clients')}")

    try:
        info = wx_download.parse_sph(source)
        progress.update("download", 8, "解析成功，提交下载队列")
        wx_download.submit_download(info)
    except Exception as e:
        log(f"API 解析不可用（{e}），转入等待手动下载")
        progress.update(
            "download", 6,
            f"请在 wx_channel 中打开该链接并下载（等待最多 {wait_manual} 分钟）",
        )

    def on_wait(waited: int, total: int) -> None:
        progress.update(
            "download", min(20, 6 + int(waited / max(total, 1) * 12)),
            f"等待下载完成… 已等待 {waited}s",
        )

    got = wx_download.wait_for_manual_download(timeout_sec=wait_manual * 60,
                                               on_progress=on_wait)
    if got is None:
        raise RuntimeError("等待手动下载超时")
    progress.update("download", 20, f"已获取视频 {got.name}")
    return got, True


def run_stages(video: Path, is_temp: bool, cfg: dict, run_dir: Path,
               progress: Progress, keep: bool) -> dict:
    transcripts_dir = run_dir / "transcripts"
    frames_dir = run_dir / "frames"
    notes_dir = run_dir / "notes"
    for d in (transcripts_dir, frames_dir, notes_dir):
        d.mkdir(parents=True, exist_ok=True)

    # ---- 转录 ----
    audio_path = run_dir / "audio.wav"
    progress.update("transcribe", 22, "提取音频")
    extract_audio(str(video), str(audio_path))

    progress.update("transcribe", 25, "Whisper 转录中（长视频需 20-30 分钟）")
    wcfg = cfg["whisper"]
    ffmpeg_bin = resolve_ffmpeg()
    transcriber = Transcriber(
        model_size=wcfg.get("model_size", "medium"),
        fallback_model=wcfg.get("fallback_model", "small"),
        max_duration_s=wcfg.get("max_duration_s", 300),
        device=wcfg.get("device", "cpu"),
        compute_type=wcfg.get("compute_type", "int8"),
        ffmpeg_path=ffmpeg_bin,
    )
    t0 = time.time()
    result = transcriber.transcribe(str(audio_path))
    transcript = result.get("text", "")
    segments = result.get("segments", [])
    log(f"转录完成 {len(transcript)} 字，用时 {time.time()-t0:.0f}s")

    stem = sanitize(video.stem)
    (transcripts_dir / f"{stem}.txt").write_text(transcript, encoding="utf-8")
    (transcripts_dir / f"{stem}_segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- 抽帧 ----
    progress.update("frames", 45, "分桶择优抽帧")
    frame_paths = extract_keyframes(str(video), str(frames_dir))
    frames_meta = {}
    meta_file = frames_dir / "frames_meta.json"
    if meta_file.exists():
        frames_meta = json.loads(meta_file.read_text(encoding="utf-8"))
    log(f"关键帧 {len(frame_paths)} 张")
    if not frame_paths:
        raise RuntimeError("关键帧提取失败")

    # ---- OCR（带时间戳） ----
    progress.update("ocr", 55, "画面文字识别")
    ocr = OCRProcessor(confidence_threshold=cfg["ocr"].get("confidence_threshold", 0.5))
    ocr_texts = ocr.batch_extract(frame_paths)
    ts_map = {f["file"]: f["timestamp"] for f in frames_meta.get("frames", [])}
    ocr_items: list[tuple[float, str]] = []
    for p, txt in zip(frame_paths, ocr_texts):
        if not txt:
            continue
        ts = ts_map.get(Path(p).name, 0.0)
        ocr_items.append((float(ts), txt.strip()))

    # ---- 分析 ----
    progress.update("analyze", 60, "视觉理解 + 事实抽取")
    lcfg = cfg["llm"]
    analyzer = ContentAnalyzer(
        api_key=lcfg.get("api_key", ""),
        base_url=lcfg.get("base_url", ""),
        model=lcfg.get("model", "Qwen/Qwen3-VL-32B-Instruct"),
        fast_model=lcfg.get("fast_model", ""),
    )
    title = resolve_title(video)
    result_full = analyzer.run_full(
        transcript=transcript,
        segments=segments,
        frames=frame_paths,
        frames_meta=frames_meta,
        ocr_items=ocr_items,
        title=title,
        duration=float(frames_meta.get("duration", 0) or 0),
        output_dir=str(notes_dir),
    )
    progress.update("analyze", 85,
                    f"分析完成 类型={result_full['video_type']} "
                    f"成本=¥{result_full['cost']['cost_cny']}")

    # ---- 输出三形态 ----
    progress.update("write", 90, "写入笔记")
    written = write_outputs(notes_dir, result_full, title,
                            transcript=transcript, segments=segments)

    # ---- 清理 ----
    if not keep:
        progress.update("cleanup", 95, "清理中间文件")
        cleanup(video if is_temp else None, run_dir,
                files=[audio_path], dirs=[frames_dir])

    return {"result": result_full, "notes": written, "title": title}


def _format_transcript(transcript: str, segments: list[dict] | None) -> list[str]:
    """把音频文案渲染成 md 段落。

    有带时间戳的分段时，输出时间轴列表（便于对照作者原话）；
    否则退化为整段纯文本。均为空则返回空列表（调用方跳过该段）。
    """
    transcript = (transcript or "").strip()
    segs = segments or []
    timed = [s for s in segs if str(s.get("text", "")).strip()]
    if not transcript and not timed:
        return []

    lines: list[str] = ["## 音频文案", ""]

    if timed:
        lines.append("> 以下为视频原声转录，保留说话人原话与语气，便于理解作者的完整思路。")
        lines.append("")
        for s in timed:
            try:
                st = float(s.get("start", 0) or 0)
                ts = f"{int(st) // 60:02d}:{int(st) % 60:02d}"
            except Exception:
                ts = ""
            txt = str(s.get("text", "")).strip()
            lines.append(f"- `{ts}` {txt}" if ts else f"- {txt}")
        lines.append("")
    elif transcript:
        lines.append("> 以下为视频原声转录，便于理解作者的完整思路。")
        lines.append("")
        lines.append(transcript)
        lines.append("")

    return lines


def merge_notes(notes_dir: Path, res: dict, title: str,
                transcript: str = "", segments: list[dict] | None = None) -> Path:
    """把 tutorial / brief / checklist 三份笔记整合成一份最终 md。

    结构：标题+元信息 → 速览 → 执行清单 → 详细教程正文 → 音频文案。
    已存在的内容块不会重复拼接（例如 checklist 为空时直接省略该段）。
    """
    out: list[str] = []

    # ---- 头部：标题 + 元信息 ----
    vtype = res.get("video_type", "")
    duration = res.get("duration", 0)
    try:
        dur = f"{int(float(duration)) // 60:02d}:{int(float(duration)) % 60:02d}"
    except Exception:
        dur = str(duration)
    out += [
        f"# {title}", "",
        f"> 类型：{vtype} ｜ 时长：{dur} ｜ 章节：{len(res.get('chapters', []))} "
        f"｜ 帧数：{res.get('frames_used', 0)} "
        f"｜ 成本：¥{res.get('cost', {}).get('cost_cny', 0)}", "",
    ]

    # ---- 速览 ----
    brief = res.get("brief") or {}
    if brief.get("one_line") or brief.get("bullets"):
        out += ["## 速览", ""]
        if brief.get("one_line"):
            out += [f"**一句话**：{brief['one_line']}", ""]
        for b in brief.get("bullets", []):
            out.append(f"- {b}")
        if brief.get("who"):
            out += ["", f"**适合谁**：{brief['who']}"]
        out.append("")

    # ---- 执行清单 ----
    items = (res.get("checklist") or {}).get("items", [])
    if items:
        out += ["## 执行清单", ""]
        for i, it in enumerate(items, 1):
            step = it.get("step", "")
            cmd = it.get("command", "")
            out.append(f"- [ ] {i}. {step}")
            if cmd:
                out.append(f"  ```bash\n  {cmd}\n  ```")
        out.append("")

    # ---- 详细教程正文 ----
    tutorial = (res.get("tutorial") or "").strip()
    if tutorial:
        # 去掉 tutorial 自身的 H1 标题行（避免与整合稿重复）
        body_lines = tutorial.splitlines()
        while body_lines and (not body_lines[0].strip()
                              or body_lines[0].startswith("# ")
                              or body_lines[0].startswith("> 类型：")):
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()
        if body:
            # 正文内部标题整体降一级（## → ###），使其嵌入「详细教程」之下
            demoted: list[str] = []
            for ln in body.splitlines():
                if ln.startswith("## "):
                    demoted.append("#" + ln)
                elif ln.startswith("### "):
                    demoted.append("#" + ln)
                else:
                    demoted.append(ln)
            out += ["---", "", "## 详细教程", "", "\n".join(demoted), ""]

    # ---- 音频文案（原始口播转录，帮助理解作者原意）----
    tlines = _format_transcript(transcript, segments)
    if tlines:
        out += ["---", ""]
        out += tlines

    final = notes_dir / f"{title}.md"
    final.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return final


def write_outputs(notes_dir: Path, res: dict, title: str,
                  transcript: str = "", segments: list[dict] | None = None) -> list[str]:
    written: list[str] = []

    tutorial = notes_dir / "tutorial.md"
    tutorial.write_text(res["tutorial"], encoding="utf-8")
    written.append(str(tutorial))

    brief = res.get("brief") or {}
    lines = [f"# {title} · 速览", "",
             f"> 类型：{res['video_type']} ｜ 时长：{res['duration']}s ｜ "
             f"帧数：{res['frames_used']} ｜ 成本：¥{res['cost']['cost_cny']}", "",
             f"**一句话**：{brief.get('one_line', '')}", ""]
    for b in brief.get("bullets", []):
        lines.append(f"- {b}")
    if brief.get("who"):
        lines += ["", f"**适合谁**：{brief['who']}"]
    p = notes_dir / "brief.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    written.append(str(p))

    items = (res.get("checklist") or {}).get("items", [])
    cl = [f"# {title} · 执行清单", ""]
    for i, it in enumerate(items, 1):
        step = it.get("step", "")
        cmd = it.get("command", "")
        cl.append(f"- [ ] {i}. {step}")
        if cmd:
            cl.append(f"  ```bash\n  {cmd}\n  ```")
    (notes_dir / "checklist.md").write_text("\n".join(cl), encoding="utf-8")
    written.append(str(notes_dir / "checklist.md"))

    # ---- 整合成最终单文件 md（三段合一 + 音频文案）----
    final = merge_notes(notes_dir, res, sanitize(title, 60),
                        transcript=transcript, segments=segments)
    written.append(str(final))

    (notes_dir / "raw.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return written


def write_ima_pending(notes_dir: Path, final_md: Path, res: dict,
                      title: str) -> Path:
    """写一份待上传清单，供 Agent 用 ima MCP 上传 + 校验 + 删本地。

    流水线自身不能调 ima MCP（该 MCP 只注入 Agent 会话，不是本地服务），
    因此产出此清单，由 Agent 按 SKILL.md 的 SOP 执行。
    """
    sub = "项目复刻教学" if res.get("video_type") == VIDEO_TYPE_PROJECT else "功能提升教学"
    manifest = {
        "final_md": str(final_md),
        "title": final_md.name,
        "knowledge_base_id": "001a8016b30037f0",
        "folder_hint": sub,
        "delete_local_after_verify": True,
        "notes_dir": str(notes_dir),
        "intermediate": [
            str(notes_dir / "tutorial.md"),
            str(notes_dir / "brief.md"),
            str(notes_dir / "checklist.md"),
        ],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = notes_dir / ".ima_pending.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def publish(res: dict, title: str, notes: list[str]) -> list[str]:
    """把笔记同步到项目 outputs 目录。"""
    if res.get("source_type") == "article":
        # 文章归档：目录单独归类，文件名原样（标题已在文件名里）
        dest_dir = PROJECT_ROOT / "outputs" / "公众号文章"
        dest_dir.mkdir(parents=True, exist_ok=True)
        out: list[str] = []
        for src in notes:
            p = Path(src)
            if p.name == "raw.json":
                continue
            target = dest_dir / p.name
            shutil.copy2(p, target)
            out.append(str(target))
        return out

    sub = "项目复刻教学" if res["video_type"] == VIDEO_TYPE_PROJECT else "功能提升教学"
    dest_dir = PUBLISH_ROOT / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = sanitize(title, 60)
    out = []
    for src in notes:
        p = Path(src)
        if p.name == "raw.json":
            continue
        target = dest_dir / f"{safe}_{p.name}"
        shutil.copy2(p, target)
        out.append(str(target))
    return out


def cleanup(video: Path | None, run_dir: Path, files: list[Path], dirs: list[Path]) -> None:
    for f in files:
        try:
            if f.exists():
                f.unlink()
        except Exception as e:
            log(f"删除失败 {f}: {e}")
    for d in dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception as e:
            log(f"删除目录失败 {d}: {e}")
    for junk in (run_dir / "_candidates",):
        shutil.rmtree(junk, ignore_errors=True)
    if video and video.exists() and video.parent == DOWNLOADS:
        try:
            video.unlink()
            log(f"已删除源视频 {video.name}")
        except Exception as e:
            log(f"删除源视频失败: {e}")
        # 一并清掉下载时留下的标题旁路文件
        sidecar = video.with_suffix(".title.txt")
        try:
            if sidecar.exists():
                sidecar.unlink()
        except Exception:
            pass


def run_article(url: str, run_dir: Path, progress: Progress) -> dict:
    """公众号文章通道：抓取 → 保真转 Markdown（原文归档，不改写）。

    与视频通道不同：不做转录/抽帧/分析，正文即最终交付物。
    """
    notes_dir = run_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    progress.update("fetch", 15, "抓取公众号文章")
    info = wx_article.fetch(url)
    log(f"文章：{info['title'][:60]}（{info['account']}）")

    progress.update("convert", 70, "保真转换 Markdown")
    md = wx_article.to_markdown(info)

    final = notes_dir / wx_article.filename_for(info["title"])
    final.write_text(md, encoding="utf-8")

    # 顺带存原始 HTML，便于复核/重转（中间产物）
    try:
        (run_dir / "article_raw.html").write_text(info["raw_html"], encoding="utf-8")
    except Exception:
        pass

    res = {
        "video_type": "公众号文章",
        "source_type": "article",
        "title": info["title"],
        "account": info["account"],
        "author": info["author"],
        "publish_time": info["publish_time"],
        "url": url,
        "duration": 0,
        "chapters": [],
        "frames_used": 0,
        "cost": {"cost_cny": 0},
        "elapsed_sec": 0,
    }
    progress.update("write", 90, "写入归档 md")
    log(f"归档完成：{final}")
    return {"result": res, "notes": [str(final)], "title": info["title"]}


def write_article_pending(notes_dir: Path, final_md: Path, res: dict) -> Path:
    """公众号文章的 ima 待上传清单（无正文改写，folder 归入项目复刻教学）。"""
    manifest = {
        "final_md": str(final_md),
        "title": final_md.name,
        "knowledge_base_id": "001a8016b30037f0",
        "folder_hint": ARTICLE_FOLDER_HINT,
        "source_type": "article",
        "source_url": res.get("url", ""),
        "account": res.get("account", ""),
        "author": res.get("author", ""),
        "delete_local_after_verify": True,
        "notes_dir": str(notes_dir),
        "intermediate": [str(notes_dir.parent / "article_raw.html")],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = notes_dir / ".ima_pending.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="视频号链接 / 公众号文章链接 / 直链 / 本地文件路径")
    ap.add_argument("--keep", action="store_true", help="保留视频与中间文件")
    ap.add_argument("--no-publish", action="store_true", help="不复制到项目 outputs 目录")
    ap.add_argument("--wait-manual", type=int, default=30, help="等待手动下载的分钟数")
    args = ap.parse_args()

    cfg = load_config()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    is_article = wx_article.is_article_url(args.source)
    if is_article:
        name = "wechat_article"
    else:
        name = sanitize(Path(args.source).stem if not args.source.startswith("http")
                        else "sph_video", 30)
    run_dir = OUTPUT / f"{ts}_{name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_run_id.txt").write_text(f"{ts}_{name}", encoding="utf-8")

    progress = Progress(run_dir / "progress.json")
    video: Path | None = None
    is_temp = False

    try:
        # ---------- 公众号文章通道 ----------
        if is_article:
            progress.update("init", 2, f"公众号文章 {args.source[:80]}")
            out = run_article(args.source, run_dir, progress)
            res = out["result"]
            notes = out["notes"]
            if not args.no_publish:
                notes += publish(res, out["title"], notes)
            final_md = Path(notes[0])
            pending = write_article_pending(final_md.parent, final_md, res)
            progress.finish(notes)
            log("=" * 50)
            log(f"完成！公众号文章「{res['account']}」发布的《{res['title'][:40]}》")
            for n in notes:
                log(f"  {n}")
            log("-" * 50)
            log(f"最终交付物: {final_md}")
            log(f"待上传清单: {pending}")
            log("Agent 下一步：上传该 md 到 ima → 回读校验 → 校验通过后删本地文件")
            return 0

        # ---------- 视频通道 ----------
        progress.update("init", 2, f"开始处理 {args.source[:80]}")
        video, is_temp = resolve_source(args.source, run_dir, progress, args.wait_manual)
        out = run_stages(video, is_temp, cfg, run_dir, progress, keep=args.keep)

        res = out["result"]
        notes = out["notes"]
        if not args.no_publish:
            notes += publish(res, out["title"], notes)

        # 写 ima 待上传清单（最终交付物 = 整合后的单文件 md）
        final_md = next((Path(n) for n in notes
                         if Path(n).name.endswith(".md")
                         and Path(n).name not in ("tutorial.md", "brief.md",
                                                  "checklist.md")), None)
        pending = None
        if final_md:
            pending = write_ima_pending(final_md.parent, final_md, res, out["title"])

        progress.finish(notes)
        log("=" * 50)
        log(f"完成！类型={res['video_type']} 章节={len(res['chapters'])} "
            f"帧数={res['frames_used']} 成本=¥{res['cost']['cost_cny']} "
            f"耗时={res['elapsed_sec']}s")
        for n in notes:
            log(f"  {n}")
        if final_md:
            log("-" * 50)
            log(f"最终交付物: {final_md}")
            log(f"待上传清单: {pending}")
            log("Agent 下一步：上传该 md 到 ima → 回读校验 → 校验通过后删本地文件")
        return 0

    except Exception as e:
        tb = traceback.format_exc()
        log(tb)
        progress.fail(str(e))
        # 失败时保留视频便于重试
        if video and is_temp:
            log(f"失败，已保留视频: {video}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
