---
name: video-tutorial-generator
description: >-
  视频（尤其是微信视频号）→ 结构化笔记的一键流水线。当用户粘贴视频号链接、B站/YouTube/直链或本地视频文件，
  并说"分析这个视频""扒一下这个视频""这个视频讲了啥""视频转笔记""生成教程""生成复刻教程"
  "生成功能教程/效率提升教程""展开为复刻教程""看看这个视频怎么做的"等时触发。
  默认走「Agent 写笔记」模式（纯本地、零 API 成本）：流水线只产出干净素材
  （Whisper 转录 + 分桶抽帧 + 本地 OCR + 缩略图拼版），视觉理解与写笔记全部由 Agent 完成，
  笔记结构按每条视频的内容自适应；也保留传统 Qwen 自动生成模式（--with-vision）。
  另含公众号文章通道（原文归档，一字不改）。
  agent_created: true
---

# 视频 / 公众号文章 → 结构化笔记生成器

## 零、Agent 执行 SOP（拿到链接后照做）

### 第 0 步：判类型

| 输入 | 通道 |
|---|---|
| 含 `mp.weixin.qq.com/s` | **公众号文章通道**（原文归档，不改写）→ 见 §13 |
| 其余（视频号 / 直链 / 本地文件） | **视频通道** → 见下 |

### 视频通道：默认走「Agent 写笔记」模式（纯本地 · 零 API 成本）

1. **启动**（脱离进程，立刻返回）：
   ```powershell
   & "<工具根>\run_agent.cmd" "<链接或本地文件路径>"
   ```
   等价于 `python auto_run.py --agent-write "<链接>"`。批量加 `--from-file links.txt`。
2. **轮询** `output/<最新 run 目录>/progress.json`，直到 `done=true`（每 60-90 秒一次，
   不要刷屏）。`stage` 取值：`init → download → transcribe → frames → ocr → analyze → write → cleanup`。
3. **读干净素材**：`notes/agent_input.json`（结构见 §10）
   - **先看 `contact_sheet`**（缩略图拼版，一张图纵览全片画面）→ 判断这条属哪类；
   - 按 `agent_rules.adapt_rule` 决定**要不要再读单帧**；
   - ⚠️ **禁止**读任何已成型的 md（`tutorial.md` / `brief.md` / `checklist.md` / 整合稿）。
4. **写笔记**：**按这条视频的内容定最合适的结构**（不套模板，见 §11）→ 存
   `outputs/_新版笔记/<标题>.md`，末尾必须带 `## 音频文案`（取自 `segments`，带 `mm:ss`，原样保留）。
5. **上传 ima + 回读校验**（§12）——校验命中才算完成。

**禁止事项**：
- 不要用平台后台任务跑流水线（约 10 分钟会被清理）。
- 不要前台等待转录（长视频 20-30 分钟）。
- 不要在流程跑完前反复重启脚本。
- **不要在 ima 回读校验通过之前删本地 md。**
- 文章通道**不要**做 AI 改写/加工——用户要求「跟原文一模一样」。

---

## 一、两种运行模式（先选）

| | **① Agent 写笔记（默认，推荐）** | ② 传统 Qwen 模式 |
|---|---|---|
| 开关 | `--agent-write`（默认含纯本地） | 不带 `--agent-write` |
| 启动器 | `run_agent.cmd` | `run_auto.cmd` |
| 流水线产出 | 转录 + 关键帧 + 本地 OCR + 缩略图拼版 + `agent_input.json` | 三形态笔记 + 整合稿 |
| 谁写笔记 | **Agent**（按内容定结构，不编造） | Qwen3-VL 逐章生成 |
| LLM 调用 | **0 次**（`--with-vision` 时才恢复） | 分类 / 视觉 / 事实 / 教程 / 自检 |
| 成本 | **¥0** | 约 ¥0.04–0.11/条 |
| 适用 | 有人值守、追求质量（日常） | 无人值守的超大批量 |

> `--agent-write --with-vision` = Agent 写笔记 + 保留付费视觉模型（视觉观察写进 facts，
> 供 Agent 参考）。默认关闭。

**启动方式**：必须用 **cmd 启动器**或用**脱离进程**方式启动（转录可能 20-30 分钟，
前台/后台任务会被超时清理）：

```python
import subprocess
F = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [r".\venv\Scripts\python.exe", "-u", "auto_run.py", "--agent-write", source],
    cwd=r"<工具根>", creationflags=F, stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
```

