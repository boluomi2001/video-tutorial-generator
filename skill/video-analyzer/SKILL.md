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

# 视频 / 公众号文章 → 教学教程生成器

## 零、Agent 执行 SOP（拿到链接后照做）

0. **先判类型**：链接含 `mp.weixin.qq.com/s` → **公众号文章通道**（只做原文归档，不改写）；
   其余（视频号 / 直链 / 本地文件）→ 视频通道（下载→转录→分析→生成笔记）。
1. 用脱离进程启动流水线（代码见第一节），**立刻返回**，不要等待。
2. 每 60-90 秒读一次 `<工具根>/output/<最新 run 目录>/progress.json`。
3. `done=false` 时，用一句话同步进度（如「转录中 25%」），不要刷屏。
4. `done=true` 且 `error=null`：读 `notes` 数组，找到**最终交付 md**。
   - 视频通道：文件名 = `<标题>.md`（不是 `tutorial/brief/checklist`）
   - 文章通道：文件名 = `<文章标题>.md`（仅此一份）
5. **上传 ima + 校验 + 删本地**（见下方「十、ima 自动上传 SOP」）——这是必须完成的收尾步骤。
6. `error` 非空：说明原因，并告知 mp4 已保留可直接重试。

禁止事项：
- 不要用平台后台任务跑流水线（约 10 分钟会被清理）。
- 不要前台等待转录（长视频 20-30 分钟）。
- 不要在流程跑完前反复重启脚本。
- **不要在 ima 回读校验通过之前删本地 md**。
- 文章通道**不要**做 AI 改写/加工——用户要求「跟原文一模一样」。

---

## 一、最快用法（默认走这条）

**Agent 必须用 cmd 启动器**（固定 venv python、工作目录和编码，避免环境差异导致静默失败）：

```powershell
& "D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\run_auto.cmd" "<视频号链接或本地文件路径>"
```

⚠️ 不要直接用 `python auto_run.py`——系统 python 缺依赖会 import 即崩，
且 stderr 进了 DEVNULL 时看起来像"启动即退出无报错"。

新版下载器（v260907，端口 2022）：`tools\wx_channels_download_v260907\wx_video_download.exe`

**Agent 可以自己启动它**，但必须走沙箱外通道——因为它启动时要调 `reg.exe`
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

**已配置开机自启**：
`shell:startup\start_wx_downloader.vbs`（静默启动、已运行则跳过），
所以正常情况下开机后下载器就在跑，Agent 不需要再启动。

用户侧唯一需要做的：打开微信并进入视频号页面（解密必须在微信里做）。

**必须由 Agent 用脱离进程方式启动**（转录可能跑 20-30 分钟，前台/后台任务会被超时清理）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [r".\venv\Scripts\python.exe", "-u", "auto_run.py", source],
    cwd=r"D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb",
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
| 视频号链接 | **① 云端 Worker 解析**（首选，免登录）→ ② 本地 MCP → ③ 等待手动下载 |

**下载优先级（视频号）**：`resolve_source()` 依次尝试
1. **云端 Worker**（`SphWorkerClient`）— 免微信登录、免本地进程，2-5 秒出直链
2. 本地 MCP 下载器（需微信视频号页面已连接）
3. wx_channel 本地服务 API → 等待手动下载

### 云端解析（Cloudflare Worker）— 视频号推荐通道

> ⚠️ **前置条件（不是开箱即用）**：这条通道依赖一个部署在**使用者自己的
> Cloudflare 账号**下的 Worker。仓库**不含**该 Worker 的配置与凭据
> （`tools/wx_channel/config.yaml` 已被 `.gitignore` 排除）。
> 未部署时，`SphWorkerClient.available()` 返回 False，`auto_run.resolve_source()`
> 会自动降级到「本地 MCP → wx_channel 服务 / 等待手动」，功能不中断。
> 给用户解释时务必说明「需自己部署一次」，不要承诺开箱即用。

**原理**：Worker 持有视频号解析逻辑，收到分享链接后调用微信
`channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`（该接口免鉴权），
拿到 `videoUrl`（未加密直链）后直接透传。**不需要微信登录态，不需要本地进程。**

配置与部署（一次性）：
```yaml
# tools/wx_channel/config.yaml
cloudflare:
  accountid: "..."    # Cloudflare Account ID
  apitoken: "..."     # API Token（Edit Cloudflare Workers 模板）
  sphcookie: "..."    # dash.cloudflare.com 的 cookie
  sphhostname: ""     # 部署后自动回填
```
```powershell
cd tools\wx_channel
.\wx_channel.exe sph_deploy --config config.yaml
```

**Agent 侧调用**：`modules/wx_download.py` 的 `SphWorkerClient`
- 自动从 `wx_channel/config.yaml` 的 `cloudflare.sphhostname` 读取地址
- 也可用环境变量 `SPH_WORKER_URL` 覆盖
- 端点 `POST /api/fetch_video_profile`，body `{"url": "<分享链接>"}`
- `.profile(url)` 返回 feedInfo（含 `videoUrl` / `h264VideoInfo` / 描述 / 作者）
- `.download(url, dest_dir)` 解析+下载一步到位

