# 流水线执行命令参考

## 路径常量

```
项目根:   <项目根>
工具根:   <项目根>\tools\weixin-favor-kb
Python:   <工具根>\venv\Scripts\python.exe
下载目录: <工具根>\downloads\
输出根:   <工具根>\output\
ffmpeg:   <项目根>\tools\ffmpeg\...\bin\ffmpeg.exe（frames.resolve_ffmpeg() 自动定位）
wx_channel: <项目根>\tools\wx_channel\wx_channel.exe
```

## 环境变量（Windows 必设）

```
PYTHONIOENCODING=utf-8
PYTHONLEGACYWINDOWSSTDIO=utf-8
```

## 一键命令（首选）

```powershell
Set-Location "<项目根>\tools\weixin-favor-kb"
$env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python.exe" -u "auto_run.py" "<链接或文件路径>"
```

Agent 必须用脱离进程启动（否则转录会被超时清理）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [r".\venv\Scripts\python.exe", "-u", "auto_run.py", source],
    cwd=r"<项目根>\tools\weixin-favor-kb",
    creationflags=F, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

进度轮询：`output\<run_id>\progress.json`
字段：`stage / percent / message / done / error / notes`

## 旧的两步脚本（保留，仅作兜底）

```powershell
# 步骤1 转录
& ".\venv\Scripts\python.exe" -u "transcribe_only.py" "downloads\<视频>.mp4"
# 步骤2 分析
& ".\venv\Scripts\python.exe" -u "step2_analyze.py" "<run_id>"
```

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

**解析依赖登录态**，需要满足其一：
1. `tools/wx_channel/config.yaml` 配置 `cloudflare.sphHostname` + `cloudflare.sphCookie`
2. GUI 内置浏览器已登录视频号并作为 client 连接

否则返回：
```json
{"code":400,"message":"cloudflare.sphHostname or cloudflare.sphCookie not configured"}
```

服务未运行时 `modules/wx_download.py` 会自动拉起（脱离进程），
但 Agent 调用结束后服务可能被回收，因此推荐用户手动常驻。

## 模型配置（config.yaml）

```yaml
whisper:
  model_size: "medium"      # >5 分钟自动切 small
  fallback_model: "small"
  max_duration_s: 300
  device: "cpu"
  compute_type: "int8"
llm:
  model: "Qwen/Qwen3-VL-30B-A3B-Instruct"  # 视觉 / 教程，MoE 实测 13-19s 每批
  fast_model: "Qwen/Qwen2.5-7B-Instruct"   # 分类 / 速览 / 清单 / 自检，约 0.5s
  base_url: "https://api.siliconflow.cn/v1"
```

可用视觉模型（/v1/models 实测）：
```
Qwen/Qwen3-VL-30B-A3B-Instruct   ★ 推荐，13-19s
Qwen/Qwen3-VL-8B-Instruct        5-9s，内容偏简
Qwen/Qwen3-VL-32B-Instruct       73-120s，常超时
Qwen/Qwen2.5-VL-32B-Instruct     ✗ 403 已禁用
```

## 关键坑（务必遵守）

1. **脱离进程**：转录 20-30 分钟，前台或平台后台任务会被清理，必须用 DETACHED_PROCESS。
2. **图片必须压缩**：1080×1920 原图 8 张 base64 后约 3MB，32B 模型会超时；
   送检前压到长边 1024 / 质量 85，体积降 73%。
3. **无代理请求本地服务**：`urllib.request.build_opener(ProxyHandler({}))`，否则 502。
4. **中文路径**：`cv2.imwrite` 会静默失败，用 `np.fromfile` / `imencode` 绕过。
5. **文件名 sanitize**：去 `# ! ? * < > : " / \ |` 再建目录。
6. **Whisper 模型**：本地在 `models/whisper-{small,medium}/`，缺失从 ModelScope 下载。
7. **终端编码**：`PYTHONIOENCODING=utf-8`，否则 GBK 崩溃。
