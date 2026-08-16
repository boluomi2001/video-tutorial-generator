"""从视频中提取音频（ffmpeg → 16kHz mono WAV）"""

import subprocess
from pathlib import Path

from loguru import logger


def extract_audio(video_path: str, output_path: str) -> str:
    """使用 ffmpeg 从视频中提取音频，输出为 16kHz 单声道 WAV（Whisper 最优格式）。"""
    video = Path(video_path)
    output = Path(output_path)

    if not video.exists():
        raise FileNotFoundError(f"视频文件不存在: {video}")

    output.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = r"D:\work\2026-07-12-13-31-07\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe"
    cmd = [
        ffmpeg_path,
        "-i",
        str(video),
        "-vn",  # 不要视频流
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",  # 16kHz 采样率
        "-ac",
        "1",  # 单声道
        "-y",  # 覆盖已存在文件
        str(output),
    ]

    logger.info("提取音频: {} → {}", video.name, output.name)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            logger.error("ffmpeg 错误: {}", result.stderr[-500:])
            raise RuntimeError(f"ffmpeg 提取音频失败: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg 超时（600s）: {video.name}")
    except FileNotFoundError:
        raise RuntimeError("未找到 ffmpeg，请先安装: sudo apt install ffmpeg")

    logger.success("音频提取完成: {}", output.name)
    return str(output)