**实测**（3 条真实链接，2026-10-04）：解析 2.4-3.2s，下载 2.8-4.8s，
产出 H.264+AAC 标准 mp4，均可正常播放。**全程无需打开微信。**

备选：`modules/wx_download.py` 里也保留了直连微信 `get_feed_info` 的能力
（见 `SphWorkerClient` 注释），Worker 未部署时可作降级。

**视频号本地通道依赖登录态**：`/api/channels/parse_sph` 需要以下二者之一
1. 已部署解析 Cloudflare Worker（`cloudflare.sphHostname` + `sphCookie`）
2. GUI 内置浏览器已登录视频号并作为 client 连接

Worker 通道已在第 ① 步直接命中，通常不会走到这里。

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
- 超短视频 + 非教程类内容 → `checklist` 可能为空，属正常（该段在整合稿中自动省略）

---

## 十、最终产出：单文件整合稿 + ima 自动上传

### 10.1 最终交付物

流水线不再以三份 md 交付。`write_outputs()` 会额外生成**一份整合稿**：

```
notes/
  <标题>.md          ← 最终交付物（速览 + 执行清单 + 详细教程，三合一）
  tutorial.md        ← 中间产物
  brief.md           ← 中间产物
  checklist.md       ← 中间产物
  raw.json / cost.json
  .ima_pending.json  ← 待上传清单
```

#### 文件命名规则（重要）

整合稿必须用**可读视频标题**命名，**禁止**使用分享 ID（如 `AWEUh1CSE0.md`）：

```
{作者}：{描述首行}          例如：王假乐：Minimax官方感谢H3开发者.md
```

取值优先级（`auto_run.resolve_title`）：
1. 显式传入的 `explicit` 标题
2. 下载时留下的旁挂文件 `<视频stem>.title.txt`
   （由 `_sph_worker_try_download()` 调 `SphWorkerClient.meta_title()` 写入，cleanup 时删除）
3. 兜底：视频文件名去扩展名

注意：`transcripts/` 下的 `*.txt` / `*_segments.json` **仍以视频 ID 命名**（属中间产物，不改）。

整合稿结构：
```
# <标题>
> 类型｜时长｜章节｜帧数｜成本
## 速览            （一句话 + 要点 bullets + 适合谁）
## 执行清单        （- [ ] 步骤 + 可选 command；无内容则省略）
## 详细教程        （原 tutorial 正文，标题已整体降一级）
## 音频文案        （视频原声逐句转录，带时间戳，帮助理解作者原意）
```

#### 音频文案段（必须包含）

整合稿**必须**附带 `## 音频文案` 段，用于让读者直接看到作者的口播原话与语气。

- 有 `transcripts/<stem>_segments.json` 时 → 输出**带时间戳的逐句列表** `- \`mm:ss\` 原句`
- 只有纯文本转录时 → 输出整段文本
- 两者皆空 → 整段省略（不输出空标题）

数据来源：`run_stages()` 里已产出的 `transcript` / `segments` 变量，透传给
`write_outputs(..., transcript=..., segments=...)` → `merge_notes(...)` → `_format_transcript()`。

### 10.2 ima 自动上传 SOP（Agent 必做）

⚠️ **流水线脚本无法直接调 ima MCP** —— ima MCP 只注入 Agent 会话，
不是本地 HTTP 服务（探测 `127.0.0.1:5283` 返回空响应）。
因此上传必须由 **Agent 用 MCP 工具**完成。

**三步链路**（ima MCP 原生定义）：

| 步骤 | 工具 | 关键参数 |
|---|---|---|
| 1 | `mcp__ima-mcp__create_media` | `knowledge_base_id`, `file_name`, `file_ext`, `content_type`=`text/markdown`, `file_size` |
| 2 | COS 上传 | 用第 1 步返回的 `cos_credential` PUT 到腾讯云 |
| 3 | `mcp__ima-mcp__add_knowledge` | `knowledge_base_id`, `media_id`, `duplicate_name_strategy` |

**但 Agent 侧更简单的做法**：直接用 `modules/ima_upload.py` 的
`upload_md()`（内部完成三步），或让我用 MCP 工具逐步执行。

**知识库固定为**：`001a8016b30037f0`（朝の度的知识库）
**已存在的目标文件夹**（可选 `folder_id`）：
- `folder_7483004846882248` 项目复刻教学
- `folder_7483004888827852` 功能提升教学

**校验（删除本地前必须做）**：
```
mcp__ima-mcp__search_knowledge  { knowledge_base_id, query: "<标题去后缀>", cursor: "" }
```
命中条件：`title` 完全一致 或 `media_id` 一致。命中即可删本地。

### 10.3 删除规则（硬约束）

```
上传 → 回读校验 ──✅命中──→ 删本地 md（notes/<标题>.md）
                   └─❌未命中─→ 保留本地 + 报错，绝不删
```

