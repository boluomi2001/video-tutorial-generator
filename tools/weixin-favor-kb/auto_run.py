"""一键视频分析流水线：给链接或文件，直接产出教学笔记。

用法:
    # 单条
    python auto_run.py "<链接 / 本地文件路径>" [选项]

    # 批量（多条位置参数）
    python auto_run.py "<链接1>" "<链接2>" "<链接3>" ...

    # 批量（从文件读，一行一条，# 开头为注释）
    python auto_run.py --from-file links.txt

    # 断点续跑（跳过已成功项，重试失败项）
    python auto_run.py --from-file links.txt --resume output/batch_20261004_210000

选项:
    --keep            保留视频与中间文件
    --no-publish      不复制到项目 outputs 目录
    --publish-by-platform  发布时在类型目录下再按平台分子目录
    --no-worker       禁用云端解析 Worker，视频号强制走本地/微信通道
    --wait-manual N   等待手动下载的分钟数（默认 30）

支持的输入（按链接自动分流）:
    微信视频号     → 云端 Worker 解析（免登录）→ 视频流水线
    B站 / 抖音 / 快手 / 微博 / YouTube …  → 下载器 MCP → 视频流水线
    小红书         → 图文笔记归档（需 Cookie）/ 视频笔记转视频流水线
    公众号文章     → 抓取 → 保真转 Markdown（原文归档，不改写）
    本地文件       → 直接进视频流水线

流程（视频通道）:
    下载 → 转录 → 抽帧 → OCR → 分析 → 三形态笔记 → 清理
流程（小红书图文通道）:
    解析笔记 → 下载原图 → 保真归档 md（不改写）→ 清理

批量模式:
    串行逐条执行，单条失败不中断后续；每条独立 run 目录（沿用 output/）。
    批次目录 output/batch_<时间戳>/ 下产出：
      - batch_state.json        执行状态（供 --resume 续跑）
      - _batch_<时间戳>.md      汇总表（成功/失败/耗时/成本/产物）
      - .ima_pending_batch.json ima 待上传清单（汇总全部成功项）
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
# Whisper 模型首次使用要从 HuggingFace 下载（medium 约 1.5GB），国内直连很慢。
# 默认切到镜像；想用官方源就显式设 HF_ENDPOINT=https://huggingface.co
# （必须在 import faster_whisper / huggingface_hub 之前设好——所以放在这里）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml
from loguru import logger

from modules import platforms, wx_article, wx_download, xhs_note
from modules.analyzer import VIDEO_TYPE_PROJECT, ContentAnalyzer
from modules.audio import extract_audio
from modules.frames import build_contact_sheet, extract_keyframes, resolve_ffmpeg
from modules.ocr import OCRProcessor
from modules.transcribe import Transcriber

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
OUTPUT = ROOT / "output"
PROJECT_ROOT = ROOT.parent.parent
PUBLISH_ROOT = PROJECT_ROOT / "outputs" / "公众号视频项目"
# 小红书图文/视频笔记的归档目录（原文归档，不改写）
XHS_PUBLISH_ROOT = PROJECT_ROOT / "outputs" / "小红书笔记"
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


def _ima_setting(key: str, env_name: str) -> str:
    """读 ima 相关配置：环境变量 > config.yaml 的 ima 段 > 空。

    知识库 ID / 文件夹 ID 属于**使用者个人**信息，不能硬编码进仓库。
    留空时上报相关功能会被跳过（不报错），需要的人自己填。
    """
    v = (os.environ.get(env_name) or "").strip()
    if v:
        return v
    try:
        return str((load_config().get("ima") or {}).get(key) or "").strip()
    except Exception:
        return ""


IMA_KB_ID = _ima_setting("knowledge_base_id", "IMA_KB_ID")
IMA_FOLDER_ID = _ima_setting("folder_id", "IMA_FOLDER_ID")
IMA_KB_NAME = "你的 ima 知识库"


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
    这样视频号/B站/抖音下载不再用 `AWEUh1CSE0`、`_quality_1080x1920` 这种无意义命名。
    """
    if explicit:
        return explicit
    side = platforms.read_title_sidecar(video)
    if side:
        return side
    return video.stem.rsplit("_", 1)[0] if "_" in video.stem else video.stem


