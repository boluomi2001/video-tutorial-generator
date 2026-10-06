# 流水线执行命令参考

## 路径常量

```
项目根:   D:\work\2026-07-12-13-31-07
工具根:   D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb
Python:   <工具根>\venv\Scripts\python.exe
下载目录: <工具根>\downloads\
输出根:   <工具根>\output\
笔记落盘: <项目根>\outputs\_新版笔记\
ffmpeg:   <项目根>\tools\ffmpeg\...\bin\ffmpeg.exe（frames.resolve_ffmpeg() 自动定位）
wx_channel: <项目根>\tools\wx_channel\wx_channel.exe
```

## 环境变量（Windows 必设）

```
PYTHONIOENCODING=utf-8
PYTHONLEGACYWINDOWSSTDIO=utf-8
```

## ★ 首选命令：Agent 写笔记模式（纯本地 · 零 API 成本）

```powershell
# 启动器（已固定 venv python + 工作目录 + 编码）
& "<工具根>\run_agent.cmd" "<视频号链接或本地文件路径>"
# 等价
Set-Location "<工具根>"; & ".\venv\Scripts\python.exe" -u "auto_run.py" --agent-write "<链接>"
```

**批量**：
```powershell
& "<工具根>\run_agent.cmd" "链接1" "链接2"
& "<工具根>\run_agent.cmd" --from-file links.txt
& "<工具根>\run_agent.cmd" --from-file links.txt --resume output\batch_<时间戳>
```

**保留付费视觉模型**（仅无人值守超大批量才用）：
```powershell
& "<工具根>\run_agent.cmd" --with-vision "<链接>"
```

**Agent 必须用脱离进程启动**（转录 20-30 分钟，前台/平台任务会被超时清理）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [r".\venv\Scripts\python.exe", "-u", "auto_run.py", "--agent-write", source],
    cwd=r"<工具根>", creationflags=F, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

进度轮询：`output\<run_id>\progress.json`
字段：`stage / percent / message / done / error / notes`
`stage`：`init → download → transcribe → frames → ocr → analyze → write → cleanup`

**跑完读**：`output\<run_id>\notes\agent_input.json`（字段说明见 SKILL.md §10）。

## 传统模式命令（备选）

```powershell
& "<工具根>\run_auto.cmd" "<链接>"          # 不带 --agent-write
& "<工具根>\run_auto.cmd" --no-publish "<链接>"
```

产出三形态笔记 + 整合稿 + `raw.json` / `cost.json` + `.ima_pending.json`。

## 旧的两步脚本（兜底）

```powershell
# 步骤1 转录
& ".\venv\Scripts\python.exe" -u "transcribe_only.py" "downloads\<视频>.mp4"
# 步骤2 分析
& ".\venv\Scripts\python.exe" -u "step2_analyze.py" "<run_id>"
```

> ⚠️ 2026-10-06 修复：`step2_analyze.py` 原先调用
> `extract_keyframes(..., threshold=..., ...)`，但该函数**没有 `threshold` 形参** →
> 必然 `TypeError`（等于这条兜底路径是坏的）。已删除该参数。
> 其余调用（`analyze()` / `classify_content()` / `templates/obsidian.md`）均已核实存在且签名匹配。

## ima 上传辅助脚本

```bash
# 单文件上传 COS（ima 三步链路的第 2 步）；从 stdin 读一行 JSON 凭证
python scripts/_ima_up2/up_one.py "<本地 md 绝对路径>" << 'JSON'
{"token":"…","secret_id":"…","secret_key":"…","region":"ap-shanghai",
 "bucket_name":"ima-media-prod-1258344701","cos_key":"…","media_id":"…"}
JSON
```

> `scripts/_ima_up*/**.json` 含 COS 临时密钥，**已在 `.gitignore`，严禁入库**。

## wx_channel 本地服务 API

服务地址 `http://127.0.0.1:2025`，无鉴权，**必须用无代理的 opener 访问**
（系统代理会返回 502）。

```
GET  /api/status              服务与客户端状态
POST /api/channels/parse_sph  {"url": "..."}  解析视频号链接
POST /api/search/parse_sph    同上（备用路由）
POST /api/v1/queue            {"videos": [...]}  提交下载队列
GET  /api/v1/queue            队列状态
GET  /api/v1/system/info      系统信息
WS   ws://127.0.0.1:2026      download_progress / download_complete 事件
```

**解析依赖登录态**，需满足其一：
1. `tools/wx_channel/config.yaml` 配好 `cloudflare.sphHostname` + `cloudflare.sphCookie`
2. GUI 内置浏览器已登录视频号并作为 client 连接

否则返回：
```json
{"code":400,"message":"cloudflare.sphHostname or cloudflare.sphCookie not configured"}
```

## 模型配置（config.yaml）

```yaml
whisper:
  model_size: "medium"      # 音频 >300s 自动切 small
  fallback_model: "small"
  max_duration_s: 300
  device: "cpu"
  compute_type: "int8"
frames:
  threshold: 30.0           # ⚠️ 死配置（代码未使用）
  max_frames: 20            # ⚠️ 仅 step2_analyze.py 生效；主链路实际上限是 36
llm:
  model: "Qwen/Qwen3-VL-30B-A3B-Instruct"  # 视觉 / 教程，MoE 实测 13-19s 每批
  fast_model: "Qwen/Qwen2.5-7B-Instruct"   # 分类 / 速览 / 清单 / 自检，约 0.5s
  base_url: "https://api.siliconflow.cn/v1"
```

> **Agent 模式（默认）完全不读 `llm` 段** —— 连客户端都不构造，`cost.json` 也不会生成。

可用视觉模型（/v1/models 实测）：
```
Qwen/Qwen3-VL-30B-A3B-Instruct   ★ 推荐，13-19s
Qwen/Qwen3-VL-8B-Instruct        5-9s，内容偏简
Qwen/Qwen3-VL-32B-Instruct       73-120s，常超时
Qwen/Qwen2.5-VL-32B-Instruct     ✗ 403 已禁用
```

## 关键坑（务必遵守）

1. **脱离进程**：转录 20-30 分钟，前台或平台后台任务会被清理，必须用 DETACHED_PROCESS。
2. **图片必须压缩**（传统模式）：1080×1920 原图 8 张 base64 后约 3MB，模型会超时；
   送检前压到长边 1024 / 质量 85。
3. **无代理请求本地服务**：`urllib.request.build_opener(ProxyHandler({}))`，否则 502。
4. **中文路径**：`cv2.imwrite` 会静默失败，用 `np.fromfile` / `imdecode` / `imencode` 绕过。
5. **文件名 sanitize**：去 `# ! ? * < > : " / \ |` 再建目录。
6. **Whisper 模型**：本地在 `models/whisper-{small,medium}/`，缺失从 ModelScope 下载。
7. **终端编码**：`PYTHONIOENCODING=utf-8`，否则 GBK 崩溃。
8. **事实抽取会超时**（传统模式）：输入过大时 `facts` 120s 超时 ×3 并降级为正则结果，
   视觉观察被整段丢弃 —— 所以**转录 + OCR 才是稳定源**。
9. **抽帧上限是 36**（不是 config 里的 20）；列举型 / 无口播视频密度可能不够，
   由 Agent 判断后补采（见 SKILL.md §11.3）。
