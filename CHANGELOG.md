# 更新日志

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