def _mcp_try_download(source: str, progress: Progress) -> dict | None:
    """尝试用新版下载器的 MCP 直接下载，返回产物字典，失败返回 None。

    返回值：{"video": Path|None, "images": [Path], "title": str, "platform": str}
    ——视频号/B站/抖音等视频类产出 video；小红书图集等产出 images。
    """
    wx_download.ensure_downloader(wait_sec=25)  # 没在跑就静默拉起
    try:
        cli = wx_download.MCPClient()
    except Exception as e:
        progress.update("download", 4, f"MCP 不可用（{str(e)[:60]}），转常规流程")
        return None

    plat = platforms.detect(source)
    plat_cn = platforms.label(plat)
    before = platforms.snapshot(DOWNLOADS)
    title = ""
    meta_ok = False
    dl_timeout = 900

    # 复用已下载产物：重跑同一条链接时下载器会判「已存在该下载内容」，
    # 这里先查最近一条已完成任务，文件仍在磁盘上就直接接管，省一次完整下载。
    try:
        old = cli.find_finished_task(source,
                                     alt_urls=(platforms.resolve_redirect(source),))
    except Exception:
        old = None
    if old:
        files = [Path(str(f.get("file_path")))
                 for f in (old.get("files") or []) if isinstance(f, dict)
                 and Path(str(f.get("file_path") or "")).exists()]
        vids = [p for p in files if p.suffix.lower() in platforms.VIDEO_EXTS]
        imgs = [p for p in files if p.suffix.lower() in platforms.IMAGE_EXTS]
        if vids or imgs:
            progress.update("download", 20,
                            f"复用已下载产物（{len(vids)} 视频 / {len(imgs)} 图）")
            try:
                meta = platforms.fetch_profile(source, cli)
                title = platforms.compose_title(meta.get("author", ""),
                                                meta.get("title", ""))
            except Exception:
                title = ""
            if title:
                platforms.write_title_sidecar((vids or imgs)[0], title)
            return {"video": (max(vids, key=lambda p: p.stat().st_mtime)
                              if vids else None),
                    "images": imgs, "title": title, "platform": plat}

    try:
        if wx_download.is_sph_url(source):
            if not cli.wxchannels_available():
                progress.update(
                    "download", 4,
                    "微信视频号页面未连接，请在微信里打开视频号后重试（随后自动接管）")
                return None
            # 视频号任务的 source_url 常是空串，只能靠 content_id 命中已下载任务。
            # fetch_content 对视频号链接实测可用，顺便把标题/作者一起拿回来。
            cid, ctitle, cauthor = cli.content_id_of(source)
            title = platforms.compose_title(cauthor, platforms.clean_title(ctitle))
            if cid:
                old = None
                try:
                    old = cli.find_finished_task(content_id=cid)
                except Exception:
                    old = None
                if old:
                    files = cli.files_of(old)
                    vids = [p for p in files
                            if p.suffix.lower() in platforms.VIDEO_EXTS]
                    if vids:
                        newest = max(vids, key=lambda p: p.stat().st_mtime)
                        if title:
                            platforms.write_title_sidecar(newest, title)
                        progress.update("download", 20,
                                        f"复用已下载视频号产物 {newest.name[:40]}")
                        return {"video": newest, "images": [], "title": title,
                                "platform": plat}
            progress.update("download", 5,
                            "MCP 下载视频号视频（本地通道）"
                            + (f"｜{title[:30]}" if title else ""))
            res = cli.download_video(source, download_dir=str(DOWNLOADS),
                                     existing_action="overwrite")
        else:
            # 非视频号：先取元数据（标题/作者/形态），再下载。
            # B站/抖音/快手/微博等走 download_content；小红书当前多为图集。
            try:
                meta = platforms.fetch_profile(source, cli)
                meta_ok = True
                title = platforms.compose_title(meta.get("author", ""),
                                                meta.get("title", ""))
                if meta.get("kind"):
                    progress.update(
                        "download", 5,
                        f"MCP 解析 {plat_cn}（{meta['kind']}）"
                        + (f"｜{title[:30]}" if title else ""))
            except Exception as e:
                # 解析层就失败 → 下载层必然也失败（下载依赖解析出的直链）。
                # 这里直接短路，避免 wait_for_completion 白等十几分钟：
                # 小红书当前就属于这种情况（需登录态，适配器拿不到笔记详情）。
                progress.update("download", 4,
                                f"MCP 解析 {plat_cn} 失败：{str(e)[:60]}")
                if plat == "xiaohongshu":
                    return None
                dl_timeout = 120
            res = cli.download_content(source, download_dir=str(DOWNLOADS),
                                       timeout=dl_timeout)
    except Exception as e:
        progress.update("download", 4,
                        f"MCP 下载失败（{str(e)[:60]}），转常规流程")
        return None

    # 等待新文件落盘（视频 / 图集两种形态）
    got = platforms.wait_for_artifacts(DOWNLOADS, before, timeout=600)
    videos, images = got.get("video") or [], got.get("image") or []

    if videos:
        newest = max(videos, key=lambda p: p.stat().st_mtime)
        if not title and not meta_ok:
            # 解析元数据失败、但下载居然成功（少见）：补取一次标题
            try:
                meta = platforms.fetch_profile(source, cli)
                title = platforms.compose_title(meta.get("author", ""),
                                                meta.get("title", ""))
            except Exception:
                title = ""
        if title:
            platforms.write_title_sidecar(newest, title)
        progress.update("download", 20, f"MCP 下载完成 {newest.name[:40]}")
        return {"video": newest, "images": [], "title": title, "platform": plat}

    if images:
        if title:
            platforms.write_title_sidecar(images[0], title)
        progress.update("download", 20,
                        f"MCP 下载完成 {plat_cn} 图集 {len(images)} 张")
        return {"video": None, "images": images, "title": title, "platform": plat}

    progress.update("download", 4, "MCP 已提交但未检测到文件，转常规流程")
    return None


def resolve_source(source: str, run_dir: Path, progress: Progress,
                   wait_manual: int, prefer_worker: bool = True) -> dict:
    """下载/定位素材。

    返回：{"video": Path|None, "images": [Path], "title": str, "is_temp": bool}
    —— 视频类只填 video；图集类只填 images；本地文件 is_temp=False。

    prefer_worker=False（或环境变量 SPH_NO_WORKER=1）时跳过云端 Worker，
    视频号强制走「下载器 + 微信页面」本地通道 —— 给不想注册 Cloudflare 的人用。
    """
    p = Path(source)
    if p.exists() and p.is_file():
        progress.update("download", 5, f"使用本地文件 {p.name}")
        return {"video": p, "images": [], "title": "", "is_temp": False}

    if not source.startswith("http"):
        raise RuntimeError(f"无法识别的输入: {source}")

    plat = platforms.detect(source)
    plat_cn = platforms.label(plat)
    if os.environ.get("SPH_NO_WORKER", "").strip() in ("1", "true", "yes"):
        prefer_worker = False

    # ① 视频号链接：优先走云端 Worker 解析（免登录态、免本地进程、全自动）
    if wx_download.is_sph_url(source) and prefer_worker:
        got = _sph_worker_try_download(source, progress)
        if got is not None:
            return {"video": got, "images": [], "title": "", "is_temp": True}
    elif wx_download.is_sph_url(source):
        progress.update("download", 4, "已禁用云端 Worker → 走本地/微信通道")

    # ② 优先走新版下载器的 MCP（视频号需微信页面已连接，其他平台直接可用）
    if not plat == "generic" or not wx_download.is_sph_url(source):
        progress.update("download", 4, f"识别为 {plat_cn}")
    got = _mcp_try_download(source, progress)
    if got is not None:
        return {"video": got.get("video"), "images": got.get("images") or [],
                "title": got.get("title") or "", "is_temp": True}

    if not wx_download.is_sph_url(source):
        progress.update("download", 5, "直链兜底下载")
        target = wx_download.fetch_direct(source, DOWNLOADS)
        return {"video": target, "images": [], "title": "", "is_temp": True}

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
    return {"video": got, "images": [], "title": "", "is_temp": True}