⚠️ 不要直接用系统 `python`——缺依赖会 import 即崩，且 stderr 进 DEVNULL 时
看起来像"启动即退出无报错"。cmd 启动器已固定 venv python + 工作目录 + 编码。

### 批量模式（一次多条链接）

```powershell
& "<工具根>\run_agent.cmd" "链接1" "链接2" "链接3"
& "<工具根>\run_agent.cmd" --from-file links.txt
& "<工具根>\run_agent.cmd" --from-file links.txt --resume output\batch_<时间戳>
```

**行为要点**：
- **串行**执行、逐条隔离 → **单条失败不中断批次**（区别于单条模式失败即退出）
- 每条仍是独立 run 目录（`output/<ts>_xxx/`），`progress.json` 照常可轮询
- 批次目录 `output/batch_<时间戳>/` 额外产出：
  - `batch_state.json` —— 每跑完一条即落盘，供 `--resume`
  - `_batch_<时间戳>.md` —— 汇总表（成功/失败/耗时/成本/产物 + 失败项可复制重跑清单）
  - **Agent 模式**：`.agent_pending_batch.json` —— 汇总各条 `agent_input` 的待办清单
  - 传统模式：`.ima_pending_batch.json` —— 汇总全部成功项的 ima 待上传清单
- 退出码：全成功 `0`，有失败 `1`

**给用户答耗时**：串行下 2 分钟视频约 3-6 分钟/条（Agent 模式更短，省掉了 LLM 环节）。

---

## 二、参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `source` | 视频号链接 / 直链 / 本地文件路径（**可传多条 → 批量**） | 必填* |
| `--agent-write` | **Agent 写笔记模式**（只出干净素材；默认纯本地、零 API） | 关 |
| `--with-vision` | 配合 `--agent-write`：保留付费视觉模型（无人值守超大批量才用） | 关 |
| `--from-file <文件>` | 从文件批量读取链接（一行一条，`#` 注释） | — |
| `--resume <批次目录>` | 断点续跑：跳过已成功项 | — |
| `--keep` | 保留视频与中间文件（默认成功后删除） | 关 |
| `--no-publish` | 不复制到项目 outputs 目录 | 关 |
| `--wait-manual <分钟>` | 等待手动下载的最长时间 | 30 |

\* `source` 与 `--from-file` 至少要有一个；两者可同时给（合并后去重）。

---

## 三、下载环节的三种情况

| 情况 | 行为 |
|------|------|
| 本地文件路径 | 直接使用（**不会删除源文件**：`is_temp=False`） |
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
> 未部署时 `SphWorkerClient.available()` 返回 False，`resolve_source()`
> 会自动降级到「本地 MCP → wx_channel 服务 / 等待手动」，功能不中断。
> 给用户解释时务必说明「需自己部署一次」，**不要承诺开箱即用**。

**原理**：Worker 持有视频号解析逻辑，收到分享链接后调用微信
`channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`（该接口免鉴权），
拿到 `videoUrl`（未加密直链）后直接透传。**不需要微信登录态，不需要本地进程。**

部署（一次性）：

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

**两个坑（都踩过）**：
1. CLI 默认读 `$HOME/.wx_channel/config.yaml`，**必须**用 `--config config.yaml` 指向程序目录的配置。
2. **cookie 里常含英文双引号**（如 `curr-account={"xxx"}`），用双引号包裹会让 YAML 解析失败，
   导致整个 `cloudflare` 段静默失效。必须用块标量 `>-` 或单引号。

**Agent 侧调用**：`modules/wx_download.py` 的 `SphWorkerClient`
- `.available()` / `.profile(url)` / `.video_url(url)` / `.meta_title(url)` / `.download(url, dest_dir)`
- 地址自动从 `wx_channel/config.yaml` 的 `cloudflare.sphhostname` 读取；`SPH_WORKER_URL` 可覆盖
- 端点 `POST /api/fetch_video_profile`，body `{"url": "<分享链接>"}`
- `.meta_title(url)` 返回 `{作者}：{描述首行}`，会写成旁挂文件供标题使用

### 新版下载器 MCP（v260907+）

程序路径：`tools/wx_channels_download_v260907/wx_video_download.exe`
API 与管理页端口 **2022**（旧版 2025），MCP 端点 `http://127.0.0.1:2022/mcp`。

