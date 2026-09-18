"""关键帧提取：按时长定预算 + 分桶保覆盖 + 桶内打分择优 + 类型自适应。

设计要点：
1. 预算 = clamp(10 + 8 * ln(1 + 分钟数), 12, 36)，短视频给相对更高密度。
2. 视频按帧数均分成 N 个桶，每桶必出 1 帧，消灭「抽满上限后半段无画面」。
3. 桶内按四信号打分：边缘/文字密度、桶内帧间差异、与已选帧差异、人脸占比惩罚。
4. 预扫描判定画面类型（代码终端 / 幻灯片 / 口播），动态调整预算与权重。
5. 允许保留 2 张「相似但状态不同」的帧（如改代码前 / 后）。
6. 候选帧用 ffmpeg 抽 1fps 低分辨率图做打分，选中帧再按时间戳提原分辨率，
   避免逐帧解码全片带来的分钟级耗时。
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

MIN_FRAMES = 12
MAX_FRAMES = 36
CANDIDATE_FPS = 1
CANDIDATE_WIDTH = 480

# 打分权重（会被类型自适应调整）
BASE_WEIGHTS = {
    "edge": 0.45,      # 边缘/文字密度：代码、终端、文档画面得分高
    "novelty": 0.35,   # 桶内帧间差异：抓变化瞬间
    "distinct": 0.20,  # 与已选帧的差异：去重
    "face_penalty": 0.25,  # 人脸占比惩罚：纯口播画面降权
}


def resolve_ffmpeg() -> str:
    """定位 ffmpeg 可执行文件（统一走 modules.paths）。"""
    from modules.paths import resolve_ffmpeg as _resolve  # 延迟导入避免循环

    return _resolve()


def plan_frame_budget(duration_s: float) -> int:
    """按时长用对数曲线计算帧数预算。

    短视频信息密度高，给相对更高的采样密度；长视频边际收益递减。
    """
    minutes = max(duration_s, 1.0) / 60.0
    n = 10 + 8 * math.log(1 + minutes)
    return int(max(MIN_FRAMES, min(MAX_FRAMES, round(n))))


def _edge_density(gray: np.ndarray) -> float:
    """归一化边缘密度，代码/文字画面显著偏高。"""
    edges = cv2.Canny(gray, 50, 150)
    return float(edges.mean()) / 255.0


def _dhash(gray: np.ndarray) -> np.ndarray:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    return diff.flatten()


def _hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


def _face_ratio(gray: np.ndarray, cascade) -> float:
    if cascade is None:
        return 0.0
    faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(48, 48))
    if len(faces) == 0:
        return 0.0
    area = sum(int(w) * int(h) for (_, _, w, h) in faces)
    return min(1.0, area / float(gray.shape[0] * gray.shape[1]))


def _load_face_cascade():
    try:
        path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if path.exists():
            return cv2.CascadeClassifier(str(path))
    except Exception:
        pass
    return None


def _probe_video(video_path: str) -> tuple[float, float]:
    """返回 (时长秒, FPS)。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    duration = total / fps if total > 0 else 0.0
    if duration <= 0:
        duration = _duration_via_ffmpeg(video_path)
    return duration, fps


def _duration_via_ffmpeg(video_path: str) -> float:
    ff = resolve_ffmpeg()
    try:
        out = subprocess.run(
            [ff, "-i", video_path], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=30,
        )
        for line in (out.stderr or "").splitlines():
            if "Duration:" in line:
                part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = part.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _extract_candidates_ffmpeg(video_path: str) -> list[tuple[float, np.ndarray]]:
    """用 ffmpeg 按 1fps 抽低分辨率候选帧，直接在内存中解码。

    不落盘的原因：候选帧常有上百张，写成文件后清理时会触发批量删除保护，
    且磁盘 IO 比内存慢。用 image2pipe 输出 mjpeg 流后按 JPEG 起止标记切分。
    """
    cmd = [
        resolve_ffmpeg(), "-y", "-i", video_path,
        "-vf", f"fps={CANDIDATE_FPS},scale={CANDIDATE_WIDTH}:-1",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        data = proc.stdout
    except Exception as e:
        logger.warning("ffmpeg 候选帧抽取失败: {}", e)
        return []

    items: list[tuple[float, np.ndarray]] = []
    pos = 0
    while True:
        start = data.find(b"\xff\xd8", pos)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start + 2)
        if end < 0:
            break
        img = cv2.imdecode(np.frombuffer(data[start:end + 2], dtype=np.uint8),
                           cv2.IMREAD_COLOR)
        if img is not None:
            items.append((len(items) / float(CANDIDATE_FPS), img))
        pos = end + 2
    return items