def run_stages(video: Path, is_temp: bool, cfg: dict, run_dir: Path,
               progress: Progress, keep: bool,
               facts_only: bool = False, source: str = "",
               local_only: bool = False, platform_key: str = "") -> dict:
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
    title = resolve_title(video)
    if local_only:
        # 纯本地模式：不调用任何 LLM API。
        # 视觉理解（看画面）与写笔记全部交给 Agent —— 流水线只负责产出干净素材：
        # 转录 + 关键帧 + 本地 OCR + 缩略图拼版。
        progress.update("analyze", 85, "纯本地：跳过 LLM（视觉/事实交给 Agent）")
        result_full = {
            "title": title,
            "video_type": "",            # 由 Agent 判定
            "domain": "",
            "duration": round(float(frames_meta.get("duration", 0) or 0), 1),
            "chapters": [],
            "facts": {},
            "tutorial": "",
            "brief": {},
            "checklist": {},
            "quality": {},
            "frames_used": len(frame_paths),
            "cost": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                     "cost_cny": 0.0},
            "elapsed_sec": 0,
            "facts_only": True,
            "local_only": True,
        }
    else:
        progress.update("analyze", 60, "视觉理解 + 事实抽取")
        lcfg = cfg["llm"]
        analyzer = ContentAnalyzer(
            api_key=lcfg.get("api_key", ""),
            base_url=lcfg.get("base_url", ""),
            model=lcfg.get("model", "Qwen/Qwen3-VL-32B-Instruct"),
            fast_model=lcfg.get("fast_model", ""),
        )
        result_full = analyzer.run_full(
            transcript=transcript,
            segments=segments,
            frames=frame_paths,
            frames_meta=frames_meta,
            ocr_items=ocr_items,
            title=title,
            duration=float(frames_meta.get("duration", 0) or 0),
            output_dir=str(notes_dir),
            facts_only=facts_only,
        )
        progress.update("analyze", 85,
                        f"分析完成 类型={result_full['video_type']} "
                        f"成本=¥{result_full['cost']['cost_cny']}")

    # ---- 输出 ----
    if facts_only:
        # Agent 模式：只落地干净源（转录 + 事实 + OCR 画面文字），
        # 不生成 Qwen 版笔记（tutorial/brief/checklist），也不做整合稿。
        progress.update("write", 90, "落地干净源（Agent 模式）")
        written = write_agent_input(notes_dir, run_dir, result_full, title,
                                    transcript, segments, ocr_items, source,
                                    platform_key=platform_key)
    else:
        progress.update("write", 90, "写入笔记")
        written = write_outputs(notes_dir, result_full, title,
                                transcript=transcript, segments=segments)

    # ---- 清理 ----
    if not keep:
        progress.update("cleanup", 95, "清理中间文件")
        # Agent 模式保留 frames：便于回溯"画面文字从哪来"，也让 Agent 可按需看图
        dirs = [] if facts_only else [frames_dir]
        cleanup(video if is_temp else None, run_dir,
                files=[audio_path], dirs=dirs)

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
        "knowledge_base_id": IMA_KB_ID,
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


def write_agent_input(notes_dir: Path, run_dir: Path, res: dict, title: str,
                      transcript: str, segments: list[dict] | None,
                      ocr_items: list[tuple[float, str]] | None,
                      source: str = "", platform_key: str = "") -> list[str]:
    """Agent 模式：只落地「干净源」——转录 + 结构化事实 + 画面文字(OCR)。

    刻意**不写** tutorial/brief/checklist，也不做整合稿：Qwen 的注水产物一旦
    混进输入就会把 Agent 带偏。取数约定直接写进本文件，避免口头约定失效。
    """
    plat = platform_key or platforms.detect(source)
    tdir = run_dir / "transcripts"
    tpath = next(iter(sorted(tdir.glob("*.txt"))), None)
    spath = next(iter(sorted(tdir.glob("*_segments.json"))), None)
    frames_dir = run_dir / "frames"
    frames = (sorted(str(p) for p in frames_dir.glob("keyframe_*.jpg"))
              if frames_dir.is_dir() else [])

    # 缩略图拼版：把全部关键帧拼成一张图，Agent 读一张即可纵览全片画面
    contact_sheet = ""
    if frames:
        try:
            contact_sheet = build_contact_sheet(
                frames, frames_dir / "contact_sheet.jpg") or ""
        except Exception as e:
            log(f"缩略图拼版失败（不影响主流程）: {e}")

    # 本地可得的两个"画面/文字"信号，供 Agent 自适应决定要不要看单帧
    meta: dict = {}
    meta_file = frames_dir / "frames_meta.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    n_ocr = len(ocr_items or [])
    ocr_chars = sum(len(t) for _, t in (ocr_items or []))

    payload = {
        "mode": "agent_write",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "title": title,
        "source_url": source,
        "platform": plat,
        "platform_label": platforms.label(plat),
        "video_type": res.get("video_type", ""),
        "domain": res.get("domain", ""),
        "duration": res.get("duration", 0),
        "chapters_hint": res.get("chapters", []),
        "facts": res.get("facts", {}),
        "transcript": transcript,
        "transcript_path": str(tpath) if tpath else "",
        "segments_path": str(spath) if spath else "",
        "ocr": [{"t": round(float(t), 1), "text": txt}
                for t, txt in (ocr_items or [])],
        "frames_dir": str(frames_dir) if frames else "",
        "frames": frames,
        "contact_sheet": contact_sheet,
        "frame_profile": meta.get("profile", {}),
        "ocr_stats": {
            "frames": n_ocr,
            "chars": ocr_chars,
            "chars_per_frame": round(ocr_chars / max(n_ocr, 1), 1),
        },
        "agent_rules": {
            "read_only": ["facts", "transcript", "segments_path", "ocr",
                          "chapters_hint", "contact_sheet", "frames"],
            "never_read": [
                "notes/tutorial.md / brief.md / checklist.md",
                "notes/raw.json 的 tutorial/brief/checklist 字段",
                "任何已成型的整合稿 md",
            ],
            "note": ("先看 contact_sheet（全片关键帧缩略拼版，每格标了时间戳）纵览画面；"
                     "只在需要看清某屏时才读 frames/ 里的单帧原图。"
                     "笔记结构按本条视频内容自定；音频文案取自 segments（带 mm:ss），原样保留。"
                     f"本条来源平台：{platforms.label(plat)}（platform={plat}）。"),
            "adapt_rule": (
                "按内容自适应。注意：frame_profile / ocr_stats 是本地机器算出的**参考信号**，"
                "可能失准（例：彩色打光下人脸检测会漏检、字幕多的口播会被判成 code）——"
                "**以你在 contact_sheet 里实际看到的画面为准**。"
                "① 文稿/PPT/代码/图表类（ocr_stats.chars_per_frame 偏高，如 >150）"
                "→ 画面文字已在 ocr 里，一般无需再看单帧；"
                "② 实物/场景/3D/审美/操作演示类（chars_per_frame 偏低）→ 按需读 frames/ 中"
                "对应时间戳的原帧，看清画面细节；"
                "③ 纯口播类（画面只有人脸）→ 画面无信息，只写转录内容。"
            ),
        },
    }
    p = notes_dir / "agent_input.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")

    # raw.json 仍留一份便于回溯（Agent 模式下 tutorial 为空，不构成污染）
    (notes_dir / "raw.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return [str(p)]


def write_agent_pending(notes_dir: Path, res: dict, title: str,
                        agent_input: Path, source: str = "") -> Path:
    """Agent 模式的待办清单：不是「待上传 md」，而是「待 Agent 写笔记」。"""
    sub = ("项目复刻教学" if res.get("video_type") == VIDEO_TYPE_PROJECT
           else "功能提升教学")
    manifest = {
        "mode": "agent_write",
        "agent_input": str(agent_input),
        "title": title,
        "knowledge_base_id": IMA_KB_ID,
        "folder_hint": sub,
        "folder_id": IMA_FOLDER_ID,
        "source_url": source,
        "delete_local_after_verify": True,
        "notes_dir": str(notes_dir),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent_steps": [
            "读 agent_input.json 的 facts / transcript / ocr（禁止读任何 *.md）",
            "按视频内容自定笔记结构（观点 / 操作 / 科普 / 方法 / 工具 / 随笔 …）",
            "音频文案取自 segments（带 mm:ss），原样保留",
            "写出的 md 存到 outputs/_新版笔记/",
            f"上传 ima「你的知识库」({IMA_FOLDER_ID}) → 回读校验 → 命中后删本地",
        ],
    }
    p = notes_dir / ".agent_pending.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def publish(res: dict, title: str, notes: list[str],
            source: str = "", by_platform: bool = False) -> list[str]:
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

    if res.get("source_type") == "xhs_note":
        # 小红书笔记归档：md + 配图同目录，保持相对引用可用
        dest_dir = XHS_PUBLISH_ROOT
        dest_dir.mkdir(parents=True, exist_ok=True)
        out: list[str] = []
        src_md = Path(notes[0])
        for src in notes:
            p = Path(src)
            if p.name == "raw.json":
                continue
            shutil.copy2(p, dest_dir / p.name)
            out.append(str(dest_dir / p.name))
        # 配图目录整份复制（md 里是 `images/xx.jpg` 相对引用）
        img_src = src_md.parent / "images"
        if img_src.is_dir():
            img_dst = dest_dir / "images"
            img_dst.mkdir(parents=True, exist_ok=True)
            for f in img_src.iterdir():
                if f.is_file():
                    shutil.copy2(f, img_dst / f.name)
        return out

    sub = "项目复刻教学" if res["video_type"] == VIDEO_TYPE_PROJECT else "功能提升教学"
    dest_dir = PUBLISH_ROOT / sub
    if by_platform:
        key = res.get("platform") or platforms.detect(source)
        dest_dir = dest_dir / platforms.label(key or "generic")
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
        "knowledge_base_id": IMA_KB_ID,
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