- **只有 `notes/<标题>.md`（整合稿）删本地**；若同步发布了 `outputs/公众号视频项目/...`
  副本，按用户约定一并删除（当前约定：也删）。
- 中间产物 `tutorial.md` / `brief.md` / `checklist.md` 是否删由用户决定，
  默认**保留**以便排查（在 run 目录内，不碍事）。
- **校验失败三种处理**：网络抖动 → 重试 3 次（间隔 3s）；仍失败 → 保留文件并告知用户；
  标题重名 → 用 `DUPLICATE_NAME_STRATEGY_REPLACE` 覆盖。

### 10.3.1 ima 无删除接口（已知限制）

ima MCP **不提供删除 / 移除知识条目的工具**（可用工具仅：
`create_media` / `add_knowledge` / `search_knowledge` / `fetch_media_content` /
`get_knowledge_list` / `get_knowledge_base_list` / `get_addable_knowledge_base_list` / `import_urls`）。

后果：
- 同一条视频若重复上传（如改标题后重传），**旧条目会遗留在 ima**，无法程序化清理；
- `add_knowledge` 的重名策略只影响同名文件，改名后即为两条不同条目。

对策：
- 上传前先 `search_knowledge` 查是否已有该视频条目，命中则**不再重复上传**；
- 需要清理历史遗留条目时，明确告知用户「请到 ima 内手动删除」，并附上 `media_id` 便于定位。

### 10.4 一键脚本

`modules/ima_upload.py` 已封装：
```python
from modules import ima_upload
# 上传前先配 IMA_MCP_URL 环境变量（若走脚本通道）
iu.upload_md(Path("notes/标题.md"), kb_id="001a8016b30037f0")
hit = iu.verify_uploaded("标题.md")   # 命中才返回，否则 None
if hit: local.unlink()                # 确认后才删
```

若 `IMA_MCP_URL` 未配置（默认情形），Agent 直接走自己的 MCP 工具完成上传与校验。

---

## 十一、公众号文章通道（原文归档，不改写）

**触发**：链接形如 `https://mp.weixin.qq.com/s/<id>`（`wx_article.is_article_url()` 判定）。
这类链接**自动**走文章通道，无需额外参数；`auto_run.py` 在 `main()` 里分流。

### 11.1 与视频通道的区别

| | 视频通道 | 文章通道 |
|---|---|---|
| 输入 | 视频号链接 / 直链 / 文件 | 公众号文章链接 |
| 处理 | 下载→转录→抽帧→OCR→VL 分析 | 抓取 → 保真转 md |
| 输出 | 整合稿（速览+执行清单+详细教程+音频文案） | **原文归档**（元信息头 + 正文） |
| 是否改写 | 是（AI 生成教学笔记） | **否，一字不改** |
| 成本 | 有 LLM 成本 | 0 |
| 发布目录 | `outputs/公众号视频项目/<分类>/` | `outputs/公众号文章/` |
| ima 文件夹 | 项目复刻教学 / 功能提升教学 | 功能提升教学 |

### 11.2 抓取原理（实测 2026-10-04）

- 纯 HTTP GET 即可，**无需登录 / Cookie / 签名**，无反爬拦截
- 页面锚点：
  - 标题：`var msg_title` 或 `<h1 class="rich_media_title">` 或 `og:title`
  - 公众号名：`var nickname` / `#js_name` / `data-nickname`
  - 作者：`og:article:author` / `var author`
  - 发布时间：`var ct`（Unix 秒）
  - 正文：`<div class="rich_media_content" id="js_content">`，**结束边界是 `</div>\s*<script`**
  - 配图：`<img data-src="...">`（mmbiz.qpic.cn 外链，不会本地化）

### 11.3 保真转换的 4 个坑（代码已处理，勿改顺序）

1. `<strong>` 常嵌在 `<span leaf>` 里 → **必须先转 `**` 再清 span**，否则加粗全丢
2. `<li>` 内部自带 `•` / `1.` 标记 → 去重，避免出现 `- •`
3. `</p>` → 双换行，否则段间被压成一行
4. 图片 URL 含 `)`，简单正则会截断 → 用占位符注册表再回填

自检口径：`**` 计数为偶数、无残留 HTML 标签、图片链接闭合。

### 11.4 命令

```bash
python auto_run.py "https://mp.weixin.qq.com/s/XXXX"
```

产出：
- `output/<ts>_wechat_article/notes/<文章标题>.md`（最终交付物）
- `output/<ts>_wechat_article/article_raw.html`（原始 HTML，中间产物，便于复核）
- `output/<ts>_wechat_article/notes/.ima_pending.json`（待上传清单）
- 发布副本：`outputs/公众号文章/<文章标题>.md`

### 11.5 代码位置

- `modules/wx_article.py`：`is_article_url()` / `fetch()` / `to_markdown()` / `html_to_markdown()` / `filename_for()`
- `auto_run.py`：`run_article()` / `write_article_pending()`，`main()` 里按 `is_article_url` 分流
- `publish()` 对文章分支：发布到 `outputs/公众号文章/`，**文件名不加前缀**（标题已在文件名内）