def prescan_profile(candidates: list[tuple[float, Path]]) -> dict:
    """预扫描候选帧，判定画面类型并返回统计特征。"""
    if not candidates:
        return {"type": "unknown", "edge": 0.0, "motion": 0.0, "face": 0.0}

    cascade = _load_face_cascade()
    edges: list[float] = []
    motions: list[float] = []
    faces: list[float] = []
    prev: np.ndarray | None = None

    step = max(1, len(candidates) // 60)  # 预扫描最多取 60 个样本，控制耗时
    for _, img in candidates[::step]:
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cur = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
        edges.append(_edge_density(gray))
        faces.append(_face_ratio(gray, cascade))
        if prev is not None:
            motions.append(float(np.abs(cur.astype(np.int16) - prev.astype(np.int16)).mean()) / 255.0)
        prev = cur

    if not edges:
        return {"type": "unknown", "edge": 0.0, "motion": 0.0, "face": 0.0}

    e = float(np.mean(edges))
    m = float(np.mean(motions)) if motions else 0.0
    f = float(np.mean(faces))

    if f > 0.25 and m < 0.05:
        vtype = "talking"
    elif e > 0.06:
        vtype = "code"
    elif m > 0.12:
        vtype = "slides"
    else:
        vtype = "general"

    return {"type": vtype, "edge": e, "motion": m, "face": f}


def _imread_any(path: Path) -> np.ndarray | None:
    """读取图片，兼容中文路径。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def adapt_plan(budget: int, profile: dict) -> tuple[int, dict]:
    """按画面类型调整帧数预算与打分权重。"""
    weights = dict(BASE_WEIGHTS)
    vtype = profile.get("type", "general")

    if vtype == "code":
        budget = int(round(budget * 1.2))
        weights["edge"] = 0.60
        weights["novelty"] = 0.25
    elif vtype == "slides":
        weights["novelty"] = 0.55
        weights["edge"] = 0.30
    elif vtype == "talking":
        budget = int(round(budget * 0.7))
        weights["edge"] = 0.30
        weights["face_penalty"] = 0.40

    return int(max(MIN_FRAMES, min(MAX_FRAMES, budget))), weights


def _save_frame_at(video_path: str, timestamp: float, out_path: Path) -> bool:
    """按时间戳从原视频提取一帧原分辨率图像。"""
    cmd = [
        resolve_ffmpeg(), "-y", "-ss", f"{timestamp:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "2", str(out_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as e:
        logger.warning("提帧失败 t={}: {}", timestamp, e)
    return out_path.exists() and out_path.stat().st_size > 0


def _imwrite_any(path: Path, img: np.ndarray) -> None:
    """写入图片，兼容中文路径。"""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
        path.write_bytes(buf.tobytes())


def extract_keyframes(
    video_path: str,
    output_dir: str,
    max_frames: int = MAX_FRAMES,
    allow_twin: bool = True,
) -> list[str]:
    """分桶择优抽帧。

    Returns:
        选中的关键帧路径列表（按时间顺序）。
        同时写出 frames_meta.json，包含每帧的时间戳、分数、所属桶。
    """
    video = Path(video_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    duration, fps = _probe_video(str(video))
    if duration <= 0:
        raise RuntimeError(f"无法获取视频时长: {video}")

    budget = plan_frame_budget(duration)
    logger.info("视频时长 {:.0f}s / {:.1f}fps，基础帧数预算 {}", duration, fps, budget)

    candidates = _extract_candidates_ffmpeg(str(video))
    if not candidates:
        logger.warning("ffmpeg 候选帧抽取失败，回退 OpenCV 逐帧采样")
        candidates = _fallback_candidates(str(video), fps)

    profile = prescan_profile(candidates)
    budget, weights = adapt_plan(budget, profile)
    budget = min(budget, max_frames)
    logger.info(
        "画面类型={} 边缘={:.3f} 动态={:.3f} 人脸={:.3f} → 调整后帧数 {}",
        profile["type"], profile["edge"], profile["motion"], profile["face"], budget,
    )

    selected = _select_by_bucket(
        candidates, budget, weights, allow_twin=allow_twin
    )

    saved: list[str] = []
    meta: list[dict] = []
    cascade = _load_face_cascade()
    for idx, item in enumerate(selected):
        ts = item["timestamp"]
        name = f"keyframe_{idx:03d}_t{ts:.1f}s.jpg"
        target = out_dir / name
        if not _save_frame_at(str(video), ts, target):
            logger.warning("关键帧提取失败，跳过 t={}", ts)
            continue
        saved.append(str(target))
        meta.append({
            "index": idx,
            "file": name,
            "timestamp": round(ts, 2),
            "score": round(item["score"], 4),
            "bucket": item["bucket"],
            "candidate_ts": round(item["candidate_ts"], 2),
        })

    _write_meta(out_dir, meta, duration, budget, profile)

    logger.success("关键帧提取完成: {}/{} 帧，类型={}", len(saved), budget, profile["type"])
    return saved


def _fallback_candidates(video_path: str, fps: float) -> list[tuple[float, np.ndarray]]:
    """ffmpeg 不可用时的兜底：OpenCV 按 1 秒间隔采样（同样只在内存中）。"""
    cap = cv2.VideoCapture(video_path)
    step = max(1, int(round(fps)))
    items: list[tuple[float, np.ndarray]] = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % step == 0:
            items.append((idx / fps, frame))
        idx += 1
    cap.release()
    return items


def _select_by_bucket(
    candidates: list[tuple[float, Path]],
    budget: int,
    weights: dict,
    allow_twin: bool = True,
) -> list[dict]:
    """把候选帧按时间均分成 budget 个桶，每桶选取得分最高（及次优）的帧。"""
    if not candidates:
        return []

    # 桶数留出 20% 余量，给「相似但状态不同」的孪生帧
    n_bucket = max(1, min(int(budget * 0.8), len(candidates)))
    size = len(candidates) / n_bucket
    cascade = _load_face_cascade()

    # 预计算候选帧特征
    feats: list[dict] = []
    prev_small: np.ndarray | None = None
    for ts, img in candidates:
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
        novelty = 0.0
        if prev_small is not None:
            novelty = float(np.abs(small.astype(np.int16) - prev_small.astype(np.int16)).mean()) / 255.0
        prev_small = small
        feats.append({
            "ts": ts,
            "edge": _edge_density(gray),
            "novelty": novelty,
            "face": _face_ratio(gray, cascade),
            "hash": _dhash(gray),
        })

    if not feats:
        return []

    max_edge = max(f["edge"] for f in feats) or 1.0
    max_nov = max(f["novelty"] for f in feats) or 1.0

    selected: list[dict] = []
    selected_hashes: list[np.ndarray] = []

    for b in range(n_bucket):
        lo = int(b * size)
        hi = int((b + 1) * size) if b < n_bucket - 1 else len(feats)
        bucket = feats[lo:hi]
        if not bucket:
            continue

        scored = []
        for f in bucket:
            distinct = 1.0
            if selected_hashes:
                d = min(_hamming(f["hash"], h) for h in selected_hashes) / 64.0
                distinct = min(1.0, d)
            score = (
                weights["edge"] * (f["edge"] / max_edge)
                + weights["novelty"] * (f["novelty"] / max_nov)
                + weights["distinct"] * distinct
                - weights["face_penalty"] * f["face"]
            )
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        selected.append({
            "timestamp": best["ts"],
            "candidate_ts": best["ts"],
            "score": best_score,
            "bucket": b,
        })
        selected_hashes.append(best["hash"])

        # 允许保留 1 张「相似但状态不同」的帧（如改代码前 / 后）
        if allow_twin and len(scored) > 1 and len(selected) < budget:
            twin_score, twin = scored[1]
            d = _hamming(twin["hash"], best["hash"])
            if 2 <= d <= 20 and twin_score > best_score * 0.6:
                selected.append({
                    "timestamp": twin["ts"],
                    "candidate_ts": twin["ts"],
                    "score": twin_score,
                    "bucket": b,
                })
                selected_hashes.append(twin["hash"])

    selected.sort(key=lambda x: x["timestamp"])
    return selected[:budget]


def _write_meta(out_dir: Path, meta: list[dict], duration: float, budget: int, profile: dict) -> None:
    import json

    data = {
        "duration": round(duration, 2),
        "budget": budget,
        "profile": profile,
        "frames": meta,
    }
    try:
        (out_dir / "frames_meta.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("写入 frames_meta.json 失败: {}", e)