WorkBuddy 侧配置（写入 `~/.workbuddy/mcp.json`）：
```json
{"mcpServers":{"wx_channels_download":{"type":"streamable-http","url":"http://127.0.0.1:2022/mcp"}}}
```

关键工具：

| 工具 | 用途 |
|---|---|
| `get_wxchannels_status` | 检查微信视频号页面是否已连接 |
| `download_wxchannels_video` | 传 url 直接下载视频号视频（需微信页面连接） |
| `download_content` | B站/抖音/快手/微博/YouTube 等，**无需浏览器** |
| `get_download_tasks` | 查任务状态（0等待 2下载中 5完成 6失败） |

**已配置开机自启**：`shell:startup\start_wx_downloader.vbs`（静默启动、已运行则跳过）。

**唯一前提**：视频号视频是加密的，解密只能在微信里做 —— 下载视频号时**必须保持
微信打开着视频号页面**。其他平台无此限制。

> 启动下载器脚本时须设 `dangerouslyDisableSandbox=True`（它要调 `reg.exe` 设系统代理，
> 沙箱内会被安全策略杀掉，表现为进程秒退）。

---

## 四、关键帧策略（分桶择优）

| 环节 | 规则 |
|------|------|
| 帧数预算 | `clamp(10 + 8×ln(1+分钟数), 12, 36)`：30s→13，1min→16，5min→24，10min→29，**≥30min→36（硬顶）** |
| 覆盖保证 | 候选帧按时间均分成 N 桶（`N = min(预算×0.8, 候选数)`），**每桶必出 1 帧** |
| 桶内打分 | 边缘/文字密度 0.45 + 帧内差异 0.35 + 与已选帧差异 0.20 − 人脸占比惩罚 0.25 |
| 类型自适应 | 代码/终端 → 帧数×1.2 且文字权重 0.6；幻灯片 → 差异权重 0.55；口播 → 帧数×0.7 |
| 孪生帧 | 允许同一桶保留 2 张「相似但状态不同」的帧（如改代码前/后） |
| 性能 | ffmpeg 按 1fps 抽低清候选打分，选中帧再提原图；2 分钟视频约 14-25 秒 |
| 产物 | `frames/keyframe_NNN_tXX.Xs.jpg` + `frames_meta.json`（含 `profile`：type/edge/motion/face） |

> ⚠️ **代码里的实际上限是 36 帧**（模块常量 `MAX_FRAMES`）。
> `config.yaml` 的 `frames.max_frames: 20` **只在 `step2_analyze.py` 那条路生效**，
> 主链路 `auto_run.run_stages()` 调用时没传该参数 → 走默认 36。

**已知短板（列举型视频）**：抽帧是"按预算均分时间"，**假设画面内容大致平稳**。
若视频是**列举型**（每几秒换一个知识点，如"30 种风格"）或**无口播**，
这个密度会**整段漏掉**（实测 30 段只采到 17 段）。
→ 这种视频**不在流水线里硬加规则**（会伤到正常视频），
**由 Agent 在写笔记前自行判断并补采**（做法见 §11.3）。

---

## 五、分析流程

### 5.1 Agent 写笔记模式（默认，`--agent-write`，纯本地）

```
下载 → 音频提取 → Whisper 转录 → 分桶抽帧 → 本地 OCR(RapidOCR)
    → 生成缩略图拼版 → 落地 agent_input.json
```

**不调用任何 LLM API**（`run_stages(local_only=True)` 时连 `ContentAnalyzer` 都不构造）。
产物里 `video_type` / `domain` 为空 —— 由 Agent 自己判定。

### 5.2 传统 Qwen 模式（不带 `--agent-write`）

1. **分类**：项目复刻 / 效率提升（快模型，约 3 秒）
2. **章节切分**：按转录分段切约 6 章
3. **视觉理解**：每章一批、每批 ≤4 张帧、2 线程并行
4. **事实抽取**：章节/命令/包名/URL/版本/配置/坑/前置条件；命令类用正则二次补抽
5. **教程生成**：逐章并行生成（每章含步骤/注意/FAQ/参数/原理）← **已知会注水重复，见 §11.2**
6. **质量自检**：覆盖率 <70 分自动重跑一次
7. **三形态输出** → 整合稿

---

## 六、输出落盘

### Agent 模式