# ------------------------------------------------------------ 小红书笔记通道

XHS_COOKIE_HINT = (
    "小红书解析失败。请按顺序排查：\n"
    "  1) 链接里有没有 xsec_token？**这才是关键**（与登录无关）——\n"
    "     请在小红书 App 里打开该笔记 →「分享」→「复制链接」，用复制出来的链接重试；\n"
    "     浏览器地址栏直接复制、或手打的 /explore/<id> 裸链接通常不带 token，匿名拿不到详情。\n"
    "  2) 笔记是不是已删除 / 被设为私密 / 仅关注可见？这类才需要登录态。\n"
    "  3) 确实需要登录时，把 Cookie 写进 config.yaml 的 xhs.cookie（或用环境变量 XHS_COOKIE），\n"
    "     或直接跑 login_xhs.cmd 扫码登录（会帮你自动写入）。"
)


def _norm_xhs_note(raw: dict) -> dict:
    """把 xhs_note.fetch 的结果规整成通道内部统一结构。"""
    title = (raw.get("title") or "").strip()
    desc = (raw.get("desc") or "").strip()
    if not title:
        title = platforms.clean_title(desc)
    return {
        "kind": raw.get("kind") or "images",
        "title": title,
        "desc": desc,
        "author": (raw.get("author") or "").strip(),
        "publish_time": raw.get("publish_time") or "",
        "tags": raw.get("tags") or [],
        "url": raw.get("url") or "",
        "note_id": raw.get("note_id") or "",
        "remote_images": raw.get("images") or [],
        "video_url": raw.get("video_url") or "",
    }


def xhs_prepare(source: str, progress: Progress) -> tuple[dict, Path | None, list[Path]]:
    """解析小红书链接，返回 (规范化笔记, 本地视频路径|None, 本地图片列表)。

    优先级（2026-10-15 更正：**匿名优先，不需要登录**）：
      ① 直接解析（不带 Cookie）—— 只要链接带 xsec_token 就能拿到详情
      ② 配置里有 `xhs.cookie` → 带 Cookie 再试一次（私密/仅关注可见才需要）
      ③ 下载器 MCP 兜底
    全失败时抛出带操作指引的异常，绝不静默产出空笔记。
    """
    progress.update("fetch", 10, "解析小红书笔记（匿名）")
    try:
        raw = xhs_note.fetch(source)
        return _norm_xhs_note(raw), None, []
    except xhs_note.XhsUnavailable as e:
        log(f"小红书匿名解析失败：{e}")

    cookie = xhs_note.cookie_from_config()
    if cookie:
        progress.update("fetch", 11, "匿名失败 → 带已配置的 Cookie 重试")
        try:
            raw = xhs_note.fetch(source, cookie=cookie)
            return _norm_xhs_note(raw), None, []
        except xhs_note.XhsError as e:
            log(f"小红书带 Cookie 解析也失败：{e}")
            progress.update("fetch", 12, f"带 Cookie 仍失败（{str(e)[:40]}）")
    else:
        progress.update("fetch", 12, "匿名失败，且未配置 Cookie，尝试下载器通道")

    got = _mcp_try_download(source, progress)
    if got and got.get("images"):
        title = got.get("title") or ""
        note = {
            "kind": "images", "title": title,
            "desc": "", "author": "", "publish_time": "", "tags": [],
            "url": source, "note_id": xhs_note.extract_note_id(source) or "",
            "remote_images": [],
        }
        return note, None, list(got["images"])
    if got and got.get("video"):
        note = {
            "kind": "video", "title": got.get("title") or "",
            "desc": "", "author": "", "publish_time": "", "tags": [],
            "url": source, "note_id": xhs_note.extract_note_id(source) or "",
            "remote_images": [],
        }
        return note, Path(got["video"]), []

    raise RuntimeError("小红书解析失败。\n" + XHS_COOKIE_HINT)


