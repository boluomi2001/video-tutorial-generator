# 流水线执行命令参考

本文件记录视频解析流水线的精确命令、路径和环境配置。

## 路径常量

```
项目根:   D:\work\2026-07-12-13-31-07
工具根:   D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb
Python:   <工具根>\venv\Scripts\python.exe
下载目录: <工具根>\downloads\
输出根:   <工具根>\output\
ffmpeg:   D:\work\2026-07-12-13-31-07\tools\ffmpeg\ffmpeg-8.1.2-essentials_build\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe
wx_channel:D:\work\2026-07-12-13-31-07\tools\wx_channel\wx_channel.exe
```

## 环境变量（Windows 必设）

```
PYTHONIOENCODING=utf-8
PYTHONLEGACYWINDOWSSTDIO=utf-8
```

## 阶段 0：下载视频

```bash
# 微信视频号收藏 → 用 wx_channel.exe（GUI 工具，用户手动操作导出到 downloads/）

# 直链 mp4
curl -L -o "downloads/<name>.mp4" "<url>"

# 复制本地文件
cp "<本地路径>" "downloads/"
```

## 阶段 1：转录（独立进程，勿用后台任务）

```powershell
# 方式一：双击 step1_transcribe.cmd（会交互式列出视频供选择）
# 方式二：命令行指定视频
$env:PYTHONIOENCODING="utf-8"
Set-Location "<工具根>"
& ".\venv\Scripts\python.exe" -u "transcribe_only.py" "downloads\<视频名>.mp4"
```

产出（隔离目录 `output\<时间戳>_<安全文件名>\`）：
- `transcripts\<视频名>.txt`
- `transcripts\<视频名>_segments.json`
- `audio.wav`

## 阶段 2：分析（2-3 分钟）

```powershell
$env:PYTHONIOENCODING="utf-8"
Set-Location "<工具根>"
& ".\venv\Scripts\python.exe" -u "step2_analyze.py" "<run_id>"
```

`<run_id>` 是阶段 1 输出的目录名（如 `20260715_143548_...`）。

产出：
- `frames/` 关键帧
- `notes\<视频名>.md` 结构化笔记

## 模型配置（<工具根>\config.yaml）

```yaml
whisper:
  model_size: "medium"      # 首选；长视频自动降级 small
  fallback_model: "small"
  max_duration_s: 300       # 超 5 分钟自动切 small
  device: "cpu"
  compute_type: "int8"
llm:
  model: "Qwen/Qwen3-VL-32B-Instruct"
  base_url: "https://api.siliconflow.cn/v1"
```

## 关键坑（务必遵守）

1. 后台任务 ~10 分钟超时 → 转录必须用 `.cmd`/独立进程。
2. 中文路径 + cv2.imwrite 会失败 → 关键帧用 `np.fromfile` 保存。
3. 文件名含 `# ! ? * < > : " / \ |` → 先 `sanitize()` 再建目录。
4. 富文本库（rich）emoji 在 GBK 终端崩溃 → 脚本用纯 print + flush，且设 UTF-8。
5. Whisper 模型本地在 `models/whisper-small/` 和 `models/whisper-medium/`，缺模型时从 ModelScope 下载（hf-mirror 太慢）。
