"""从视频中提取关键帧（OpenCV HSV 直方图场景变化检测）"""

from pathlib import Path

import cv2
import numpy as np
from loguru import logger


def _compute_hsv_histogram(frame: cv2.typing.MatLike) -> np.ndarray:
    """计算 HSV 直方图特征向量，用于场景变化比较。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def extract_keyframes(
    video_path: str,
    output_dir: str,
    threshold: float = 30.0,
    max_frames: int = 20,
) -> list[str]:
    """从视频中提取关键帧，基于 HSV 直方图差异检测场景切换。

    Args:
        video_path: 视频文件路径
        output_dir: 关键帧输出目录
        threshold: HSV 直方图差异阈值，值越小越敏感
        max_frames: 最多提取帧数

    Returns:
        保存的关键帧文件路径列表
    """
    video = Path(video_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    logger.info("视频信息: {} 帧, {:.1f} FPS", total_frames, fps)

    saved_paths: list[str] = []
    prev_hist: np.ndarray | None = None
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        current_hist = _compute_hsv_histogram(frame)

        if prev_hist is None:
            # 第一帧总是保存
            path = _save_frame(frame, out_dir, len(saved_paths), frame_idx, fps)
            saved_paths.append(path)
            prev_hist = current_hist
            frame_idx += 1
            continue

        # 计算与上一关键帧的直方图相关性（越低差异越大）
        correlation = cv2.compareHist(
            prev_hist.reshape(-1, 1).astype(np.float32),
            current_hist.reshape(-1, 1).astype(np.float32),
            cv2.HISTCMP_CORREL,
        )
        diff_score = (1.0 - correlation) * 100.0

        if diff_score > threshold:
            path = _save_frame(frame, out_dir, len(saved_paths), frame_idx, fps)
            saved_paths.append(path)
            prev_hist = current_hist
            logger.debug("帧 #{}: 差异={:.1f}, 已保存", frame_idx, diff_score)

            if len(saved_paths) >= max_frames:
                logger.info("已达到最大帧数限制: {}", max_frames)
                break

        frame_idx += 1

    cap.release()
    logger.success("关键帧提取完成: 共 {} 帧", len(saved_paths))
    return saved_paths


def _save_frame(
    frame: cv2.typing.MatLike,
    out_dir: Path,
    key_idx: int,
    frame_idx: int,
    fps: float,
) -> str:
    """保存单帧为 JPG 文件（绕过 cv2.imwrite 中文路径问题）。"""
    timestamp = frame_idx / fps
    filename = f"keyframe_{key_idx:03d}_t{timestamp:.1f}s.jpg"
    filepath = out_dir / filename
    # cv2.imwrite 在 Windows 上遇到中文路径会静默失败，
    # 改用 imencode + 二进制写入来规避
    result, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if result:
        filepath.write_bytes(encoded.tobytes())
    else:
        logger.warning("关键帧编码失败: {}", filepath.name)
    return str(filepath)