def archive_xhs_note(note: dict, local_images: list[Path], run_dir: Path,
                     progress: Progress, source: str = "") -> dict:
    """图文笔记归档：下载原图（或直接用已落盘的图）→ 保真 md（不改写一字）。"""
    notes_dir = run_dir / "notes"
    assets_dir = notes_dir / "images"
    notes_dir.mkdir(parents=True, exist_ok=True)

    files = list(local_images)
    if not files and note.get("remote_images"):
        progress.update("fetch", 55, f"下载原图 {len(note['remote_images'])} 张")
        cookie = xhs_note.cookie_from_config()
        files = xhs_note.download_images(note, assets_dir, cookie=cookie)
    elif files:
        # MCP 产出已在 downloads/，复制进 run 目录一起归档
        assets_dir.mkdir(parents=True, exist_ok=True)
        moved: list[Path] = []
        for i, src in enumerate(sorted(files, key=lambda p: p.name), 1):
            target = assets_dir / f"note_{i:02d}{src.suffix.lower() or '.jpg'}"
            try:
                shutil.copy2(src, target)
                moved.append(target)
            except Exception:
                continue
        files = moved

    progress.update("convert", 70, "生成保真归档 md")
    rel = [Path("images") / p.name for p in files]
    md = xhs_note.to_markdown(note, image_files=rel)

    title = note.get("title") or "小红书笔记"
    final = notes_dir / xhs_note.filename_for(title)
    final.write_text(md, encoding="utf-8")

    res = {
        "video_type": "小红书图文笔记",
        "source_type": "xhs_note",
        "title": title,
        "author": note.get("author", ""),
        "account": note.get("author", ""),
        "publish_time": note.get("publish_time", ""),
        "url": note.get("url") or source,
        "duration": 0,
        "chapters": [],
        "frames_used": 0,
        "image_count": len(files),
        "cost": {"cost_cny": 0},
        "elapsed_sec": 0,
    }
    progress.update("write", 90, f"写入归档 md（{len(files)} 张配图）")
    log(f"归档完成：{final}")
    return {"result": res, "notes": [str(final)], "title": title}


