# 更新日志

## v0.4.0 —— 2026-10-06

**Agent 写笔记模式：默认纯本地、零 API 成本**

### 新增

- **`--agent-write` 模式（新的默认工作流）**：流水线只产出**干净素材**，
  视觉理解与写笔记全部交给 Agent
  - 产出 `notes/agent_input.json`：转录 + `facts` + 画面 OCR（带时间戳）+
    关键帧路径 + **缩略图拼版** + `frame_profile` + `ocr_stats` + `agent_rules`
  - **默认纯本地**：连 `ContentAnalyzer` 都不构造 → **跳过分类 / 视觉理解 / 事实抽取
    三个 LLM 调用**，成本 **¥0**（实测）
  - 新增 `--with-vision`：需无人值守超大批量时，一键保留付费视觉模型
  - 新增启动器 `run_agent.cmd`
- **缩略图拼版 `build_contact_sheet()`**：把全部关键帧拼成一张网格图（每格标时间戳），
  Agent **读 1 张图即可纵览全片画面**（实测 17 帧 → 1680×474 / 98KB）
- **自适应信号**：`frame_profile`（画面类型）+ `ocr_stats`（每帧字数）+
  `agent_rules.adapt_rule`（文稿类走 OCR、视觉类按需读原帧、口播类只写转录）
- **待办清单**：`notes/.agent_pending.json`、批量 `.agent_pending_batch.json`
- **ima 上传助手** `scripts/_ima_up2/up_one.py`（单文件 COS 上传）

### 改进

- `run_stages(..., facts_only=, local_only=)`：支持「只出事实」/「纯本地」两种裁剪
- `run_full(..., facts_only=True)`：抽完事实即返回（跳过教程/速览/清单/自检）
- **Agent 模式保留 `frames/`**（便于回溯画面来源，也让 Agent 可按需看图）
- Agent 模式**不再发布 Qwen 版**到 `outputs/`，也不再写 `cost.json`
- `write_batch_summary()` 在 Agent 模式下展示 `agent_input` 产物名

### 修复

- **`step2_analyze.py` 崩溃**：调用 `extract_keyframes(threshold=…)`，
  但该函数**没有 `threshold` 形参** → 必然 `TypeError`（两步工作流是坏的）。
  已删除该参数（其余调用签名均已核实匹配）
- **`.gitignore` 安全漏洞**：`scripts/_ima_up*/**.json` 含 COS 临时密钥却未被忽略
  （164 个文件）→ 已补；同时补 `_backup_*/`、根 `output/`

### 文档

- `SKILL.md` **全面重写**（497 → 618 行）：新增 Agent 写笔记 SOP、两种模式对比、
  `agent_input.json` 结构与取数硬约束、缩略图拼版、「规则之外由 Agent 现场适配」、
  开发维护（三副本同步 / 备份 / 修复记录 / 发版 SOP）
- `references/pipeline-commands.md`：补 `--agent-write` / `--with-vision` / 上传脚本
- `references/tutorial-templates.md`：改为「按内容定结构」指南 + 反面教材（旧模板仅供传统模式）

### 实测数据

| 项目 | 结果 |
|---|---|
| 纯本地成本 | **¥0**（vs 传统模式 ¥0.04–0.11/条） |
| 2 分钟视频全流程 | 约 3 分钟（转录占大头） |
| 同题新旧对比 | 旧 7,074 字（把"自适应组件"听写成"自摄用组件"）→ 新 6,652 字（用词正确，且多出 12 套主题表、控制台维度、GitHub 真实地址） |

---

## v0.3.2 —— 2026-10-04

**批量处理：一次给多条链接 + 断点续跑**

### 新增

- **批量模式**：一次处理多条链接，三种输入方式
  - 多条位置参数：`run_auto.cmd "链接1" "链接2" ...`
  - 文件输入：`run_auto.cmd --from-file links.txt`（一行一条，`#` 注释，空行忽略）
  - 自动去重（保序）
- **断点续跑 `--resume <批次目录>`**：读取 `batch_state.json`，
  跳过已成功项、只重试失败项；状态**每跑完一条即落盘**，任意时机中断都能接上
- **批次汇总表** `_batch_<时间戳>.md`：序号/状态/类型/标题/耗时/成本/产物，
  失败项附「可直接复制重跑」链接段
- **批量 ima 待上传清单** `.ima_pending_batch.json`：汇总全部成功项
  （含各自 folder_hint），供 Agent 一次性批量上传 + 校验

### 改进

- `main()` 重构：单条处理逻辑抽为 `run_one()`，返回统一结构
  （`ok` / `source_type` / `video_type` / `title` / `run_dir` / `final_md` /
  `pending` / `notes` / `elapsed_sec` / `cost_cny` / `error`），
  供批量层汇总；单条模式输出格式与退出码**保持完全兼容**
- 批量模式下每条仍是独立 run 目录（沿用 `output/`），同一秒重名自动加后缀
- `run_auto.cmd` 注释补批量与续跑用法

### 修复

- 批次状态写入时 `Path` 对象无法 JSON 序列化（`TypeError`），
  原被 `except: pass` 静默吞掉，导致 `--resume` 永远拿不到历史记录。
  现加 `_json_default` 处理 Path/set，并**不再静默吞异常**（失败会打日志）

---

## v0.3.1 —— 2026-10-04

**文档补漏：明确「云端解析需自部署 Worker」+ 补齐部署模板**

### 修复

- **README 从未说明云端解析需要自部署 Cloudflare Worker**，读者会误以为开箱即用。
  实际上 `tools/wx_channel/` 整个被 `.gitignore` 排除（含凭据与域名配置），
  克隆仓库后云端通道必然不可用，只能降级到需开微信的旧通道。现已补齐完整说明。
