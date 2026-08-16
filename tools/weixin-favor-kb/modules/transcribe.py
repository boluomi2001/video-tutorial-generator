"""Faster-Whisper 语音转文字（中文，自带模型自动降级）"""

import glob
import json
import os
import subprocess
from pathlib import Path

from loguru import logger

CUDA_FALLBACK_DIRS = [
    # 修改为你的 NVIDIA CUDA 库路径
    # "/path/to/your/venv/lib/python3.12/site-packages/nvidia",
]


def _ensure_cuda_libs() -> None:
    """WSL 环境下 ctranslate2 可能找不到 CUDA 库，提前注入 LD_LIBRARY_PATH。"""
    if os.environ.get("_CUDA_LIBS_INJECTED"):
        return

    extra: list[str] = []
    for base in CUDA_FALLBACK_DIRS:
        if Path(base).exists():
            for lib_dir in glob.glob(f"{base}/*/lib"):
                if Path(lib_dir).exists():
                    extra.append(lib_dir)

    if extra:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = ":".join(extra) + (
            f":{existing}" if existing else ""
        )
        logger.debug("注入 CUDA 库路径: {} 条", len(extra))

    os.environ["_CUDA_LIBS_INJECTED"] = "1"


class Transcriber:
    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        fallback_model: str | None = None,
        max_duration_s: int = 300,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self.model_size = model_size
        self.fallback_model = fallback_model
        self.max_duration_s = max_duration_s
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._active_model_size = model_size
        self._ffmpeg_path = ffmpeg_path

    def _get_audio_duration(self, audio_path: str) -> float:
        """用 ffprobe 获取音频时长（秒）。"""
        import locale
        ffprobe = str(Path(self._ffmpeg_path).parent / "ffprobe.exe")
        if not Path(ffprobe).exists():
            ffprobe = "ffprobe"
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "quiet", "-print_format", "json",
                    "-show_format", audio_path,
                ],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            info = json.loads(result.stdout)
            return float(info["format"]["duration"])
        except Exception as e:
            logger.warning("ffprobe 无法获取时长: {}", str(e)[:100])
            return 0.0

    def _select_model(self, audio_duration: float) -> str:
        """根据音频时长自动选择模型。"""
        if self.fallback_model and audio_duration > self.max_duration_s:
            logger.info(
                "音频时长 {:.0f}s > {}s 阈值，切换: {} → {}",
                audio_duration, self.max_duration_s,
                self.model_size, self.fallback_model,
            )
            return self.fallback_model
        return self.model_size

    def _load_model(self) -> None:
        if self._model is not None:
            return

        _ensure_cuda_libs()

        from faster_whisper import WhisperModel

        # 优先使用本地模型目录，避免网络下载
        local_model_dir = (
            Path(__file__).parent.parent / "models" / f"whisper-{self.model_size}"
        )
        if local_model_dir.exists() and (local_model_dir / "model.bin").exists():
            logger.info(
                "加载本地 Whisper 模型: {}",
                local_model_dir,
            )
            self._model = WhisperModel(
                str(local_model_dir),
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
        else:
            logger.info(
                "加载 Whisper 模型: size={}, device={}, compute_type={}",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        logger.success("Whisper 模型加载完成")

    def transcribe(self, audio_path: str) -> dict:
        audio = Path(audio_path)
        if not audio.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio}")

        # 检测时长，自动选模型
        duration = self._get_audio_duration(str(audio))
        self._active_model_size = self._select_model(duration)
        self._load_model()

        logger.info("开始转录: {} ({:.0f}s, 模型={})", audio.name, duration, self._active_model_size)

        segments_iter, info = self._model.transcribe(
            str(audio),
            language="zh",
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
        )

        segments: list[dict] = []
        full_text_parts: list[str] = []
        seg_count = 0
        last_log_time = 0.0

        for seg in segments_iter:
            segment_dict = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            segments.append(segment_dict)
            full_text_parts.append(seg.text.strip())
            seg_count += 1

            # 每 30 秒或每 10 个片段显示一次进度
            if seg_count % 10 == 0 or seg.end - last_log_time > 30:
                last_log_time = seg.end
                logger.info(
                    "转录进度: {:>4} 段 | {:.0f}s / ~{:.0f}s ({:>5} 字)",
                    seg_count,
                    seg.end,
                    info.duration if hasattr(info, 'duration') and info.duration else 0,
                    len("".join(full_text_parts)),
                )

        full_text = "".join(full_text_parts)
        logger.success("转录完成: {} 个片段, 共 {} 字", len(segments), len(full_text))

        return {"text": full_text, "segments": segments}