| 位置 | 内容 |
|------|------|
| `<run>/notes/agent_input.json` | **Agent 的唯一输入**（干净素材，结构见 §10） |
| `<run>/notes/raw.json` | 同 res（Agent 模式下 tutorial 为空，不构成污染；便于回溯） |
| `<run>/notes/.agent_pending.json` | 待办清单（含 folder_id 与 agent_steps） |
| `<run>/transcripts/*.txt` + `*_segments.json` | 转录（`.txt` 以视频 ID 命名） |
| `<run>/frames/` | **关键帧 + `contact_sheet.jpg` + `frames_meta.json`（Agent 模式保留）** |
| `outputs/_新版笔记/<标题>.md` | Agent 写出的笔记（由 Agent 落盘） |

### 传统模式

| 位置 | 内容 |
|------|------|
| `<run>/notes/<标题>.md` | 最终交付物（速览 + 执行清单 + 详细教程 + 音频文案） |
| `<run>/notes/tutorial.md` `brief.md` `checklist.md` | 中间产物 |
| `<run>/notes/raw.json` `cost.json` | 事实 + 成本 |
| `outputs/公众号视频项目/{项目复刻教学,功能提升教学}/` | 发布副本 |

**清理规则**：成功后删除源 mp4（仅本次下载到 `downloads/` 的）、`audio.wav`；
**Agent 模式额外保留 `frames/`**（便于回溯画面来源，也让 Agent 可按需看图）。
失败时保留 mp4 便于重试。

**本地笔记副本策略**：Agent 写出的 `outputs/_新版笔记/<标题>.md` **默认保留**，
不因上传成功而删除（用户约定：本地留一份存档）。仅当用户明确要求清理时才删。

---

## 七、模型与成本

| 模式 | LLM 调用 | 实测成本 |
|---|---|---|
| **Agent 写笔记（默认）** | **0 次** | **¥0**（只耗本机 CPU + Agent 会话额度） |
| `--agent-write --with-vision` | 分类 + 视觉 + 事实 | 约 ¥0.04/条 |
| 传统模式 | 分类 + 视觉 + 事实 + 教程 + 自检 | ¥0.04–0.11/条 |

传统模式所用模型：

| 用途 | 模型 |
|------|------|
| 视觉理解 / 教程 | `Qwen/Qwen3-VL-30B-A3B-Instruct`（MoE，激活 3B，4 图批次 13-19s） |
| 分类 / 速览 / 清单 / 自检 | `Qwen/Qwen2.5-7B-Instruct`（纯文本约 0.5s） |

**为什么不用 32B 密集模型**：`Qwen3-VL-32B-Instruct` 实测 4 图批次 73-120s 且频繁超时；
MoE 版快 5-8 倍，视觉质量接近。

> **成本归因（实测）**：`--agent-write --with-vision` 的 ¥0.0387 里，
> **约 10/11 的调用是"视觉理解"**（把关键帧喂给 Qwen3-VL），分类仅 1 次。
> 关掉视觉（默认）即归零。这也是默认纯本地的主要理由。

> **facts 层不可靠（实测）**：输入过大时 `_extract_facts` 会 120s 超时 ×3 并降级为正则结果，
> 此时**视觉观察被整段丢弃**。结论：**转录 + OCR 才是稳定数据源**，facts 只能当参考。

---

## 八、Windows 约束

- 必须设 `PYTHONIOENCODING=utf-8` + `PYTHONLEGACYWINDOWSSTDIO=utf-8`
- `cv2.imwrite` 不支持中文路径 → 用 `np.fromfile` / `cv2.imdecode` / `imencode` 绕过
- 文件名先 `sanitize()` 去 `# ! ? * < > : " / \ |`
- 无 NVIDIA 显卡，Whisper 走 CPU：2 分钟视频约 78-121 秒；**音频 >300s 自动切 small 模型**
- 拼版/图片统一走 `write_bytes` + `cv2.imencode`，避免中文路径问题

---

## 九、常见边界

