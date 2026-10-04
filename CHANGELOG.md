# 更新日志

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
