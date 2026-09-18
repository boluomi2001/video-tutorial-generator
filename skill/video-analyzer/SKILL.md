---
name: video-tutorial-generator
description: >-
  视频（尤其是微信视频号）→ 教学教程的一键流水线。当用户粘贴视频号链接、B站/YouTube/直链或本地视频文件，
  并说"分析这个视频""扒一下这个视频""这个视频讲了啥""视频转笔记""生成教程""生成复刻教程"
  "生成功能教程/效率提升教程""展开为复刻教程""看看这个视频怎么做的"等时触发。
  一条命令完成：下载 → Whisper 转录 → 分桶择优抽帧 → OCR → Qwen3-VL 视觉理解 → 事实抽取
  → 三形态笔记（详细教程/速览卡片/可执行清单）→ 自动清理临时文件。
  笔记含操作步骤、注意事项、FAQ、参数说明、原理。也支持只问"这个视频讲了什么"直接给速览。
  agent_created: true
---

# 视频 → 教学教程生成器

> 约定：下文 `<项目根>` 指本仓库在你机器上的实际路径（如 `D:\video-tutorial-generator`）。
> 未完成安装时先跑 `python scripts/setup.py --api-key <你的APIKey>`。

## 零、Agent 执行 SOP（拿到链接后照做）

0. 若流水线未安装/依赖缺失：先执行安装脚本（见 README 第 3 节），再继续。
0. 若 `<项目根>\tools\weixin-favor-kb\config.yaml` 不存在或 `api_key` 为空：
   复制 `config.example.yaml` 为 `config.yaml` 并填入用户的 API Key，否则分析阶段必然失败。
1. 用脱离进程启动流水线（代码见第一节），**立刻返回**，不要等待。
2. 每 60-90 秒读一次 `<工具根>/output/<最新 run 目录>/progress.json`。
3. `done=false` 时，用一句话同步进度（如「转录中 25%」），不要刷屏。
4. `done=true` 且 `error=null`：读 `notes` 数组里的 `tutorial.md`，把笔记内容直接呈现给用户。
   - 用户只要概要 → 给 `brief.md`
   - 用户要照着做 → 给 `checklist.md`
5. `error` 非空：说明原因，并告知 mp4 已保留可直接重试。

禁止事项：
- 不要用平台后台任务跑流水线（约 10 分钟会被清理）。
- 不要前台等待转录（长视频 20-30 分钟）。
- 不要在流程跑完前反复重启脚本。

---

## 一、最快用法（默认走这条）

**Agent 必须用 cmd 启动器**（固定 venv python、工作目录和编码，避免环境差异导致静默失败）：

```powershell
& "<项目根>\tools\weixin-favor-kb\run_auto.cmd" "<视频号链接或本地文件路径>"
```

⚠️ 不要直接用 `python auto_run.py`——系统 python 缺依赖会 import 即崩，
且 stderr 进了 DEVNULL 时看起来像"启动即退出无报错"。

新版下载器：`tools\wx_channels_download\wx_video_download.exe`（MCP + API 端口 2022）。
流水线已在 `modules/wx_download.py` 内置自动拉起逻辑（`ensure_downloader()`），
端口未通时会静默启动，Agent 通常无需手动involve。