| 情况 | 表现 / 处理 |
|---|---|
| 只有链接但服务不可用 | 提示用户手动启动 `tools/wx_channel/wx_channel.exe` |
| **转录为空（无口播）** | 正常现象（纯 BGM 视频）。**画面成为唯一信息源** → Agent 必须看拼版并按需读帧；必要时补采（§11.3） |
| **列举型视频（每 N 秒一个知识点）** | 抽帧密度可能不够 → Agent 判断后补采（§11.3） |
| 超短视频 + 非教程类 | 传统模式 `checklist` 可能为空，属正常（整合稿自动省略该段） |
| 本地文件路径 | **不会被删除**（`is_temp=False`），可放心用作测试源 |
| `facts` 抽取超时 | 日志出现 `Request timed out` ×3 → 降级为正则结果；**不影响 Agent 模式** |
| `--resume` 无法续跑 | 检查 `batch_state.json` 是否存在（每跑一条即落盘） |

---

## 十、Agent 写笔记：干净素材与取数约定

### 10.1 `notes/agent_input.json` 结构

```jsonc
{
  "mode": "agent_write",
  "title": "…",                    // 视频标题（{作者}：{描述首行}）
  "source_url": "…",
  "video_type": "",                // 纯本地模式下为空 —— 由 Agent 判定
  "domain": "",
  "duration": 164.8,
  "chapters_hint": [],             // 仅参考，不要当结构用
  "facts": {},                     // 纯本地模式下为空；--with-vision 时才有（且可能不可靠）
  "transcript": "…",               // 全文转录
  "transcript_path": "…/transcripts/xxx.txt",
  "segments_path": "…/transcripts/xxx_segments.json",   // 带时间戳，写「音频文案」用
  "ocr": [{"t": 12.0, "text": "…"}],                    // 画面文字（本地 OCR，带时间戳）
  "frames_dir": "…/frames",
  "frames": ["…/keyframe_000_t8.0s.jpg", "…"],
  "contact_sheet": "…/frames/contact_sheet.jpg",        // ★ 一张图纵览全片
  "frame_profile": {"type": "code|slides|talking|general", "edge": 0.07, "motion": 0.25, "face": 0.0},
  "ocr_stats": {"frames": 22, "chars": 946, "chars_per_frame": 43.0},
  "agent_rules": { "read_only": [...], "never_read": [...], "note": "...", "adapt_rule": "..." }
}
```

### 10.2 取数约定（**硬约束**）

**只读**：`facts` / `transcript` / `segments_path` / `ocr` / `contact_sheet` / `frames`。

**禁止读**：
- `notes/tutorial.md` / `brief.md` / `checklist.md`
- `notes/raw.json` 里的 `tutorial` / `brief` / `checklist` 字段
- 任何已成型的整合稿 md

> 原因：Qwen 生成的教程是 **20–40 倍注水**的源（实测：861 字转录 → 17,692 字笔记）。
> 从污染源"优化"是在垃圾里挑金子；**转录 + OCR 才是干净的源**。

### 10.3 缩略图拼版（contact sheet）

`frames/contact_sheet.jpg`：把全部关键帧拼成一张网格图（默认 6 列、每格 280px），
**每格左上角标注该帧时间戳**。用途：**读 1 张图 = 看完全片画面**，上下文成本极低。

实测：17 帧拼成 1680×474 / 98KB，一张图即可读出全部 17 格的时间戳与内容。

**需要看细节时**，再按拼版上的时间戳去 `frames/` 读对应原帧
（文件名格式 `keyframe_NNN_tXX.Xs.jpg`，可直接对上）。

---

## 十一、写笔记：结构与「规则之外」的适配

### 11.1 结构按内容定（**不要套模板**）

一篇笔记只用一个结构，结构随内容走。参考骨架（不是必选清单）：

| 内容类型 | 骨架 | 典型例子 |
|---|---|---|
| 观点说理 | 核心观点 → 论证 → 论据 → **适用边界**（**不列执行清单**） | "买铲子=智商税" |
| 操作步骤 | 适用场景 → 前置条件 → 步骤 → 执行清单 → 避坑 | 字幕 Skill、Vibe Coding |
| 概念科普 | 是什么 → 怎么运作 → 例子 → 常见误解 | Token 计费、提示词注入 |
| 方法体系 | 方法论主张 → 框架阶段 → 关键动作 → 易错点 | 干中学、如何痛苦学 AI |
| 工具项目 | 是什么 → 上手复刻步骤 → 执行清单 → 限制 / 对比 | 6 个 AI 项目架构 |
| 记录随笔 | 背景 → 内容脉络 → 观点与留白 → 金句 | 等待 AI 工作时 |
| 风格图鉴 | 清单表（名/英文/一句话/画面特征）→ 家族归类 → 用法 | 30 种视觉风格 |

