"""Step 2: Keyframe extraction + OCR + Qwen3-VL analysis (skip transcription)
Usage: python step2_analyze.py [run_id]
  - If run_id given: process that specific run
  - If no arg: list recent runs and let user choose
Output: output/<run_id>/notes/
"""
import sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml
import json as _json
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from modules.frames import extract_keyframes
from modules.ocr import OCRProcessor
from modules.analyzer import ContentAnalyzer
from modules.classifier import classify_content

def log(msg):
    print(f"[step2] {msg}", flush=True)

def sanitize(name: str, max_len: int = 40) -> str:
    bad = '<>:"/\\|?*#'
    for c in bad:
        name = name.replace(c, "")
    name = " ".join(name.split())[:max_len].strip().rstrip(".")
    return name or "video"

try:
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    DOWNLOADS = Path("downloads")
    OUTPUT = Path("output")

    # --- Step 0: Select run ---
    runs = sorted(
        [d for d in OUTPUT.iterdir() if d.is_dir() and (d / "_run_id.txt").exists()],
        key=lambda d: d.stat().st_mtime, reverse=True
    )

    if len(sys.argv) > 1:
        # User specified a run ID
        run_id = sys.argv[1]
        run_dir = OUTPUT / run_id
        if not run_dir.exists() or not (run_dir / "_run_id.txt").exists():
            log(f"ERROR: Run not found: {run_id}")
            sys.exit(1)
    else:
        if not runs:
            log("ERROR: No completed runs found. Run step1 first!")
            input("Press Enter..."); sys.exit(1)

        print("Available runs:")
        for i, r in enumerate(runs):
            print(f"  [{i}] {r.name}")

        if len(runs) == 1:
            choice = "0"
        else:
            choice = input(f"\nSelect run [0-{len(runs)-1}] (default=0): ").strip() or "0"

        try:
            run_dir = runs[int(choice)]
        except (ValueError, IndexError):
            log(f"Invalid choice")
            sys.exit(1)

    log(f"Run: {run_dir.name}")

    # --- Find video ---
    run_name = run_dir.name
    # Parse video name from run_id (format: YYYYMMDD_HHMMSS_video_name)
    video_stem = "_".join(run_name.split("_")[2:]) if "_" in run_name else run_name
    videos = list(DOWNLOADS.glob(f"{video_stem}*.mp4")) + list(DOWNLOADS.glob(f"{video_stem}*.mov"))
    # Fallback: try all mp4
    if not videos:
        videos = list(DOWNLOADS.glob("*.mp4")) + list(DOWNLOADS.glob("*.mov"))
    # Match by checking if run name contains the video stem
    matched = [v for v in videos if v.stem in run_name or run_name.endswith(v.stem)]
    if matched:
        video = matched[0]
    elif videos:
        video = videos[0]
    else:
        log("ERROR: Cannot find source video")
        sys.exit(1)
    log(f"Video: {video.name}")

    # --- Load transcript ---
    trans_dir = run_dir / "transcripts"
    transcript_file = trans_dir / f"{video.stem}.txt"
    if not transcript_file.exists():
        # Try any .txt file
        txt_files = list(trans_dir.glob("*.txt"))
        if txt_files:
            transcript_file = txt_files[0]
        else:
            log("ERROR: No transcript found")
            sys.exit(1)
    transcript = transcript_file.read_text(encoding="utf-8")
    log(f"Transcript: {len(transcript)} chars")

    # --- Load segments ---
    seg_file = trans_dir / f"{video.stem}_segments.json"
    if not seg_file.exists():
        seg_files = list(trans_dir.glob("*_segments.json"))
        if seg_files:
            seg_file = seg_files[0]
    segments = []
    if seg_file.exists():
        segments = _json.loads(seg_file.read_text(encoding="utf-8"))
        for seg in segments:
            s = int(seg["start"])
            seg["timestamp"] = f"{s // 60:02d}:{s % 60:02d}"
    log(f"Segments: {len(segments)}")

    # --- Extract keyframes ---
    frames_path = run_dir / "frames"
    frames_path.mkdir(parents=True, exist_ok=True)
    frame_paths = extract_keyframes(
        str(video), str(frames_path),
        threshold=config["frames"]["threshold"],
        max_frames=config["frames"]["max_frames"],
    )
    log(f"Keyframes: {len(frame_paths)}")

    # --- OCR ---
    ocr = OCRProcessor(confidence_threshold=config["ocr"]["confidence_threshold"])
    ocr_texts = ocr.batch_extract(frame_paths)
    ocr_text = " ".join(t for t in ocr_texts if t)
    log(f"OCR: {len(ocr_text)} chars")

    # --- Classify ---
    analyzer = ContentAnalyzer(
        api_key=config["llm"]["api_key"],
        base_url=config["llm"]["base_url"],
        model=config["llm"]["model"],
    )
    category_names = [c["name"] for c in config["categories"]]
    log("Classifying content...")
    category, tags = classify_content(
        transcript + "\n" + ocr_text,
        llm_client=analyzer.client,
        model=analyzer.model,
        categories=category_names,
        classify_rules=config.get("classify_rules", ""),
    )
    log(f"Category: {category} | Tags: {tags}")

    # --- Visual analysis ---
    log("Running Qwen3-VL-32B visual analysis...")
    analysis = analyzer.analyze(transcript, ocr_text, category, tags, frame_paths)
    log(f"Analysis: {len(analysis['key_points'])} points, {len(analysis['resources'])} resources")

    # --- Render note ---
    notes_dir = run_dir / "notes"
    notes_dir.mkdir(exist_ok=True)
    video_name = video.stem
    title = video_name.rsplit("_", 1)[0][:80] if "_" in video_name else video_name[:80]
    env = Environment(loader=FileSystemLoader("templates"), keep_trailing_newline=True)
    template = env.get_template("obsidian.md")
    note = template.render(
        title=title, author="video", date=datetime.now().strftime("%Y-%m-%d"),
        category=analysis["category"], tags=analysis["tags"],
        summary=analysis["summary"],
        visual_observations=analysis.get("visual_observations", ""),
        key_points=analysis["key_points"],
        resources=analysis["resources"],
        action_items=analysis["action_items"],
        transcript=transcript,
        segments=segments,
    )
    note_file = notes_dir / f"{video_name}.md"
    note_file.write_text(note, encoding="utf-8")
    log(f"DONE! Note: {note_file}")
    log(f"Category: {category} | Tags: {', '.join(tags)}")

except Exception as e:
    log(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

input("Press Enter to exit...")