- `scripts/setup.py` 收尾提示把「打开微信进视频号页面」写成必做步骤，
  事实是仅兜底通道需要；现改为**双通道并列表述**，推荐路径为部署 Worker。

### 新增

- `tools/wx_channel/config.example.yaml`：Worker 部署所需配置模板
  （含 `accountid` / `apitoken` / `sphcookie` / `sphhostname` 及两处 YAML 坑位注释）
- README **§3.7 Step 6「部署云端解析 Worker」**：完整四步（建 Token → 填配置 →
  `sph_deploy` → 验证），含 cookie 双引号坑与 `--config` 路径坑
- README 新增 FAQ 两条：为何需要部署、为何仓库里没有 `tools/wx_channel/`

### 改进

- 设计原则表、工具清单、FAQ 中所有「免登录」表述补充**前置条件**，
  不再暗示零配置可用
- `.gitignore` 规则细化：`tools/wx_channel/*` + 负向规则保留 `config.example.yaml`，
  真实 `config.yaml` / `wx_channel.exe` / 设备指纹仍被排除
- 两份 SKILL.md 的云端解析章节开头补「前置条件」警告，
  并说明未部署时的自动降级路径（功能不中断）
- README 目录结构补 `tools/wx_channel/` 条目

---

## v0.3.0 —— 2026-10-04

**云端解析免登录 + 公众号文章通道 + 单文件整合稿 + ima 自动上传**

### 新增

- **视频号云端解析通道**：`modules/wx_download.py` 新增 `SphWorkerClient`，
  经 Cloudflare Worker 调用微信 `get_feed_info` 接口（**免鉴权**）拿未加密直链，
  **无需登录微信、无需本地下载器进程**，2-5 秒出直链；支持 `SPH_WORKER_URL` 覆盖
- **公众号文章通道**：`modules/wx_article.py`，抓取 `mp.weixin.qq.com/s/...` 文章并
  **保真转为 Markdown**（只还原结构、不改写一字）；`auto_run.py` 按链接类型自动分流
- **单文件整合稿**：`merge_notes()` 把 tutorial / brief / checklist 三合一，并追加
  **音频文案段**（视频原声逐句转录，带 `mm:ss` 时间戳）
- **可读文件命名**：整合稿改用 `{作者}：{描述}` 命名，不再用分享 ID
  （取 `SphWorkerClient.meta_title()`，三级兜底）
- **ima 自动上传**：`modules/ima_upload.py`（create_media → COS PUT → add_knowledge），
  每次产出写 `.ima_pending.json` 待上传清单
- `docs/prompts.md`：提示词合集

### 改进

- 视频号下载优先级改为 **① 云端 Worker（免登录）→ ② 本地 MCP → ③ wx_channel 服务/手动**
- `requirements.txt` 新增 `cos-python-sdk-v5`（ima COS 上传依赖）
- `.gitignore` 新增 `.backups/`
- Skill 文档大幅扩充（云端解析、整合稿、ima 上传 SOP、公众号文章通道）

### 修复

- `wx_download.py` 第 28 行 `DOWNLOADER_DIR` 未导入（NameError，会让整个下载层 import 即崩）

### 约定（硬约束）

- **只有 ima 回读校验命中（title 或 media_id 一致）才能删本地 md**
- ima MCP 无删除接口，重复上传会遗留旧条目，需人工清理

---

## v0.2.0 —— 2026-09-19

**一键安装 + 隐私脱敏 + 路径无关化**

### 新增

- `installer/wx_video_download_v260907_windows_x86_64.zip`：内置视频下载器安装包
- `scripts/setup.py` / `scripts/setup.cmd`：一键安装配置（依赖、下载器解压、配置生成、ffmpeg 检查）
- `scripts/start_downloader.vbs`：下载器开机自启 / 静默启动脚本
- `tools/wx_channels_download/config.example.yaml`：下载器配置模板
- `skill/video-analyzer/`：可直接复制到 Agent 技能目录的 Skill 副本
- `docs/prompts.md`：提示词合集
- `tools/weixin-favor-kb/modules/paths.py`：统一路径解析
- 完整重写 `README.md`

### 改进

- **路径无关化**：移除所有硬编码的绝对路径，改由 `modules/paths.py` 相对解析；
  ffmpeg 支持 `FFMPEG_BIN` 环境变量 / PATH / 仓库内置目录三级查找
- **下载器自动拉起**：`modules/wx_download.py` 新增 `ensure_downloader()`，
  端口未通时静默启动下载器，Agent 通常无需手动介入
- **MCP 端口可配**：支持 `WX_DOWNLOAD_MCP` / `WX_DOWNLOAD_PORT` 环境变量
- **启动器稳健化**：`run_auto.cmd`、`step1_transcribe.cmd`、`step2_analyze.cmd`
  改用 `%~dp0` 定位，任意目录安装都能跑；venv 不存在时自动回退系统 python

### 安全

- 配置模板化：真实 API Key、下载器凭据等一概不入库，`.gitignore` 补齐排除项
- 文档中的本机路径 / 内网 IP / 个人域名全部占位符化

### 修复

- Whisper 视频时长读取异常导致长视频误用大模型
- 笔记字段偶发返回字符串而非列表导致逐字拆行
- 抽帧候选产生大量临时文件触发清理保护

---

## v0.1.0 —— 2026-09-12

首个提交：微信视频号 AI 视频解析工作流 + video-tutorial-generator Skill。

- 微信视频号下载 → Whisper 转录 → 关键帧提取 + OCR → Qwen3-VL 视觉分析 → 结构化笔记
- 自动判定视频类型，输出「项目复刻教程」或「效率提升教学」
- Skill 封装，支持自然语言触发
