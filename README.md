# 微信视频号 · AI 视频解析工作流

> 一条链接 → 自动解析 → 结构化笔记 / 复刻教程 / 功能教学

<div align="center">

**下载视频 → 语音转录 → 画面理解 → 类型判定 → 教程生成**

[工作流](#-项目介绍与目标) · [快速开始](#-从零开始的完整搭建步骤) · [踩坑记录](#-我实际安装时踩过的坑) · [Prompts](#-附带的提示词)

</div>

---

## 目录

- [一、项目介绍与目标](#一项目介绍与目标)
- [二、从零开始的完整搭建步骤](#二从零开始的完整搭建步骤)
- [三、需要下载安装的工具清单](#三需要下载安装的工具清单)
- [四、如何结合 Agent 安装配置](#四如何结合-agent-安装配置)
- [五、附带的提示词及使用方法](#五附带的提示词及使用方法)
- [六、关键点和常见问题](#六关键点和常见问题)
- [七、我实际安装时踩过的坑](#七我实际安装时踩过的坑)

---

## 一、项目介绍与目标

### 1.1 这个项目做什么

把**微信视频号**（或任意本地视频）自动解析成高质量知识内容。工作流分两步：

1. **视频解析**：音频提取 → Whisper 语音转文字 → 关键帧提取 → OCR 画面文字 → Qwen3-VL 视觉理解 → 生成结构化 Obsidian 笔记。
2. **教程生成**：自动判定视频类型（项目教学 / 功能教学），输出**项目复刻教程**或**效率提升教学**，每个知识点都含操作步骤、注意事项、FAQ、参数说明、原理。

### 1.2 核心价值

| 传统方式 | 本项目 |
|----------|--------|
| 反复听、暂停、手打笔记 | 一键转录 + AI 整理 |
| 只看得到「说了什么」 | 既「听」又「看」，画面/图表/代码都能理解 |
| 笔记散乱无结构 | 结构化 Obsidian 笔记 + 带时间戳分段 |
| 视频看完不会动手 | 直接产出可照着做的复刻教程 |

### 1.3 技术栈

```
语音转录：faster-whisper（medium/small，本地 CPU 推理）
画面文字：RapidOCR（onnxruntime）
视觉理解：Qwen3-VL-32B-Instruct（硅基流动 API）
音频处理：ffmpeg 8.1.2
视频下载：wx_channel（微信视频号）
笔记模板：Jinja2 → Obsidian Markdown
```

### 1.4 目录结构

```
.
├── tools/
│   ├── wx_channel/              # 微信视频号下载工具
│   ├── ffmpeg/                  # 音频处理
│   └── weixin-favor-kb/         # 核心解析流水线
│       ├── step1_transcribe.cmd # 第一步：转录
│       ├── step2_analyze.cmd    # 第二步：分析
│       ├── transcribe_only.py
│       ├── step2_analyze.py
│       ├── config.yaml          # 模型/API 配置
│       ├── models/              # 本地 Whisper 模型
│       ├── templates/           # 笔记模板
│       ├── downloads/           # 视频存放处
│       └── output/              # 解析结果（隔离目录）
├── outputs/                     # 生成的教程
│   └── 公众号视频项目/
│       ├── 项目复刻教学/
│       └── 功能提升教学/
└── .workbuddy/skills/video-analyzer/  # Skill（触发词 + 模板）
```

---

## 二、从零开始的完整搭建步骤

> 以下按顺序执行，每步都有验证方法。

### 第 1 步：安装 Python 3.12

⚠️ **必须用 3.12**，不要用 3.13（不兼容 tokenizers）。

1. 下载 Python 3.12.x：https://www.python.org/downloads/
2. 安装时勾选 **「Add Python to PATH」**
3. 验证：

```bash
python --version
# 期望输出：Python 3.12.x
```

### 第 2 步：安装 ffmpeg

1. 下载 Windows 版：https://www.gyan.dev/ffmpeg/builds/ （选 `essentials` 版）
2. 解压到 `tools/ffmpeg/`
3. 把 `bin/` 目录加入系统 PATH
4. 验证：

```bash
ffmpeg -version
```

### 第 3 步：克隆项目

```bash
git clone <你的仓库地址>
cd <项目目录>
```

### 第 4 步：创建 Python 虚拟环境并安装依赖

```bash
cd tools/weixin-favor-kb
python -m venv venv

# Windows 激活虚拟环境
venv\Scripts\activate

# 用清华镜像加速（国内必备）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

依赖清单（`requirements.txt`）：

```
faster-whisper>=1.1.0
opencv-python-headless>=4.9.0
rapidocr-onnxruntime>=1.3.0
openai>=1.30.0
pyyaml>=6.0
jinja2>=3.1.0
numpy>=1.26.0
loguru>=0.7.0
rich>=13.0.0
```

### 第 5 步：下载 Whisper 本地模型

两个模型（medium 高精度 + small 快速），放到 `tools/weixin-favor-kb/models/`：

```
models/
├── whisper-medium/
│   ├── model.bin      # ~1.5 GB
│   ├── config.json
│   ├── tokenizer.json
│   └── vocabulary.txt
└── whisper-small/
    ├── model.bin      # ~500 MB
    ├── config.json
    ├── tokenizer.json
    └── vocabulary.txt
```

**下载方式**（推荐 ModelScope，阿里云 CDN 快）：

1. 打开 https://modelscope.cn/models/pengzhendong/faster-whisper-small/files
2. 分别下载 `model.bin`、`config.json`、`tokenizer.json`、`vocabulary.txt`
3. medium 模型同理：搜索 `faster-whisper-medium`

> 💡 也可以用 `hf-mirror.com`，但实测很慢，ModelScope 3 分钟下完 500MB。

### 第 6 步：配置 API

编辑 `tools/weixin-favor-kb/config.yaml`：

```yaml
llm:
  api_key: "sk-你的硅基流动APIKey"   # ← 替换成你自己的
  base_url: "https://api.siliconflow.cn/v1"
  model: "Qwen/Qwen3-VL-32B-Instruct"
```

> 注册硅基流动：https://siliconflow.cn ，新用户有免费额度。

### 第 7 步：下载视频（可选，用 wx_channel）

`tools/wx_channel/wx_channel.exe` 用于批量下载微信视频号收藏，双击运行后按界面操作，导出到 `downloads/`。

也可以直接把任意 `.mp4` 文件手动放进 `downloads/`。

### 第 8 步：运行流水线

**第一步：转录**（慢，20-30 分钟，会弹出窗口选择视频）

```bash
# 双击运行，会交互式列出 downloads/ 下的视频
step1_transcribe.cmd
# 或命令行指定视频
step1_transcribe.cmd "downloads\你的视频.mp4"
```

**第二步：分析**（快，2-3 分钟）

```bash
step2_analyze.cmd <第一步输出的 run_id>
```

完成！笔记在 `output/<run_id>/notes/`。

---

## 三、需要下载安装的工具清单

| 工具 | 版本要求 | 用途 | 下载地址 |
|------|----------|------|----------|
| Python | **3.12.x**（勿用 3.13） | 运行环境 | python.org |
| ffmpeg | 8.1.2 essentials | 音频提取 | gyan.dev |
| wx_channel | v5.6.9 | 视频号下载 | github.com/nobiyou/wx_channel |
| Whisper medium | faster-whisper 版 | 高精度转录 | ModelScope |
| Whisper small | faster-whisper 版 | 快速转录 | ModelScope |
| 硅基流动账号 | — | Qwen3-VL API | siliconflow.cn |

**Python 依赖**：见第 2 节第 4 步的 `requirements.txt`。

---

## 四、如何结合 Agent 安装配置

这套工作流配了一个 **Skill**，让你在 WorkBuddy 里直接用自然语言触发。

### 4.1 Skill 位置

```
.workbuddy/skills/video-analyzer/
├── SKILL.md                       # 触发方式、输入参数、执行步骤
└── references/
    ├── tutorial-templates.md      # 两种教程的模板
    └── pipeline-commands.md       # 精确命令与路径
```

### 4.2 触发方式

对 Agent 说以下任一表达即可：

| 说法 | 效果 |
|------|------|
| 「分析视频 [链接]」 | 下载 + 解析 + 出笔记 |
| 「[链接] 生成复刻教程」 | 直接出项目复刻教程 |
| 「[run_id] 展开为功能教程」 | 跳过解析，直接出功能教学 |

### 4.3 输入参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `video_source` | 是 | 视频链接 / 本地路径 / run_id |
| `output_type` | 否 | `复刻` / `功能`，缺省自动判定 |
| `style` | 否 | `详细` / `精简` |

### 4.4 完整流程

```
获取视频 → 转录(step1) → 视觉分析(step2) → 判定类型 → 生成教程
```

---

## 五、附带的提示词及使用方法

### 5.1 视觉分析提示词

发送给 Qwen3-VL-32B，让它结合画面和语音输出结构化结果：

```text
你正在分析一个视频的完整内容。以下是关键帧截图（按时间顺序）。

📝 视频语音转录：
<转录文字>

📊 画面中识别到的文字（OCR）：
<OCR文字>

请仔细观察每一张截图中的界面操作、软件演示、图表、PPT、产品界面。
结合语音转录和画面内容，输出 JSON：
{
  "visual_observations": "描述画面中看到了什么",
  "summary": "3-5句摘要",
  "key_points": ["要点1", "要点2"],
  "resources": ["工具/网站/书籍"],
  "action_items": {
    "dev": ["开发建议"],
    "life": ["生活建议"],
    "tech_summary": ["技术总结"]
  }
}
```

### 5.2 教程生成提示词（判定类型后使用）

**项目复刻教程**：

```text
请基于以下视频内容，输出一份「项目复刻教程」。
读者需要打造自己的版本，含完整复刻步骤。
每个知识点必须包含：
1. 详细操作步骤（逐步）
2. 关键注意事项
3. 常见问题及解决方案（FAQ 表）
4. 参数配置说明
5. 原理简述
结构：项目概述 → 环境搭建 → 逐模块实现 → 测试调试 → 部署上线 → 个性化定制 → 工作流总结
```

**效率提升教学**：

```text
请基于以下视频内容，输出一份「效率提升教学」。
读者直接学方法，无需打造自己的版本。
每个知识点必须包含：
1. 详细操作步骤
2. 关键注意事项
3. 常见问题及解决方案（FAQ 表）
4. 参数配置说明
5. 原理简述
结构：工具概览 → 安装上手 → 核心操作 → 进阶技巧 → 竞品对比 → 流程总结
```

### 5.3 项目需求讨论提示词（示例，来自视频实战）

```text
这是一个微信小程序目录。我想做一个叫「看球 MBTI」的测试小程序。

整体流程：
1. 首页，点击「立即测试」
2. 回答约 10 道选择题
3. 根据答案计算人格维度得分
4. 生成性格卡片（类型、分析、分享）

这是简单项目，不需要后端。
你说说你准备怎么实现？或者有什么问题想问我？
```

> 先讨论需求、让 AI 列计划，再逐步实现，比一次性生成大量代码更可控。

---

## 六、关键点和常见问题

### 6.1 关键点

- **转录必须独立进程跑**：CPU 转录 35 分钟视频约需 20-30 分钟，后台任务会超时，务必用 `.cmd` 双击运行。
- **Windows 编码**：脚本已内置 UTF-8 环境变量，勿删除。
- **数据隔离**：每次运行输出到 `output/<时间戳>_<视频名>/`，不会覆盖旧数据。
- **长视频自动降级**：超过 5 分钟自动用 small 模型，快 3 倍。

### 6.2 常见问题

| 问题 | 解决方案 |
|------|----------|
| `.cmd` 双击闪退 | 检查 Python 路径、确认已 `pip install -r requirements.txt` |
| 转录乱码 | 确认设置了 `PYTHONIOENCODING=utf-8` |
| 找不到视频 | 视频须放在 `downloads/`，且是 `.mp4`/`.mov` |
| 生成笔记逐字拆行 | 已修复，升级到最新代码 |
| API 调用失败 | 检查 config.yaml 的 api_key 和余额 |
| 无 GPU 能不能跑 | 能，CPU 模式即可，只是慢 |

---

## 七、我实际安装时踩过的坑

> 这些是真实踩过的坑，按出现顺序记录，附解决方案。

### 坑 1：Python 3.13 装不上 tokenizers

**现象**：`pip install faster-whisper` 报 tokenizers 编译失败。

**解决**：改用 Python 3.12。faster-whisper 依赖的 ctranslate2/tokenizers 尚未适配 3.13。

### 坑 2：pip 下载慢到怀疑人生

**现象**：依赖包几十 KB/s，半天装不完。

**解决**：用清华镜像。

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 坑 3：Whisper 模型从 hf-mirror 下载极慢

**现象**：hf-mirror.com 下载 model.bin 卡在 0.5%，预计要十几小时。

**解决**：换 **ModelScope**（阿里云 CDN），500MB 约 3 分钟下完。

### 坑 4：Windows GBK 编码导致 emoji 崩溃

**现象**：程序在打印 `🎬` 等 emoji 时崩溃，报 `UnicodeEncodeError: 'gbk' codec can't encode character`。

**解决**：运行前设置环境变量。

```bash
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8
```

### 坑 5：cv2.imwrite 不支持中文路径

**现象**：关键帧保存到中文目录时静默失败。

**解决**：改用 `np.fromfile` + 二进制写入绕过 OpenCV 的路径处理。

### 坑 6：平台后台任务约 10 分钟超时

**现象**：把整个流水线丢给 Agent 后台跑，转录跑到一半被杀。

**解决**：拆成两步（step1 转录 + step2 分析），step1 用 `.cmd` 在独立进程运行。

### 坑 7：ffprobe 编码错误导致误用 medium 模型

**现象**：ffprobe 读不到音频时长（返回 0），长视频误用 medium 模型，慢到超时。

**解决**：`subprocess.run` 加 `encoding="utf-8", errors="replace"`。

### 坑 8：文件名含特殊字符导致建目录失败

**现象**：视频名带 `# ! ?` 等字符，`os.mkdir` 报 `FileNotFoundError`。

**解决**：建目录前先 `sanitize()` 清洗，移除 `< > : " / \ | ? * #`。

### 坑 9：笔记「技术总结」逐字拆行

**现象**：Qwen 偶发把 `tech_summary` 返回成字符串而非列表，模板按字符逐个渲染。

**解决**：加 `_normalize_list` 函数，字符串统一包装为列表；模板再 `replace("\n", " ")` 兜底。

---

## 许可证与致谢

- 转录与分析核心：[dlv2008/weixin-favor-kb](https://github.com/dlv2008/weixin-favor-kb)
- 视频号下载：[nobiyou/wx_channel](https://github.com/nobiyou/wx_channel)
- 本项目在其基础上二次开发，新增：视觉分析、教程生成、Skill 封装、数据隔离、长视频降级等。

---

<div align="center">

**一条链接，一份教程。** 🎬 → 📝

</div>
