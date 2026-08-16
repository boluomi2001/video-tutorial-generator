"""视频转知识笔记 — 主管道入口"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.table import Table

FFMPEG_PATH = r"D:\work\2026-07-12-13-31-07\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe"

from modules.audio import extract_audio
from modules.transcribe import Transcriber
from modules.frames import extract_keyframes
from modules.ocr import OCRProcessor
from modules.classifier import classify_content
from modules.analyzer import ContentAnalyzer

console = Console()

VIDEO_EXTENSIONS: set[str] = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".webm"}


def load_config(config_path: str) -> dict:
    """加载配置文件，缺失时使用默认值。"""
    defaults: dict = {
        "whisper": {
            "model_size": "large-v3",
            "device": "cuda",
            "compute_type": "int8_float16",
            "language": "zh",
        },
        "llm": {
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
        },
        "frames": {"threshold": 30.0, "max_frames": 20},
        "ocr": {"confidence_threshold": 0.5},
        "categories": [
            {
                "name": "RAG",
                "emoji": "🔍",
                "description": "检索增强生成、向量检索、知识库问答",
            },
            {
                "name": "AGENT",
                "emoji": "🤖",
                "description": "AI智能体开发、Skills、MCP、Claude Code",
            },
            {
                "name": "AI/ML",
                "emoji": "🧠",
                "description": "大模型通用、模型训练、AI行业趋势",
            },
            {"name": "前端", "emoji": "🎨", "description": "前端框架、UI/UX设计、CSS"},
            {"name": "后端", "emoji": "⚙️", "description": "后端架构、数据库、API设计"},
            {
                "name": "DevOps",
                "emoji": "🔧",
                "description": "运维、HTTPS、CI/CD、云服务",
            },
            {
                "name": "开发",
                "emoji": "💻",
                "description": "通用开发工具、GitHub项目、建站",
            },
            {
                "name": "创富",
                "emoji": "💰",
                "description": "创业、投资理财、运营、副业",
            },
            {"name": "文史哲", "emoji": "📜", "description": "历史、文学、哲学思想"},
            {
                "name": "感悟",
                "emoji": "💡",
                "description": "个人成长、心理疗愈、认知觉醒",
            },
            {"name": "运动", "emoji": "🏃", "description": "户外运动、徒步、自驾"},
            {"name": "生活技巧", "emoji": "🏠", "description": "健康养生、职场、社保"},
            {"name": "其他", "emoji": "📎", "description": "无法归类的"},
        ],
        "classify_rules": "",
        "paths": {
            "downloads": "./downloads",
            "output": "./output",
            "transcripts": "./output/transcripts",
            "notes": "./output/notes",
            "frames": "./output/frames",
        },
    }

    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # 深度合并：用户配置覆盖默认值
        _deep_merge(defaults, user_config)
        logger.info("配置已加载: {}", path)
    else:
        logger.warning("配置文件不存在: {}，使用默认配置", path)

    return defaults

def _sanitize(name: str, max_len: int = 40) -> str:
    """清理文件名，移除 Windows 不允许的字符。"""
    bad = '<>:"/\\|?*#'
    for c in bad:
        name = name.replace(c, "")
    name = " ".join(name.split())[:max_len].strip().rstrip(".")
    return name or "video"


def _format_segments(segments: list[dict]) -> list[dict]:
    """预处理分段数据，格式化时间戳供 Jinja2 模板使用。"""
    formatted = []
    for seg in segments:
        s = int(seg["start"])
        timestamp = f"{s // 60:02d}:{s % 60:02d}"
        end_s = int(seg["end"])
        formatted.append({
            "timestamp": timestamp,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        })
    return formatted


def _get_category_names(config: dict) -> list[str]:
    cats = config.get("categories", [])
    if cats and isinstance(cats[0], dict):
        return [c["name"] for c in cats]
    return cats


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典，override 覆盖 base。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def collect_videos(input_path: str) -> list[Path]:
    """收集所有待处理的视频文件（支持单文件或目录）。"""
    p = Path(input_path)

    if p.is_file():
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            return [p]
        logger.error("不支持的文件格式: {}", p.suffix)
        return []

    if p.is_dir():
        # 递归扫描所有子目录（适配 wx_channel 按作者分类的目录结构）
        videos = sorted(
            f
            for f in p.rglob("*")
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        )
        logger.info("递归扫描 {}，找到 {} 个视频文件", p, len(videos))
        return videos

    logger.error("路径不存在: {}", p)
    return []


def process_video(
    video_path: Path,
    config: dict,
    transcriber: Transcriber,
    ocr_processor: OCRProcessor,
    analyzer: ContentAnalyzer,
    env: Environment,
) -> dict:
    """处理单个视频的完整管道。

    Returns:
        {"status": "success"|"failed", "video": str, "category": str, "tags": list, "error": str|None}
    """
    video_name = video_path.stem
    author_name = (
        video_path.parent.name
        if video_path.parent.name != video_path.parent.parent.name
        else "未知"
    )
    title = video_name.rsplit("_", 1)[0] if "_" in video_name else video_name
    title = title[:80] if len(title) > 80 else title

    # 为每个视频创建隔离的输出目录
    safe_name = _sanitize(video_name)
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    run_dir = Path(config["paths"]["output"]) / run_id
    transcripts_dir = run_dir / "transcripts"
    frames_dir = run_dir / "frames"
    notes_dir = run_dir / "notes"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)
    logger.info("=" * 60)
    logger.info("开始处理: {} (作者: {})", title, author_name)

    try:
        # === 步骤 1: 提取音频 ===
        audio_path = str(run_dir / "audio.wav")
        extract_audio(str(video_path), audio_path)

        # === 步骤 2: 语音转文字 ===
        result = transcriber.transcribe(audio_path)
        transcript = result["text"]

        # === 步骤 3: 提取关键帧 ===
        frame_paths = extract_keyframes(
            str(video_path),
            str(frames_dir),
            threshold=config["frames"]["threshold"],
            max_frames=config["frames"]["max_frames"],
        )

        # === 步骤 4: OCR 识别 ===
        ocr_texts = ocr_processor.batch_extract(frame_paths)
        ocr_text = " ".join(t for t in ocr_texts if t)

        # === 步骤 5: 内容分类 ===
        combined_text = f"{transcript}\n{ocr_text}"
        category, tags = classify_content(
            combined_text,
            llm_client=analyzer.client,
            model=analyzer.model,
            categories=_get_category_names(config),
            classify_rules=config.get("classify_rules", ""),
        )

        # === 步骤 6: LLM 深度分析（有关键帧时启用视觉分析）===
        analysis = analyzer.analyze(transcript, ocr_text, category, tags, frame_paths)

        # === 步骤 7: 保存转录文稿 + 分段数据 ===
        transcript_file = transcripts_dir / f"{video_name}.txt"
        transcript_file.write_text(transcript, encoding="utf-8")
        segments_file = transcripts_dir / f"{video_name}_segments.json"
        import json as _json
        segments_data = result.get("segments", [])
        segments_file.write_text(
            _json.dumps(segments_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 保存标记文件
        (run_dir / "_run_id.txt").write_text(run_id)
        logger.info("转录文稿已保存: {} (+ {} 段)", transcript_file, len(segments_data))

        # === 步骤 8: 渲染 Obsidian 笔记 ===
        template = env.get_template("obsidian.md")
        note_content = template.render(
            title=title,
            author=author_name,
            date=datetime.now().strftime("%Y-%m-%d"),
            category=analysis["category"],
            tags=analysis["tags"],
            summary=analysis["summary"],
            visual_observations=analysis.get("visual_observations", ""),
            key_points=analysis["key_points"],
            resources=analysis["resources"],
            action_items=analysis["action_items"],
            transcript=transcript,
            segments=_format_segments(result.get("segments", [])),
        )

        note_file = notes_dir / f"{video_name}.md"
        note_file.write_text(note_content, encoding="utf-8")
        logger.success("Obsidian 笔记已生成: {}", note_file)

        return {
            "status": "success",
            "video": video_path.name,
            "author": author_name,
            "title": title,
            "category": category,
            "tags": tags,
            "note": str(note_file),
            "error": None,
        }

    except Exception as e:
        logger.exception("处理失败 [{}]: {}", video_path.name, e)
        return {
            "status": "failed",
            "video": video_path.name,
            "category": "",
            "tags": [],
            "note": "",
            "error": str(e),
        }


def print_report(results: list[dict]) -> None:
    """用 Rich 打印处理结果汇总报告。"""
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]

    console.print()
    console.print(
        Panel.fit(
            f"[bold green]✅ 成功: {len(succeeded)}[/]  [bold red]❌ 失败: {len(failed)}[/]  [bold]总计: {len(results)}[/]",
            title="📊 处理报告",
            border_style="blue",
        )
    )

    if succeeded:
        table = Table(title="成功处理的视频", show_lines=True)
        table.add_column("视频", style="cyan")
        table.add_column("作者", style="yellow")
        table.add_column("分类", style="magenta")
        table.add_column("标签", style="green")

        for r in succeeded:
            table.add_row(
                r.get("title", r["video"])[:40],
                r.get("author", ""),
                r["category"],
                ", ".join(r["tags"])[:50],
            )
        console.print(table)

    if failed:
        console.print("\n[bold red]失败的视频:[/]")
        for r in failed:
            console.print(f"  [red]✗[/] {r['video']}: {r['error']}")


def main() -> None:
    """主入口：解析参数，运行管道。"""
    parser = argparse.ArgumentParser(
        description="视频转知识笔记 — 将微信视频号收藏转为 Obsidian 笔记",
    )
    parser.add_argument(
        "input",
        help="视频文件路径或包含视频的目录",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    args = parser.parse_args()

    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )
    logger.add(
        "pipeline.log",
        rotation="10 MB",
        encoding="utf-8",
        level="DEBUG",
    )

    # 加载配置
    config = load_config(args.config)

    # 设置 CUDA 库路径（WSL 环境下 ctranslate2 需要显式指定）
    cuda_paths = config.get("cuda", {}).get("lib_paths", [])
    if cuda_paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        new_paths = ":".join(p for p in cuda_paths if Path(p).exists())
        if new_paths:
            os.environ["LD_LIBRARY_PATH"] = (
                f"{new_paths}:{existing}" if existing else new_paths
            )
            logger.info(
                "已注入 CUDA 库路径: {} 条",
                len([p for p in cuda_paths if Path(p).exists()]),
            )

    # 初始化组件
    transcriber = Transcriber(
        model_size=config["whisper"]["model_size"],
        device=config["whisper"]["device"],
        compute_type=config["whisper"]["compute_type"],
        fallback_model=config["whisper"].get("fallback_model"),
        max_duration_s=config["whisper"].get("max_duration_s", 300),
        ffmpeg_path=FFMPEG_PATH,
    )

    ocr_processor = OCRProcessor(
        confidence_threshold=config["ocr"]["confidence_threshold"],
    )

    analyzer = ContentAnalyzer(
        api_key=config["llm"]["api_key"],
        base_url=config["llm"]["base_url"],
        model=config["llm"]["model"],
    )

    # Jinja2 模板环境
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
    )

    # 收集视频文件
    videos = collect_videos(args.input)
    if not videos:
        console.print("[bold red]未找到可处理的视频文件[/]")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]找到 {len(videos)} 个视频文件[/]\n"
            f"模型: {config['whisper']['model_size']} | LLM: {config['llm']['model']}",
            title="🎬 视频转知识笔记",
            border_style="bright_blue",
        )
    )

    # 批量处理
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[current]}"),
        console=console,
    ) as progress:
        task = progress.add_task("处理视频...", total=len(videos), current="")

        for video in videos:
            progress.update(task, current=video.name)
            result = process_video(
                video, config, transcriber, ocr_processor, analyzer, env
            )
            results.append(result)
            progress.advance(task)

    # 打印报告
    print_report(results)

    # 处理完成后自动构建 Obsidian Vault
    if any(r["status"] == "success" for r in results):
        logger.info("开始构建 Obsidian Vault...")
        import subprocess

        build_result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "build_vault.py")],
            cwd=str(Path(__file__).parent),
        )
        if build_result.returncode == 0:
            logger.success("Obsidian Vault 构建完成")
        else:
            logger.error("Obsidian Vault 构建失败，可手动运行: python build_vault.py")

    # 退出码
    if all(r["status"] == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
