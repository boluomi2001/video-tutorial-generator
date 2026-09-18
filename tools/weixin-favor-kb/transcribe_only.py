"""Step 1: Audio extraction + Whisper transcription (small model)
Usage: python transcribe_only.py [video_path]
  - If video_path given: process that video only
  - If no arg: list available videos and let user choose
Output: output/<run_id>/transcripts/
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from datetime import datetime
from modules.audio import extract_audio
from modules.transcribe import Transcriber
from modules.paths import resolve_ffmpeg

FFMPEG = resolve_ffmpeg()
DOWNLOADS = Path(__file__).parent / "downloads"

def sanitize(name: str, max_len: int = 40) -> str:
    """清理文件名，移除 Windows 不允许的字符。"""
    bad = '<>:"/\\|?*#'
    for c in bad:
        name = name.replace(c, "")
    # 替换连续空格和尾部点
    name = " ".join(name.split())[:max_len].strip().rstrip(".")
    return name or "video"

# --- Step 0: Select video ---
videos = sorted(list(DOWNLOADS.glob("*.mp4")) + list(DOWNLOADS.glob("*.mov")),
                key=lambda p: p.stat().st_mtime, reverse=True)

if len(sys.argv) > 1:
    # User specified a video path
    specified = Path(sys.argv[1])
    if specified.exists():
        video = specified
    elif (DOWNLOADS / sys.argv[1]).exists():
        video = DOWNLOADS / sys.argv[1]
    else:
        print(f"ERROR: Video not found: {sys.argv[1]}")
        sys.exit(1)
else:
    # List videos and let user choose
    if not videos:
        print("ERROR: No video found in downloads/")
        input("Press Enter...")
        sys.exit(1)

    print("Available videos:")
    for i, v in enumerate(videos):
        size_mb = v.stat().st_size / 1024 / 1024
        print(f"  [{i}] {v.name} ({size_mb:.0f} MB)")

    if len(videos) == 1:
        choice = "0"
    else:
        choice = input(f"\nSelect video [0-{len(videos)-1}] (default=0): ").strip() or "0"

    try:
        video = videos[int(choice)]
    except (ValueError, IndexError):
        print(f"Invalid choice: {choice}")
        sys.exit(1)

print(f"\nSelected: {video.name}")
print(f"Size: {video.stat().st_size / 1024 / 1024:.0f} MB")

# --- Create isolated output directory ---
safe_name = sanitize(video.stem)
run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
run_dir = Path("output") / run_id
transcripts_dir = run_dir / "transcripts"
transcripts_dir.mkdir(parents=True, exist_ok=True)
print(f"Output: {run_dir}")

# --- Step 1: Extract audio ---
audio = run_dir / "audio.wav"
if audio.exists() and audio.stat().st_size > 1000:
    print(f"Audio cached: {audio.stat().st_size / 1024 / 1024:.0f} MB")
else:
    print("Extracting audio...")
    extract_audio(str(video), str(audio))
    print(f"Done: {audio.stat().st_size / 1024 / 1024:.0f} MB")

# --- Step 2: Transcribe ---
print("\nLoading Whisper small model...")
t = Transcriber(model_size="small", device="cpu", compute_type="int8", ffmpeg_path=FFMPEG)

print("Transcribing (this may take 20-30 min for long videos)...")
result = t.transcribe(str(audio))

# --- Step 3: Save ---
import json as _json
out_file = transcripts_dir / f"{video.stem}.txt"
out_file.write_text(result["text"], encoding="utf-8")
seg_file = transcripts_dir / f"{video.stem}_segments.json"
seg_file.write_text(
    _json.dumps(result.get("segments", []), ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# Save a marker file for step2 to find
marker = run_dir / "_run_id.txt"
marker.write_text(run_id)

print(f"\n=== DONE ===")
print(f"Run ID: {run_id}")
print(f"Chars: {len(result['text'])}")
print(f"Segments: {len(result.get('segments', []))}")
print(f"\nRun:  step2_analyze.cmd {run_id}")
input("Press Enter to exit...")