**通用外壳**（都要有）：`# 标题` → 元信息 → 速览 → 主体（自适应）→ `## 音频文案`。

### 11.2 硬规则（踩过的坑）

1. **执行清单只在操作类 / 工具项目类出现** —— 观点类、科普类**不设**清单。
2. **作者没讲的不许补**（如"他在求助、没给答案"，就如实写"视频没给答案"）；
   确需外部补充时**明确标注来源**（如"〔官方资料〕"）。
3. **参数 / 数值 / 工具名一律以转录 + OCR 为准** —— 不要再出现"temperature=0.7"这类没影的东西。
4. **同一观点全文只出现一次**（语义去重，不是删重复行）。
5. **同音误听要校正** —— ASR 常把专有名词听错（实测：`自适应组件→自摄用组件`、
   `Hyper3D Rodin→Hyper 3D Ready`、`Bang→Bomb`、`Blender→Bond`、`GPT→GBT/GP7`）。
   校正后在「边界与注意」里列出来。
6. **长短随内容** —— 1 分钟视频不要写成 1 万字。
7. **`## 音频文案` 原样保留**（取自 `segments`，带 `mm:ss`）。

### 11.3 ★ 规则之外：Agent 现场适配（**重要**）

`adapt_rule` 里的 `chars_per_frame` 阈值只是**参考信号，不是铁律**。
遇到规则外的情况，**由 Agent 判断并采取最好的做法**，不要机械照搬：

| 规则外情况 | Agent 应做 |
|---|---|
| **无口播**（转录 0 字） | 画面是唯一信息源 → 必须逐格读拼版；`contact_sheet` 不够就**补采** |
| **列举型**（每几秒一个知识点） | 抽帧密度必然不够 → **补采**：重新下载后按固定间隔抽帧 |
| `frame_profile` 与画面不符 | 以**你亲眼看到的拼版为准**（实测：彩色打光下人脸检测漏检，明明有人却 `face=0.0`） |
| `ocr_stats` 高但画面是关键 | 反过来也要看拼版（数字高不代表画面不重要） |
| facts 为空 / 不可靠 | 正常 —— 用转录 + OCR，不要因此卡住 |

**补采做法**（实测有效）：
```python
from modules.wx_download import SphWorkerClient
from modules.frames import resolve_ffmpeg, build_contact_sheet
v = SphWorkerClient().download(url, tmp_dir)     # 重新下载（云端 Worker，几秒）
# 再用 ffmpeg 按固定间隔抽帧（例：33 帧 / 118 秒 ≈ 3.5s 一帧），然后 OCR + 拼版
```
实测：30 段风格视频用 3.5s 间隔抽 33 帧 → **30 段全覆盖**（流水线默认只采到 17 段）。

> **不在流水线里硬加"无口播就加密抽帧"的规则** —— 那会伤到正常视频。
> 交给 Agent 按内容判断更准（这是本工作流的设计取舍）。

---

## 十二、ima 自动上传 SOP（Agent 必做）

⚠️ **流水线脚本无法直接调 ima MCP** —— ima MCP 只注入 Agent 会话，不是本地 HTTP 服务。
因此上传必须由 **Agent 用 MCP 工具**完成。

### 12.1 三步链路

| 步骤 | 工具 / 动作 | 关键参数 |
|---|---|---|
| 1 | `mcp__ima-mcp__create_media` | `knowledge_base_id`, `file_name`（**带 .md**）, `file_ext`=`md`, `content_type`=`text/markdown`, `file_size`（**必须与文件实际大小一致**） |
| 2 | COS 上传 | 用第 1 步返回的 `cos_credential` PUT；`ContentType=text/markdown` |
| 3 | `mcp__ima-mcp__add_knowledge` | `knowledge_base_id`, `media_id`, `folder_id`, `duplicate_name_strategy`=`DUPLICATE_NAME_STRATEGY_SAVE` |

**第 2 步的现成脚本**（推荐）：
```bash
# 从 stdin 读一行 JSON 凭证，单文件上传；成功输出 ETag
python scripts/_ima_up2/up_one.py "<本地 md 绝对路径>" << 'JSON'
{"token":"…","secret_id":"…","secret_key":"…","region":"ap-shanghai",
 "bucket_name":"ima-media-prod-1258344701","cos_key":"…","media_id":"…"}
JSON
```