def write_xhs_pending(notes_dir: Path, final_md: Path, res: dict) -> Path:
    """小红书图文笔记的 ima 待上传清单。"""
    manifest = {
        "final_md": str(final_md),
        "title": final_md.name,
        "knowledge_base_id": IMA_KB_ID,
        "folder_hint": ARTICLE_FOLDER_HINT,
        "source_type": "xhs_note",
        "source_url": res.get("url", ""),
        "author": res.get("author", ""),
        "image_count": res.get("image_count", 0),
        "delete_local_after_verify": True,
        "notes_dir": str(notes_dir),
        "intermediate": [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = notes_dir / ".ima_pending.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def run_one(source: str, no_publish: bool = False, keep: bool = False,
            wait_manual: int = 30, ts: str | None = None,
            agent_write: bool = False, local_only: bool = False,
            by_platform: bool = False, no_worker: bool = False) -> dict:
    """处理单条输入（视频号 / 小红书 / 公众号文章 / 其它平台链接 / 本地文件）。

    这是单条模式的完整逻辑，也被批量模式逐条复用。
    返回统一结构，便于批量层汇总：
        {
          "source": 原始输入,
          "ok": bool,
          "source_type": "video" | "article" | "xhs_note",
          "video_type": 类型名,
          "title": 最终标题,
          "run_dir": run 目录,
          "final_md": 最终交付物路径（失败为 None）,
          "pending": ima 待上传清单路径（失败为 None）,
          "notes": 产出文件列表,
          "elapsed_sec": 耗时,
          "cost_cny": 成本,
          "error": 失败原因（成功为 None）,
        }

    通道分流：
        公众号文章链接                        → 原文归档（wx_article）
        小红书链接                            → 图文笔记归档 / 视频笔记转视频通道
        视频号 & 其它平台（B站/抖音/快手…）    → 视频流水线（转录取帧 OCR → Agent）
    """
    started = time.time()
    stamp = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    is_article = wx_article.is_article_url(source)
    is_xhs = xhs_note.is_xhs_url(source)
    platform_key = platforms.detect(source)

    def _out(ok: bool, **kw) -> dict:
        base = {
            "source": source, "ok": ok,
            "source_type": ("article" if is_article
                            else "xhs_note" if is_xhs else "video"),
            "platform": platform_key,
            "video_type": "", "title": "", "run_dir": None, "final_md": None,
            "pending": None, "agent_input": None, "notes": [], "elapsed_sec": 0,
            "cost_cny": 0.0, "error": None,
        }
        base["elapsed_sec"] = round(time.time() - started, 1)
        base.update(kw)
        return base

    if is_article:
        name = "wechat_article"
    elif source.startswith("http"):
        name = f"{platform_key}_video" if platform_key not in ("", "generic") \
            else "sph_video"
        if is_xhs:
            name = "xhs_note"
    else:
        name = sanitize(Path(source).stem, 30)
    run_dir = OUTPUT / f"{stamp}_{name}"
    # 批量下同一秒可能重名，加后缀避免互相覆盖
    suffix = 2
    while run_dir.exists():
        run_dir = OUTPUT / f"{stamp}_{name}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "_run_id.txt").write_text(run_dir.name, encoding="utf-8")

    progress = Progress(run_dir / "progress.json")
    video: Path | None = None
    is_temp = False

    try:
        # ---------- 公众号文章通道 ----------
        if is_article:
            progress.update("init", 2, f"公众号文章 {source[:80]}")
            out = run_article(source, run_dir, progress)
            res = out["result"]
            notes = out["notes"]
            if not no_publish:
                notes += publish(res, out["title"], notes, source=source)
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
            return _out(True, video_type="公众号文章", title=res["title"],
                        run_dir=run_dir, final_md=final_md, pending=pending,
                        notes=notes)

        # ---------- 小红书通道 ----------
        if is_xhs:
            progress.update("init", 2, f"小红书笔记 {source[:80]}")
            note, local_video, local_images = xhs_prepare(source, progress)
            if note["kind"] == "video" and local_video is None:
                # 带 Cookie 解析出的视频笔记：直连下载后交给视频流水线
                progress.update("fetch", 30, "下载小红书视频")
                local_video = xhs_note.download_video(
                    note, DOWNLOADS, cookie=xhs_note.cookie_from_config())
            if local_video is not None:
                video, is_temp = local_video, True
                title_hint = platforms.compose_title(note.get("author", ""),
                                                     note.get("title", ""))
                if title_hint:
                    platforms.write_title_sidecar(video, title_hint)
                log(f"小红书视频笔记 → 转视频流水线：{video.name}")
                # 落到下方共用的视频通道
            else:
                out = archive_xhs_note(note, local_images, run_dir, progress,
                                       source=source)
                res = out["result"]
                res["platform"] = "xiaohongshu"
                notes = out["notes"]
                if not no_publish:
                    notes += publish(res, out["title"], notes, source=source)
                final_md = Path(notes[0])
                pending = write_xhs_pending(final_md.parent, final_md, res)
                progress.finish(notes)
                log("=" * 50)
                log(f"完成！小红书图文笔记《{res['title'][:40]}》"
                    f"（配图 {res.get('image_count', 0)} 张）")
                for n in notes:
                    log(f"  {n}")
                log("-" * 50)
                log(f"最终交付物: {final_md}")
                log(f"待上传清单: {pending}")
                return _out(True, video_type="小红书图文笔记", title=res["title"],
                            run_dir=run_dir, final_md=final_md, pending=pending,
                            notes=notes)

        # ---------- 视频通道 ----------
        if video is None:
            progress.update("init", 2, f"开始处理 {source[:80]}")
            got = resolve_source(source, run_dir, progress, wait_manual,
                                 prefer_worker=not no_worker)
            video = got.get("video")
            is_temp = bool(got.get("is_temp"))
            # 通用图集兜底：非小红书平台但下载器只吐了图片时，也走归档而不是报错
            if video is None and got.get("images"):
                img_note = {
                    "kind": "images", "title": got.get("title") or "",
                    "desc": "", "author": "", "publish_time": "", "tags": [],
                    "url": source, "note_id": "", "remote_images": [],
                }
                out = archive_xhs_note(img_note, list(got["images"]), run_dir,
                                       progress, source=source)
                res = out["result"]
                res["source_type"] = "xhs_note"
                res["platform"] = platform_key
                notes = out["notes"]
                if not no_publish:
                    notes += publish(res, out["title"], notes, source=source)
                final_md = Path(notes[0])
                pending = write_xhs_pending(final_md.parent, final_md, res)
                progress.finish(notes)
                log(f"完成！{platforms.label(platform_key)}图集归档《{res['title'][:40]}》")
                for n in notes:
                    log(f"  {n}")
                return _out(True, video_type="图集归档", title=res["title"],
                            run_dir=run_dir, final_md=final_md, pending=pending,
                            notes=notes)
            if video is None:
                raise RuntimeError("未获取到可处理素材（既无视频也无图片）")

        out = run_stages(video, is_temp, cfg=load_config(), run_dir=run_dir,
                         progress=progress, keep=keep,
                         facts_only=agent_write, source=source,
                         local_only=local_only, platform_key=platform_key)

        res = out["result"]
        res["platform"] = platform_key
        notes = out["notes"]

        # ---------- Agent 模式：只出干净源，不生成、不发布 Qwen 版 ----------
        if agent_write:
            agent_input = Path(notes[0]) if notes else None
            pending = (write_agent_pending(agent_input.parent, res, out["title"],
                                           agent_input, source)
                       if agent_input else None)
            progress.finish(notes)
            log("=" * 50)
            log(f"完成（Agent 模式）！平台={platforms.label(platform_key)} "
                f"帧数={res['frames_used']} 成本=¥{res['cost']['cost_cny']} "
                f"耗时={res['elapsed_sec']}s")
            for n in notes:
                log(f"  {n}")
            if pending:
                log("-" * 50)
                log(f"待办清单: {pending}")
                log("Agent 下一步：读 agent_input.json → 按内容写笔记 → "
                    "上传 ima「你的知识库」→ 回读校验 → 删本地")
            return _out(True, video_type=res.get("video_type", ""),
                        title=out.get("title", ""), run_dir=run_dir,
                        final_md=None, pending=pending, notes=notes,
                        agent_input=agent_input,
                        cost_cny=float(res.get("cost", {}).get("cost_cny", 0) or 0))

        if not no_publish:
            notes += publish(res, out["title"], notes, source=source,
                             by_platform=by_platform)

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
        log(f"完成！平台={platforms.label(platform_key)} 类型={res['video_type']} "
            f"章节={len(res['chapters'])} 帧数={res['frames_used']} "
            f"成本=¥{res['cost']['cost_cny']} 耗时={res['elapsed_sec']}s")
        for n in notes:
            log(f"  {n}")
        if final_md:
            log("-" * 50)
            log(f"最终交付物: {final_md}")
            log(f"待上传清单: {pending}")
        return _out(True, video_type=res.get("video_type", ""),
                    title=out.get("title", ""), run_dir=run_dir,
                    final_md=final_md, pending=pending, notes=notes,
                    cost_cny=float(res.get("cost", {}).get("cost_cny", 0) or 0))

    except Exception as e:
        tb = traceback.format_exc()
        log(tb)
        progress.fail(str(e))
        # 失败时保留视频便于重试
        if video and is_temp:
            log(f"失败，已保留视频: {video}")
        return _out(False, run_dir=run_dir, error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 批量模式
BATCH_STATE_NAME = "batch_state.json"


def _json_default(o):
    """让 Path 等对象可被 json 序列化（run_one 返回值里含 Path）。"""
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _dump_batch_state(batch_dir: Path, sources: list[str],
                      results: list[dict], started_at: str) -> None:
    """落盘批次状态。

    必须成功——否则 --resume 失去依据。因此这里不静默吞异常，
    出错时打日志并抛出，避免"以为能续跑其实没记录"。
    """
    payload = {
        "sources": sources,
        "results": results,
        "started_at": started_at,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        (batch_dir / BATCH_STATE_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2,
                       default=_json_default),
            encoding="utf-8")
    except Exception as e:
        log(f"⚠️ 批次状态写入失败（断点续跑将不可用）："
            f"{type(e).__name__}: {e}")


def load_sources(args) -> list[str]:
    """收集待处理输入：位置参数 + --from-file 指定的文件。

    文件规则：一行一条；支持 # 注释行与空行；自动去重（保序）。
    """
    items: list[str] = list(args.sources or [])

    if args.from_file:
        p = Path(args.from_file)
        if not p.is_file():
            raise FileNotFoundError(f"链接文件不存在：{p}")
        for raw in p.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            items.append(line)

    # 去重（保序），避免手抖重复导致白跑
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        key = s.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def write_batch_summary(batch_dir: Path, results: list[dict],
                        started_at: str) -> Path:
    """写批次汇总 md，并返回路径。"""
    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    total_cost = sum(float(r.get("cost_cny") or 0) for r in results)
    # 失败项的 elapsed 无意义（多为快速报错），只统计成功项耗时
    ok_elapsed = sum(float(r.get("elapsed_sec") or 0) for r in ok)

    lines = [
        "# 批量处理汇总",
        "",
        f"- 批次开始：{started_at}",
        f"- 批次结束：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 总数：**{len(results)}**　成功：**{len(ok)}**　"
        f"失败：**{len(bad)}**",
        f"- 成功项累计耗时：{ok_elapsed:.0f}s　累计成本：¥{total_cost:.4f}",
        "",
        "| # | 状态 | 类型 | 标题 | 耗时 | 成本 | 产物 / 失败原因 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        if r.get("ok"):
            mark = "✅"
            src_art = r.get("final_md") or r.get("agent_input")
            art = Path(src_art).name if src_art else "—"
            detail = f"`{art}`"
        else:
            mark = "❌"
            detail = (r.get("error") or "未知错误").replace("|", "\\|")[:120]
        lines.append(
            f"| {i} | {mark} | {r.get('video_type') or '—'} | "
            f"{(r.get('title') or '—').replace('|', chr(92) + '|')[:40]} | "
            f"{r.get('elapsed_sec') or 0}s | "
            f"¥{float(r.get('cost_cny') or 0):.4f} | {detail} |"
        )

    if bad:
        lines += ["", "## 失败明细（可直接复制重跑）", "", "```"]
        for r in bad:
            lines.append(r["source"])
        lines += ["```", "",
                  "> 用 `--from-file` 指向上面这段即可只重跑失败项。", ""]

    p = batch_dir / f"_batch_{started_at}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_batch_pending(batch_dir: Path, results: list[dict]) -> Path | None:
    """汇总所有成功产出的 ima 待上传清单（供 Agent 一次性批量上传）。"""
    items = []
    for i, r in enumerate(results, 1):
        if not r.get("ok") or not r.get("final_md"):
            continue
        md = Path(r["final_md"])
        if not md.is_file():
            continue
        is_article = r.get("source_type") == "article"
        items.append({
            "seq": i,
            "final_md": str(md),
            "title": md.name,
            "knowledge_base_id": IMA_KB_ID,
            "folder_hint": (ARTICLE_FOLDER_HINT if is_article
                            else ("项目复刻教学"
                                  if r.get("video_type") == VIDEO_TYPE_PROJECT
                                  else "功能提升教学")),
            "source_type": r.get("source_type"),
            "source_url": r.get("source"),
            "notes_dir": str(Path(r["run_dir"]) / "notes") if r.get("run_dir") else "",
            "delete_local_after_verify": True,
        })
    if not items:
        return None

    manifest = {
        "batch": True,
        "count": len(items),
        "knowledge_base_id": IMA_KB_ID,
        "delete_local_after_verify": True,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
    }
    p = batch_dir / ".ima_pending_batch.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def write_batch_agent_pending(batch_dir: Path,
                              results: list[dict]) -> Path | None:
    """Agent 模式：汇总各条的 agent_input，供 Agent 一次性写笔记 + 上传。"""
    items = []
    for i, r in enumerate(results, 1):
        if not r.get("ok") or not r.get("agent_input"):
            continue
        ai = Path(r["agent_input"])
        if not ai.is_file():
            continue
        items.append({
            "seq": i,
            "agent_input": str(ai),
            "title": r.get("title") or "",
            "video_type": r.get("video_type") or "",
            "source_url": r.get("source"),
            "folder_id": IMA_FOLDER_ID,
            "notes_dir": str(ai.parent),
        })
    if not items:
        return None

    manifest = {
        "mode": "agent_write",
        "batch": True,
        "count": len(items),
        "knowledge_base_id": IMA_KB_ID,
        "folder_id": IMA_FOLDER_ID,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent_steps": [
            "逐条读 agent_input.json 的 facts / transcript / ocr（禁止读任何 *.md）",
            "按每条内容自定结构写笔记 → 存 outputs/_新版笔记/seqNN_标题.md",
            "音频文案取自 segments（带 mm:ss），原样保留",
            f"逐条上传 ima「你的知识库」({IMA_FOLDER_ID}) → 回读校验 → 命中后删本地",
        ],
        "items": items,
    }
    p = batch_dir / ".agent_pending_batch.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def run_batch(sources: list[str], args) -> int:
    """串行批量执行：逐条隔离，失败不中断；支持断点续跑。"""
    OUTPUT.mkdir(parents=True, exist_ok=True)

    # 断点续跑：复用既有批次目录
    resume_dir = Path(args.resume).resolve() if args.resume else None
    if args.resume and not resume_dir.is_dir():
        log(f"--resume 指向的目录不存在：{resume_dir}")
        return 2

    if resume_dir:
        batch_dir = resume_dir
        started_at = batch_dir.name.replace("batch_", "") or \
            datetime.now().strftime("%Y%m%d_%H%M%S")
        state_file = batch_dir / BATCH_STATE_NAME
        prev: dict = {}
        if state_file.is_file():
            try:
                prev = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        done_map = {r["source"]: r for r in prev.get("results", [])}
        log(f"断点续跑：批次 {batch_dir.name}，历史记录 {len(done_map)} 条")
    else:
        started_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = OUTPUT / f"batch_{started_at}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        done_map = {}

    # 立刻写一份初始状态：即使第一条就崩，也能用 --resume 接上
    _dump_batch_state(batch_dir, sources, [], started_at)

    results: list[dict] = []
    total = len(sources)
    skipped = 0

    for i, src in enumerate(sources, 1):
        # 已完成且成功的项直接跳过（断点续跑核心）
        old = done_map.get(src)
        if old and old.get("ok"):
            log(f"[{i}/{total}] 跳过（已成功）：{src[:70]}")
            results.append(old)
            skipped += 1
            continue

        log("")
        log("#" * 60)
        log(f"[{i}/{total}] 开始处理：{src[:100]}")
        log("#" * 60)

        r = run_one(src, no_publish=args.no_publish, keep=args.keep,
                    wait_manual=args.wait_manual,
                    agent_write=getattr(args, "agent_write", False),
                    local_only=bool(getattr(args, "agent_write", False)
                                    and not getattr(args, "with_vision", False)),
                    by_platform=getattr(args, "publish_by_platform", False),
                    no_worker=getattr(args, "no_worker", False))
        results.append(r)

        if r["ok"]:
            log(f"[{i}/{total}] ✅ 完成：{r.get('title') or r['source'][:60]}")
        else:
            log(f"[{i}/{total}] ❌ 失败：{r.get('error')}")
            log(f"[{i}/{total}] 继续处理下一条…")

        # 每处理一条就落盘状态，进程被杀也能续跑
        _dump_batch_state(batch_dir, sources, results, started_at)

    # ---------- 收尾：汇总 ----------
    summary = write_batch_summary(batch_dir, results, started_at)
    agent_mode = bool(getattr(args, "agent_write", False))
    pending = (write_batch_agent_pending(batch_dir, results) if agent_mode
               else write_batch_pending(batch_dir, results))

    ok = [r for r in results if r.get("ok")]
    log("")
    log("=" * 60)
    log(f"批量完成：{len(ok)}/{total} 成功"
        + (f"（跳过 {skipped} 条已成功项）" if skipped else ""))
    log(f"批次目录: {batch_dir}")
    log(f"汇总清单: {summary}")
    if pending:
        log(f"待办清单: {pending}")
        if agent_mode:
            log(f"Agent 下一步：读该清单，逐条「读干净源→按内容写笔记→上传 ima→回读校验」"
                f"（共 {len(ok)} 项）")
        else:
            log(f"Agent 下一步：读取该清单，逐条上传 ima → 回读校验 → 校验通过后删本地"
                f"（共 {len(ok)} 项）")
    else:
        log("没有成功产出，无待办清单。")
    log("=" * 60)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="一键视频分析流水线：单条或批量。")
    ap.add_argument("sources", nargs="*",
                    help="一条或多条：视频号 / B站 / 抖音 / 小红书 / 快手等平台链接、"
                         "公众号文章链接、直链、本地文件路径")
    ap.add_argument("--from-file", metavar="FILE",
                    help="从文件读取链接（一行一条，# 开头为注释）")
    ap.add_argument("--resume", metavar="BATCH_DIR",
                    help="断点续跑：指向既有批次目录，跳过已成功项")
    ap.add_argument("--keep", action="store_true", help="保留视频与中间文件")
    ap.add_argument("--no-publish", action="store_true", help="不复制到项目 outputs 目录")
    ap.add_argument("--agent-write", action="store_true",
                    help="Agent 写笔记模式：只产出干净源（转录+关键帧+OCR+拼版），"
                         "不生成、也不发布 Qwen 版笔记；默认同时纯本地（不调 LLM API）")
    ap.add_argument("--with-vision", action="store_true",
                    help="配合 --agent-write：保留付费视觉模型（硅基流动），"
                         "仅在需要无人值守的超大批量时才用；默认纯本地")
    ap.add_argument("--publish-by-platform", action="store_true",
                    help="发布时在类型目录下再按平台分子目录（如 "
                         "outputs/公众号视频项目/功能提升教学/B站/）")
    ap.add_argument("--no-worker", action="store_true",
                    help="禁用云端解析 Worker，视频号强制走本地/微信通道"
                         "（不想注册 Cloudflare 的人用这条；也可设 SPH_NO_WORKER=1）")
    ap.add_argument("--wait-manual", type=int, default=30, help="等待手动下载的分钟数")
    args = ap.parse_args()

    # Agent 模式默认「纯本地」：不调 LLM API，视觉/写作都交给 Agent；
    # 只有显式 --with-vision 才保留付费视觉模型（无人值守超大批量场景）。
    local_only = bool(args.agent_write and not args.with_vision)

    sources = load_sources(args)

    # 批量模式：多条输入，或显式 --resume
    if args.resume or len(sources) > 1:
        if not sources and not args.resume:
            ap.error("批量模式至少需要一条输入（位置参数或 --from-file）")
        if not sources:
            ap.error("--resume 需要同时给出待处理的输入列表")
        log(f"批量模式：共 {len(sources)} 条待处理")
        return run_batch(sources, args)

    # 单条模式：保持原有输出格式与退出码
    if len(sources) == 1:
        r = run_one(sources[0], no_publish=args.no_publish, keep=args.keep,
                    wait_manual=args.wait_manual, agent_write=args.agent_write,
                    local_only=local_only,
                    by_platform=args.publish_by_platform,
                    no_worker=args.no_worker)
        if r["ok"]:
            log("-" * 50)
            if args.agent_write:
                log(f"模式：Agent 写笔记｜{'纯本地（零 API 调用）' if local_only else '含付费视觉模型'}")
                log("Agent 下一步：看 contact_sheet 判断类型 → 按需读原帧 → "
                    "按内容写笔记 → 上传 ima「你的知识库」→ 回读校验 → 删本地")
            else:
                log("Agent 下一步：上传该 md 到 ima → 回读校验 → 校验通过后删本地文件")
            return 0
        return 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
