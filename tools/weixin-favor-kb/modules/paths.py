"""统一路径解析。

所有脚本都必须通过本模块拿路径，禁止硬编码绝对路径，方便别人 Clone 即用。

目录约定（可通过环境变量 PIPELINE_HOME 整体搬迁）：
    <REPO_ROOT>/
        tools/weixin-favor-kb/      ← TOOL_ROOT，流水线主体
        tools/ffmpeg/               ← ffmpeg 解压目录（可选）
        tools/wx_channels_download/ ← 视频下载器（可选）
        outputs/                    ← 笔记发布目录
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]        # tools/weixin-favor-kb
REPO_ROOT = Path(__file__).resolve().parents[3]        # 仓库根
DOWNLOADS = TOOL_ROOT / "downloads"
OUTPUT = TOOL_ROOT / "output"
PUBLISH_ROOT = REPO_ROOT / "outputs" / "公众号视频项目"

DOWNLOADER_DIR = REPO_ROOT / "tools" / "wx_channels_download"          # 推荐名称
DOWNLOADER_DIRS = [
    DOWNLOADER_DIR,
    REPO_ROOT / "tools" / "wx_channels_download_v260907",              # 带版本号的旧目录
]
FFMPEG_DIR = REPO_ROOT / "tools" / "ffmpeg"


def _candidate_ffmpeg_dirs() -> list[Path]:
    env_home = os.environ.get("FFMPEG_HOME")
    dirs = []
    if env_home:
        dirs.append(Path(env_home))
    dirs.append(FFMPEG_DIR)
    # 旧结构兼容：tools/weixin-favor-kb/tools/ffmpeg
    dirs.append(TOOL_ROOT / "tools" / "ffmpeg")
    return dirs


def resolve_ffmpeg(explicit: str | None = None) -> str:
    """定位 ffmpeg 可执行文件。

    优先级：显式参数 > FFMPEG_BIN 环境变量 > PATH > 仓库内置目录。
    """
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("FFMPEG_BIN"):
        candidates.append(os.environ["FFMPEG_BIN"])

    in_path = shutil.which("ffmpeg")
    if in_path:
        candidates.append(in_path)

    for d in _candidate_ffmpeg_dirs():
        if not d.exists():
            continue
        for pattern in ("**/bin/ffmpeg.exe", "**/bin/ffmpeg", "*/ffmpeg.exe"):
            found = sorted(d.glob(pattern))
            if found:
                candidates.extend(str(p) for p in found)
                break

    for c in candidates:
        try:
            if c and Path(c).exists():
                return str(Path(c))
        except OSError:
            continue

    return candidates[0] if candidates else "ffmpeg"


def ensure_dirs() -> None:
    for d in (DOWNLOADS, OUTPUT):
        d.mkdir(parents=True, exist_ok=True)