> ⚠️ **凭证文件严禁入库**：`scripts/_ima_up*/**.json` 含 COS 临时密钥，已在 `.gitignore`。
> 凭证约 12 小时过期，配合 `create_media` 现取现用即可。

### 12.2 固定 ID

- **知识库**：`001a8016b30037f0`（朝の度的知识库）
- **目标文件夹「大李老师」（当前约定：平铺放入）**：`folder_7512859667859656`
  - 旧的 `folder_7512536198941318` **已失效**（用户重建过文件夹，勿再用）
  - 历史文件夹：`folder_7483004846882248`（项目复刻教学）、
    `folder_7483004888827852`（功能提升教学）

### 12.3 回读校验（**删本地前必须做**）

```
mcp__ima-mcp__search_knowledge  { knowledge_base_id, folder_id, query: "<标题去 .md>" }
```
命中条件：`title` 一致 **或** `media_id` 一致 → 视为上传成功。

> 返回体可能很大（10 万字符级）被截断保存到文件 → 用 `grep -o` 在该文件里查
> `"title":"…"` 与 `media_id` 片段即可。

### 12.4 删除规则（硬约束）

```
上传 → 回读校验 ──✅命中──→ 才「可以」删本地
                   └─❌未命中─→ 保留本地 + 报错，绝不删
```

- **当前用户约定：本地笔记默认保留**（`outputs/_新版笔记/`）——上传成功也不删，
  仅当用户明确要求清理时才删。
- 中间产物（`tutorial.md` / `brief.md` / `checklist.md` / `raw.json`）默认保留在 run 目录。
- 校验失败：网络抖动 → 重试 3 次（间隔 3s）；仍失败 → 保留文件并告知用户。

### 12.5 ima 无删除接口（已知限制）

ima MCP **不提供删除 / 移除知识条目的工具**（可用：`create_media` / `add_knowledge` /
`search_knowledge` / `fetch_media_content` / `get_knowledge_list` / `get_knowledge_base_list` /
`get_addable_knowledge_base_list` / `import_urls`）。

**后果**：同一条视频重复上传（如改标题后重传）会**遗留旧条目**，无法程序化清理。

**对策**：
- **上传前先 `search_knowledge` 查是否已有该条目**，命中则不重复上传；
- ⚠️ **宁可先补采 / 先确认质量，也不要传一份不完整的笔记** —— 传错了只能手动删；
- 需要清理历史条目时，告知用户「请到 ima 内手动删除」，并附 `media_id` 便于定位。

---

## 十三、公众号文章通道（原文归档，不改写）

**触发**：链接形如 `https://mp.weixin.qq.com/s/<id>`（`wx_article.is_article_url()` 判定）。
这类链接**自动**走文章通道，无需额外参数；`auto_run.py` 在 `main()` 里分流。

### 13.1 与视频通道的区别

| | 视频通道 | 文章通道 |
|---|---|---|
| 输入 | 视频号链接 / 直链 / 文件 | 公众号文章链接 |
| 输出 | 笔记（Agent 或 Qwen 生成） | **原文归档**（元信息头 + 正文） |
| 是否改写 | 是 | **否，一字不改** |
| 成本 | Agent 模式 0 | 0 |
| 发布目录 | `outputs/公众号视频项目/<分类>/` | `outputs/公众号文章/` |

### 13.2 抓取原理

- 纯 HTTP GET 即可，**无需登录 / Cookie / 签名**，无反爬拦截
- 页面锚点：
  - 标题：`var msg_title` 或 `<h1 class="rich_media_title">` 或 `og:title`
  - 公众号名：`var nickname` / `#js_name` / `data-nickname`
  - 作者：`og:article:author` / `var author`
  - 发布时间：`var ct`（Unix 秒）
  - 正文：`<div class="rich_media_content" id="js_content">`，**结束边界是 `</div>\s*<script`**
  - 配图：`<img data-src="...">`（mmbiz.qpic.cn 外链，不本地化）

### 13.3 保真转换的 4 个坑（代码已处理，勿改顺序）

1. `<strong>` 常嵌在 `<span leaf>` 里 → **必须先转 `**` 再清 span**，否则加粗全丢
2. `<li>` 内部自带 `•` / `1.` 标记 → 去重，避免出现 `- •`
3. `</p>` → 双换行，否则段间被压成一行
4. 图片 URL 含 `)`，简单正则会截断 → 用占位符注册表再回填

自检口径：`**` 计数为偶数、无残留 HTML 标签、图片链接闭合。

