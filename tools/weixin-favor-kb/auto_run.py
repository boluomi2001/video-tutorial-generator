"""一键视频分析流水线：给链接或文件，直接产出教学笔记。

用法:
    python auto_run.py "<视频号链接或本地文件路径>" [--keep] [--no-publish] [--wait-manual 30]

流程: 下载 → 转录 → 抽帧 → OCR → 分析 → 三形态笔记 → 清理
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

from modules import wx_download
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

    # 优先走新版下载器的 MCP（视频号需微信页面已连接，其他平台直接可用）
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
    title = video.stem.rsplit("_", 1)[0] if "_" in video.stem else video.stem
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
    written = write_outputs(notes_dir, result_full, title)

    # ---- 清理 ----
    if not keep:
        progress.update("cleanup", 95, "清理中间文件")
        cleanup(video if is_temp else None, run_dir,
                files=[audio_path], dirs=[frames_dir])

    return {"result": result_full, "notes": written, "title": title}


def write_outputs(notes_dir: Path, res: dict, title: str) -> list[str]:
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

    (notes_dir / "raw.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return written


def publish(res: dict, title: str, notes: list[str]) -> list[str]:
    """把笔记同步到项目 outputs 目录。"""
    sub = "项目复刻教学" if res["video_type"] == VIDEO_TYPE_PROJECT else "功能提升教学"
    dest_dir = PUBLISH_ROOT / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = sanitize(title, 60)
    out: list[str] = []
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="视频号链接 / 直链 / 本地文件路径")
    ap.add_argument("--keep", action="store_true", help="保留视频与中间文件")
    ap.add_argument("--no-publish", action="store_true", help="不复制到项目 outputs 目录")
    ap.add_argument("--wait-manual", type=int, default=30, help="等待手动下载的分钟数")
    args = ap.parse_args()

    cfg = load_config()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = sanitize(Path(args.source).stem if not args.source.startswith("http")
                    else "sph_video", 30)
    run_dir = OUTPUT / f"{ts}_{name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_run_id.txt").write_text(f"{ts}_{name}", encoding="utf-8")

    progress = Progress(run_dir / "progress.json")
    video: Path | None = None
    is_temp = False

    try:
        progress.update("init", 2, f"开始处理 {args.source[:80]}")
        video, is_temp = resolve_source(args.source, run_dir, progress, args.wait_manual)
        out = run_stages(video, is_temp, cfg, run_dir, progress, keep=args.keep)

        res = out["result"]
        notes = out["notes"]
        if not args.no_publish:
            notes += publish(res, out["title"], notes)

        progress.finish(notes)
        log("=" * 50)
        log(f"完成！类型={res['video_type']} 章节={len(res['chapters'])} "
            f"帧数={res['frames_used']} 成本=¥{res['cost']['cost_cny']} "
            f"耗时={res['elapsed_sec']}s")
        for n in notes:
            log(f"  {n}")
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