**Agent 手动拉起时**必须走沙箱外通道——它启动时要调 `reg.exe`
设置系统代理，沙箱内会被安全策略直接杀掉（表现为进程秒退、端口连不上）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen([EXE], cwd=CWD, creationflags=F,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
```
调用时设 `dangerouslyDisableSandbox=True`（用户批准一次即可）。

启动后探测 `http://127.0.0.1:2022/` 返回 200 即就绪，再查
`MCPClient().wxchannels_available()` 确认微信视频号页面已连接。

**建议配置开机自启**（一次性，可选）：
`scripts\start_downloader.vbs` 会在进程未运行时静默拉起下载器。
把它（或它的快捷方式）放进 `shell:startup`（Win+R 输入该命令即可打开目录），
之后开机下载器就在后台跑，Agent 一次都不用启动它。

用户侧唯一需要做的：打开微信并进入视频号页面（解密必须在微信里做）。

**必须由 Agent 用脱离进程方式启动**（转录可能跑 20-30 分钟，前台/后台任务会被超时清理）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [r".\venv\Scripts\python.exe", "-u", "auto_run.py", source],
    cwd=r"<项目根>\tools\weixin-favor-kb",
    creationflags=F, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
```

启动后立刻返回，之后轮询 `output/<run_id>/progress.json`：

```json
{"stage":"transcribe","percent":25,"done":false,"error":null,"notes":[]}
```

`stage` 取值：`init → download → transcribe → frames → ocr → analyze → write → cleanup → done`
`done=true` 后读 `notes` 数组里的文件路径，把教程内容直接呈现给用户。

已验证：**脱离进程可跨工具调用存活**（心跳测试持续 24s+ 正常）。

## 二、参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `source` | 视频号链接 / 直链 / 本地文件路径 | 必填 |
| `--keep` | 保留视频与中间文件（默认成功后删除） | 关 |
| `--no-publish` | 不复制到项目 outputs 目录 | 关 |
| `--wait-manual <分钟>` | 等待手动下载的最长时间 | 30 |

## 三、下载环节的三种情况

| 情况 | 行为 |
|------|------|
| 本地文件路径 | 直接使用 |
| 普通 http 直链 | 直接下载到 `downloads/` |
| 视频号链接 | 调 wx_channel 本地服务 API；失败则进入等待模式 |

**视频号依赖登录态**：`/api/channels/parse_sph` 需要以下二者之一
1. 已部署解析 Cloudflare Worker（`cloudflare.sphHostname` + `sphCookie`）
2. GUI 内置浏览器已登录视频号并作为 client 连接

不满足时脚本提示"请在 wx_channel 中打开该链接并下载"，然后自动监听 `downloads/` 目录，
文件落盘后自动接管后续全部流程。用户只需在 GUI 点一下下载。

### 新版下载器 MCP（v260907+，当前主力方案）

新版程序路径：`tools/wx_channels_download_v260907/wx_video_download.exe`
API 与管理页端口 **2022**（旧版是 2025），MCP 端点 `http://127.0.0.1:2022/mcp`。

配置文件里已设 `mcp.enabled: true`（写进配置文件才不会重启失效），
下载目录指向 `tools/weixin-favor-kb/downloads`。

WorkBuddy 侧配置（已写入 `~/.workbuddy/mcp.json`）：
```json
{"mcpServers":{"wx_channels_download":{"type":"streamable-http","url":"http://127.0.0.1:2022/mcp"}}}
```

**29 个可用工具**，关键几个：

| 工具 | 用途 |
|---|---|
| `get_wxchannels_status` | 检查微信视频号页面是否已连接 |
| `download_wxchannels_video` | 传 url 直接下载视频号视频（需微信页面连接） |
| `download_content` | B站/抖音/快手/微博/YouTube 等，**无需浏览器直接下载** |
| `get_download_tasks` | 查任务状态（0等待 2下载中 5完成 6失败） |

流水线已内置 MCP 优先下载（见 `modules/wx_download.py` 的 `MCPClient`），
`auto_run.py` 会自动先尝试 MCP，失败才回落到等待手动下载。

**唯一前提**：视频号视频是加密的，解密只能在微信里做，
所以下载视频号时**必须保持微信打开着视频号页面**。其他平台无此限制。

### 彻底免人工：部署 Cloudflare Worker（一次性配置）

在 `tools/wx_channel/config.yaml` 填好后，于该目录执行：

```powershell
.\wx_channel.exe sph_deploy --config config.yaml
```

```yaml
cloudflare:
  accountId: ""     # 控制台右侧栏 Account ID
  apiToken: ""      # 必需。My Profile → API Tokens → Create Token
                    # 用「Edit Cloudflare Workers」模板
  sphCookie: ""     # 必需。dash.cloudflare.com 的 cookie（F12 从 api.cloudflare.com 请求复制）
  sphHostname: ""   # 部署后自动回填，勿手填
```

**两个坑（都踩过）**：
1. CLI 默认读 `$HOME/.wx_channel/config.yaml`，必须用 `--config config.yaml` 指向程序目录的配置。
2. **cookie 里常含英文双引号**（如 `curr-account={"xxx"}`），用双引号包裹会让 YAML 解析失败，
   导致整个 cloudflare 段失效（程序静默走默认值）。必须用块标量 `>-` 或单引号。

部署成功后解析不再依赖 GUI 登录态，给链接即可全自动。

备选通道（需官方 token）：`wx_channel client bind` 绑定云端 Hub，
配合 `cloud_hub_url` + `cloud_secret` 使用。

### 让 wx_channel 常驻（免手动开程序）
把 `wx_channel.exe` 的快捷方式放进 `shell:startup`，开机即在后台监听 2025 端口。
配合上面的 Worker 配置，从开机到出笔记全程无需人工介入。

## 四、关键帧策略（分桶择优）

| 环节 | 规则 |
|------|------|
| 帧数预算 | `clamp(10 + 8×ln(1+分钟数), 12, 36)`：1分钟→16帧，10分钟→29帧，30分钟+→36帧 |
| 覆盖保证 | 视频按帧数均分成桶，桶数取预算 80%，每桶必出 1 帧，消灭后半段盲区 |
| 桶内打分 | 边缘/文字密度 45% + 帧间差异 35% + 与已选帧差异 20% − 人脸占比惩罚 25% |
| 类型自适应 | 代码/终端 → 帧数×1.2 且文字权重 0.6；幻灯片 → 差异权重 0.55；口播 → 帧数×0.7 |
| 孪生帧 | 允许同一桶保留 2 张「相似但状态不同」的帧（如改代码前/后） |
| 性能 | ffmpeg 按 1fps 抽低清候选打分，选中帧再提原图，2 分钟视频约 14 秒 |

## 五、分析流程（两阶段，避免压缩损失）

1. **分类**：项目复刻 / 效率提升（走快模型，约 3 秒）
2. **章节切分**：按转录分段切约 6 章，帧 / OCR / 转录按章节绑定
3. **视觉理解**：每章一批，每批 ≤10 张帧，4 章并行
4. **事实抽取**：章节 / 命令 / 包名 / URL / 版本 / 配置 / 坑 / 前置条件；命令类信息用正则二次补抽
5. **教程生成**：逐章并行生成，每章含操作步骤、注意事项、FAQ、参数说明、原理
6. **质量自检**：覆盖率 <70 分自动重跑一次
7. **三形态输出**：`tutorial.md`（详细）/ `brief.md`（速览）/ `checklist.md`（可执行清单）

## 六、输出落盘

| 位置 | 内容 |
|------|------|
| `tools/weixin-favor-kb/output/<run_id>/notes/` | 三形态笔记 + `raw.json` + `cost.json` |
| `outputs/公众号视频项目/项目复刻教学/` | 项目类教程副本 |
| `outputs/公众号视频项目/功能提升教学/` | 功能类教程副本 |

成功后删除：源 mp4（仅删除本次下载到 downloads 的）、audio.wav、frames/、候选帧。
失败时保留 mp4 便于重试。

## 七、模型与成本

| 用途 | 模型 | 实测 |
|------|------|------|
| 视觉理解 / 教程 | `Qwen/Qwen3-VL-30B-A3B-Instruct` | 4 图批次 13-19s，稳定 |
| 分类 / 速览 / 清单 / 自检 | `Qwen/Qwen2.5-7B-Instruct` | 纯文本约 0.5s |

**为什么不用 32B 密集模型**：`Qwen3-VL-32B-Instruct` 实测 4 图批次 73-120s 且频繁超时，
MoE 版（30B-A3B，仅激活 3B）速度快 5-8 倍，视觉质量接近。若需最高精度可在 config 里换回。

实测单次全流程约 **¥0.11**（17 次调用，2 分钟视频）。成本明细写入 `notes/cost.json`。

> 快模型不可用时会自动回退主模型（识别到 400/模型不存在即切回）。
> 所有阶段都有失败降级：单批视觉失败记为空观察，单章教程失败用事实摘要兜底。

## 八、Windows 约束

- 必须设 `PYTHONIOENCODING=utf-8` + `PYTHONLEGACYWINDOWSSTDIO=utf-8`
- `cv2.imwrite` 不支持中文路径 → 用 `np.fromfile` / `imencode` 绕过
- 文件名先 `sanitize()` 去 `# ! ? * < > : " / \ |`
- 无 NVIDIA 显卡，Whisper 走 CPU：2 分钟视频约 113 秒，长视频自动切 small 模型

## 九、常见边界

- 只有链接但服务不可用 → 提示用户手动启动 `tools/wx_channel/wx_channel.exe`
- 转录为空（无语音视频）→ 仍可出画面笔记，但教程质量下降
- 用户只要速览 → 直接给 `brief.md`
- 用户要照着做 → 给 `checklist.md`