### 13.4 命令与产出

```bash
python auto_run.py "https://mp.weixin.qq.com/s/XXXX"
```
- `output/<ts>_wechat_article/notes/<文章标题>.md`（最终交付物）
- `output/<ts>_wechat_article/article_raw.html`（原始 HTML，中间产物）
- `output/<ts>_wechat_article/notes/.ima_pending.json`（待上传清单）
- 发布副本：`outputs/公众号文章/<文章标题>.md`（文件名不加前缀）

### 13.5 代码位置

- `modules/wx_article.py`：`is_article_url()` / `fetch()` / `to_markdown()` / `html_to_markdown()` / `filename_for()`
- `auto_run.py`：`run_article()` / `write_article_pending()`，`main()` 按 `is_article_url` 分流

---

## 十四、开发与维护

### 14.1 ⚠️ Skill 三处副本必须同步

改一处漏同步 → 新会话会读到旧版：

| # | 位置 | 说明 |
|---|---|---|
| 1 | `skill/video-analyzer/` | **仓库正式源（入库）** |
| 2 | `.workbuddy/skills/video-analyzer/` | 工作副本（**已被 .gitignore 排除**） |
| 3 | `~/.workbuddy/skills/video-analyzer/` | 全局安装，**Agent 新会话实际读这个** |

同步命令：
```bash
cp skill/video-analyzer/SKILL.md .workbuddy/skills/video-analyzer/SKILL.md
cp skill/video-analyzer/SKILL.md ~/.workbuddy/skills/video-analyzer/SKILL.md
```

### 14.2 本地备份（改代码前必做）

```bash
cd <工具根> && B="_backup_$(date +%Y%m%d)" && mkdir -p "$B" \
  && cp -r modules "$B/modules" && cp auto_run.py step2_analyze.py "$B/"
# 回退
cp -r $B/modules/* modules/ && cp $B/auto_run.py $B/step2_analyze.py .
```

### 14.3 关键代码位置

| 文件 | 职责 |
|---|---|
| `auto_run.py` | 主编排：`main()` 分流、`run_one()`、`run_batch()`、`run_stages()`、`write_agent_input()`、`write_agent_pending()`、`publish()` |
| `modules/analyzer.py` | `ContentAnalyzer`：`run_full(facts_only=)` / `_classify` / `_visual_pass` / `_extract_facts` / `_write_tutorial` |
| `modules/frames.py` | `extract_keyframes()`（分桶择优）+ **`build_contact_sheet()`**（缩略图拼版） |
| `modules/ocr.py` | RapidOCR（**本地、零成本、带时间戳**） |
| `modules/transcribe.py` | Whisper（>300s 自动切 small） |
| `modules/wx_download.py` | `SphWorkerClient`（云端 Worker）/ `MCPClient`（本地下载器） |
| `modules/wx_article.py` | 公众号文章通道 |

### 14.4 已验证的修复记录

| 日期 | 问题 | 修复 |
|---|---|---|
| 2026-10-06 | `step2_analyze.py` 调用 `extract_keyframes(threshold=…)` 但该函数**无此形参** → 必然 `TypeError`（两步工作流是坏的） | 删掉 `threshold=`（已核实 `analyze()` / `classify_content()` / `templates/obsidian.md` 均存在且签名匹配，删这一行即可跑通） |
| 2026-10-06 | `config.yaml` 的 `frames.max_frames: 20` 在主链路无效 | 已记录实际为 36；`frames.threshold` 是死配置 |
| 2026-10-06 | Agent 模式仍会发布 Qwen 版到 `outputs/` | `--agent-write` 跳过 `publish()` 与 `write_ima_pending()` |

### 14.5 发版 SOP

1. 更新版本号与 README；
2. `git add` / `git commit`（**注意排除 `_backup_*/`、`scripts/_ima_up*/**.json`、`output/`**）；
3. 推送**必须走本机代理** `http://127.0.0.1:7897`：
   偶发 `schannel: server closed abruptly` → 加 `-c http.proxy=… -c https.proxy=…` 重试；
4. 打包：`git archive --format=zip --prefix=<name>-<ver>/ -o <out>.zip HEAD`；
5. 打 tag 并推送；GitHub MCP **没有 create_release** → 走 REST + curl（代理）创建 release 与附件；
6. 用 `mcp__github__get_latest_release` 校验。
