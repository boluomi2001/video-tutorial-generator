# 视频解析工作流 · video-tutorial-generator

> 丢一个视频链接进去，出来一篇能照着做的中文教学笔记。
> 视频号 / B站 / 抖音 / 快手 / YouTube / 本地文件，一条命令全自动。

![流程](https://img.shields.io/badge/流程-下载%20→%20转录%20→%20抽帧%20→%20视觉理解%20→%20教学笔记-blue)
![Python](https://img.shields.io/badge/Python-3.10%20~%203.12-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20推荐-orange)

---

## 目录

- [1. 项目介绍与目标](#1-项目介绍与目标)
- [2. 效果与产出](#2-效果与产出)
- [3. 从零开始：完整搭建步骤](#3-从零开始完整搭建步骤)
- [4. 工具清单与版本要求](#4-工具清单与版本要求)
- [5. 结合 Agent 安装配置（推荐用法）](#5-结合-agent-安装配置推荐用法)
- [6. 提示词 Prompts 与使用方法](#6-提示词-prompts-与使用方法)
- [7. 输出结构说明](#7-输出结构说明)
- [8. 关键点与常见问题 FAQ](#8-关键点与常见问题-faq)
- [9. 我实际踩过的坑与解决方案](#9-我实际踩过的坑与解决方案)
- [10. 隐私与安全](#10-隐私与安全)
- [11. 目录结构](#11-目录结构)
- [12. 许可证与致谢](#12-许可证与致谢)

---

## 1. 项目介绍与目标

收藏夹里塞满了"回头再看"的视频，最后一条也没看完。这套工作流就是为了解决这件事。

**目标：从「一个视频链接」到「一篇能照着做的中文教学笔记」，中间不需要人参与。**

### 1.1 它做了什么

```
视频链接 / 本地文件
   │
   ├─ ① 下载        视频号走微信内解密；B站/抖音/快手/YouTube 等平台直接下载
   │
   ├─ ② 转录        Whisper（medium / small 自适应）本地转写，不上云
   │
   ├─ ③ 抽帧 + OCR  分桶择优抽帧，识别画面里的文字（代码、命令行、参数表）
   │
   ├─ ④ 视觉理解    Qwen3-VL 逐章解读画面 + 事实抽取（命令/包名/URL/版本/坑）
   │
   └─ ⑤ 生成笔记    三形态输出 + 自动清理临时文件
```

### 1.2 和"看视频记笔记"的区别

| 传统方式 | 本项目 |
|---|---|
| 反复听、暂停、手打 | 一键转录 + AI 整理 |
| 只听得到"说了什么" | 既听又看：界面操作、终端输出、PPT 图表都能进笔记 |
| 笔记散乱无结构 | 分章节、带时间戳、含参数表 |
| 看完还是不会动手 | 直接产出可执行清单 |

### 1.3 两种输出，自动判定

- **项目复刻教程** —— 视频在教你搭一个东西（小程序、网站、工具接入…）
- **效率提升教学** —— 视频在教你用一个方法（Codex 技巧、Prompt 写法、软件操作…）

每个知识点都必须包含：**操作步骤 / 注意事项 / FAQ / 参数说明 / 原理**，这是硬要求，不是锦上添花。

### 1.4 设计原则

| 原则 | 做法 |
|---|---|
| 本地优先 | 转录与抽帧全在本地跑，只有语义理解调云端模型 |
| 便宜 | 单次全流程约 **¥0.1**（2 分钟视频） |
| 抗超时 | 各阶段独立失败降级，不会一步崩全盘 |
| 免人工 | 除"打开微信"这一步，其余全部自动 |

---

## 2. 效果与产出

一次跑完产出三个 Markdown 文件：

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| `tutorial.md` | 完整长文：分章节，含步骤 / 注意 / FAQ / 参数 / 原理 | 想彻底学会 |
| `brief.md` | 一屏速览：一句话总结 + 要点 + 适合谁 | 只想快速知道讲啥 |
| `checklist.md` | 勾选清单：每步配可执行命令 | 想照着做一遍 |

实测参考（普通 CPU 机器，无 GPU）：

| 项目 | 数值 |
|---|---|
| 2 分钟视频全流程 | 约 4-6 分钟 |
| Whisper medium 转录 | 约 113 秒 / 2 分钟视频 |
| 视觉理解（MoE 30B-A3B） | 4 图一批 13-19 秒 |
| 大模型花费 | 约 ¥0.07 ~ ¥0.11 |

---

## 3. 从零开始：完整搭建步骤

> 全程约 20 分钟，其中大部分在等下载。**按顺序做，每步都有验证方法。**
> 想偷懒可直接跳到第 3.3 节的自动安装脚本（一次搞定依赖、下载器、配置三件事）。

### 3.1 Step 0 —— 准备 API Key

流水线只有**语义理解**这一步要用云端模型。推荐[硅基流动 SiliconFlow](https://cloud.siliconflow.cn/)，新用户有免费额度。

1. 注册 → 控制台 → API 密钥 → 新建密钥
2. 得到形如 `sk-xxxxxxxx` 的 Key，**先记在心里，别急着写进文件**

默认模型（可在 `config.yaml` 里换）：

| 用途 | 模型 | 说明 |
|---|---|---|
| 视觉理解 / 教程生成 | `Qwen/Qwen3-VL-30B-A3B-Instruct` | MoE，4 图批次 13-19 秒，稳定 |
| 分类 / 速览 / 清单 / 自检 | `Qwen/Qwen2.5-7B-Instruct` | 纯文本，约 0.5 秒，便宜 |

### 3.2 Step 1 —— 拉取代码

```powershell
git clone https://github.com/boluomi2001/video-tutorial-generator.git
cd video-tutorial-generator
```

不想用 Git 也行：直接下载 ZIP 解压，效果一样。

### 3.3 Step 2 —— 一键安装（依赖 / 下载器 / 配置）

**先确认 Python 版本必须是 3.10 ~ 3.12。** Python 3.13 会在 `tokenizers` / `faster-whisper` 这类带 Rust 扩展的包上翻车。

```powershell
python --version
```

版本对了，运行安装脚本：

```powershell
# 方式 A：Windows 双击或用 render 提示一路走下去
scripts\setup.cmd

# 方式 B：一句话搞定（推荐，Key 走环境变量不落盘到命令行历史）
set LLM_API_KEY=sk-你的Key
python scripts\setup.py --pip-mirror tsinghua

# 想连 ffmpeg 一起装：
python scripts\setup.py --pip-mirror tsinghua --fetch-ffmpeg
```

脚本会自动做完这些事：

1. 检查 Python 版本是否在安全区间
2. 解压 `installer/` 里的下载器到 `tools/wx_channels_download/`
3. 生成下载器配置（下载目录指向本项目、开启 MCP 端口 2022）
4. 生成流水线配置并填入 API Key
5. 检查 / 安装 ffmpeg
6. 创建 `venv` 并用清华源装依赖
7. 打印下一步该做什么、以及 MCP 注册片段

<details>
<summary>想手动装？点这里展开</summary>

```powershell
cd tools\weixin-favor-kb
python -m venv venv
.\venv\Scripts\python.exe -m pip install -U pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

国内**务必**加清华源，否则 OpenCV / Whisper 那一坨轮子能下到你怀疑人生。

</details>

### 3.4 Step 3 —— 安装 ffmpeg

抽音频和抽帧都靠它，三选一：

```powershell
# 推荐：winget
winget install Gyan.FFmpeg

# 或让脚本自动下载（约 100MB）
python scripts\setup.py --fetch-ffmpeg

# 或手动下载 essentials 版解压到 tools\ffmpeg\
# https://www.gyan.dev/ffmpeg/builds/
```

验证：`ffmpeg -version` 能出结果就行。装在 PATH 里或 `tools/ffmpeg/**/bin/` 都能被自动找到。

### 3.5 Step 4 —— 安装视频下载器

**仓库已经内置了 Windows 安装包**，不用去别处找：

```
installer\wx_video_download_v260907_windows_x86_64.zip
```

它由 `scripts/setup.py` 自动解压到 `tools/wx_channels_download/`，手动解压效果等价。

> 这是第三方开源项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) 的官方发行包，本项目只做整合，功劳归原作者。

<details>
<summary>Whisper 模型要不要手动下？</summary>

不用。首次转录时会自动下载 medium（约 1.5GB）。

如果自动下载太慢，可以从 [ModelScope](https://www.modelscope.cn/) 搜 `faster-whisper-medium` 手动下载 4 个文件（`model.bin` / `config.json` / `tokenizer.json` / `vocabulary.txt`）放进 `tools/weixin-favor-kb/models/whisper-medium/`。实测 ModelScope 比 hf-mirror 快十几倍。

</details>

### 3.6 Step 5 —— 配置文件

安装脚本会帮你生成，手动的情况下照这样复制：

```powershell
copy tools\weixin-favor-kb\config.example.yaml tools\weixin-favor-kb\config.yaml
copy tools\wx_channels_download\config.example.yaml tools\wx_channels_download\config.yaml
```

然后编辑 `tools\weixin-favor-kb\config.yaml` 的这一行：

```yaml
llm:
  api_key: "sk-你的硅基流动APIKey"   # ← 改成 Step 0 拿到的 Key
```

下载器配置里把下载目录指向流水线（**注意用正斜杠**）：

```yaml
download:
  dir: "你的仓库绝对路径/tools/weixin-favor-kb/downloads"
api:
  port: 2022
mcp:
  enabled: true        # 必须开，Agent 靠它调下载能力
```

### 3.7 Step 6 —— 跑通第一个视频

```powershell
# 1) 启动下载器（第一次会申请修改系统代理，允许即可）
tools\wx_channels_download\wx_video_download.exe

# 2) 打开电脑版微信，随便进到一个视频号页面，让微信保持运行
#    视频号视频是加密的，解密只能在微信里完成 —— 这是唯一不能自动化的环节

# 3) 丢链接进去
tools\weixin-favor-kb\run_auto.cmd "https://channels.weixin.qq.com/..."
```

想跳过下载先验证其余流程，直接喂本地文件：

```powershell
tools\weixin-favor-kb\run_auto.cmd "D:\我的视频\demo.mp4"
```

第一次会下载 Whisper 模型，耐心等。跑完到 `tools\weixin-favor-kb\output\<run_id>\notes\` 收成果。

---

## 4. 工具清单与版本要求

| 工具 | 版本要求 | 必须？ | 用途 | 获取 |
|---|---|---|---|---|
| Python | **3.10 ~ 3.12**（勿用 3.13） | ✅ | 运行流水线 | python.org |
| Git | 任意新版 | ✅ | 拉代码 | git-scm.com |
| ffmpeg | 6.x / 8.x essentials | ✅ | 抽音频、抽帧 | `winget install Gyan.FFmpeg` |
| 硅基流动 API Key | — | ✅ | 视觉理解与生成 | cloud.siliconflow.cn |
| wx_video_download | v260907+ | 视频号必须 | MCP 下载服务（端口 2022） | 仓库 `installer/` 内置 |
| 微信 PC 客户端 | 新版 | 视频号必须 | 提供解密环境 | pc.weixin.qq.com |
| Windows | 10 / 11 | 推荐 | 下载器只有 Windows 发行包 | — |
| NVIDIA GPU + CUDA | — | 可选 | 转录提速 5-10 倍 | 配 `device: cuda` |

Python 依赖（`requirements.txt`）：

```
faster-whisper>=1.1.0         # Whisper 转写
opencv-python-headless>=4.9.0 # 抽帧与画面打分
rapidocr-onnxruntime>=1.3.0   # 画面文字识别
openai>=1.30.0                # 调模型（OpenAI 兼容接口的服务都能用）
pyyaml / jinja2 / numpy / loguru / rich
```

磁盘预算：依赖约 2GB + Whisper medium 约 1.5GB + 视频缓存。**建议留 15GB 以上。**

---

## 5. 结合 Agent 安装配置（推荐用法）

装完之后最好用的方式，是把它接进你的 Agent（WorkBuddy / Claude Desktop / 任何支持 Skill 与 MCP 的客户端）。接好之后，你只需要把链接丢过去说"分析这个视频"。

### 5.1 三步接线

**① 安装 Skill**（让 Agent 知道怎么用这套流水线）

把 `skill/video-analyzer/` 整个目录复制到你的 Agent 技能目录：

| 客户端 | 目标路径 |
|---|---|
| WorkBuddy | `C:\Users\<你>\.workbuddy\skills\video-analyzer\` |
| Claude Desktop | 放进 Project Knowledge，或在对话里直接引用 `SKILL.md` |

**② 注册 MCP**（让 Agent 能直接调用下载能力）

客户端 MCP 配置文件里加一段：

```json
{
  "mcpServers": {
    "wx_channels_download": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:2022/mcp"
    }
  }
}
```

> WorkBuddy 用户：配置在 `C:\Users\<你>\.workbuddy\mcp.json`。
> 加完后去自定义连接器入口对新服务器点「信任」才会生效。

**③ 设置开机自启**（可选，但强烈建议）

把 `scripts/start_downloader.vbs` 的快捷方式放进 `shell:startup`（Win+R 输入该命令打开目录）。
之后开机下载器就在后台跑着，连手动启动这一步都省了。

### 5.2 直接让 Agent 帮你装

把整个仓库文件夹丢给 Agent，贴上第 6 节的安装提示词即可。

### 5.3 Agent 侧的硬性约束

这几条是实战教训，违反就会**静默失败**（没报错，但也没结果）：

1. **必须脱离进程启动**，启动后立刻返回。转录可能跑 20-30 分钟，前台或平台后台任务会被超时清理。
2. **轮询 `progress.json` 汇报进度**，不要阻塞等待。
3. **不要用系统 python 跑 `auto_run.py`**，缺依赖会 import 即崩，表现为"秒退无报错"。一律走 `run_auto.cmd`。
4. Agent 拉起下载器时要走**沙箱外**通道 —— 它启动时要调 `reg.exe` 写系统代理，沙箱内会被安全策略杀掉。

---

## 6. 提示词 Prompts 与使用方法

完整版见 [`docs/prompts.md`](docs/prompts.md)，下面是高频几条。

### 6.1 让 Agent 安装的提示词

```
这是个把我收藏的视频自动转成教学笔记的工作流，请帮我装好：

- 先看 README.md 第 3 节，按顺序跑通 Step 0~6，每步做完报告结果
- Python 必须是 3.10~3.12，如果是 3.13 就先告诉我
- 依赖走清华源：pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
- ffmpeg 没装就 winget install Gyan.FFmpeg
- API Key 我从环境变量 LLM_API_KEY 给，不要回显完整内容
- 失败就把原始报错贴给我，不要自己猜
```

### 6.2 日常使用的提示词

| 你想干嘛 | 这么说 |
|---|---|
| 出完整教程 | `分析这个视频：<链接>` |
| 只想知道讲啥 | `这个视频讲了什么？给我速览：<链接>` |
| 想照着做 | `把它展开成可直接执行的清单：<链接>` |
| 指定输出类型 | `按项目复刻教程的方式展开这个视频：<链接>` |
| 处理本地文件 | `分析这个本地视频：D:\videos\demo.mp4` |
| 批量处理 | `依次分析这几个链接，每个出完速览再给下一个：<链接1> <链接2>` |
| 问中途状态 | `现在跑到哪一步了？` |
| 失败重跑 | `上次失败了，视频还在 downloads 里，重试一次` |

### 6.3 修改 Skill 时的约定

`skill/video-analyzer/SKILL.md` 头部的 `description` 字段决定触发时机。
遇到新的说法（比如同事说"扒一下这个视频"），把它加进 `description`，Agent 才会在该出现时出现。

---

## 7. 输出结构说明

```
tools\weixin-favor-kb\
├── downloads\                    # 下载的视频（成功后自动删除）
└── output\<时间戳_标题>\
    ├── progress.json             # 进度：stage / percent / done / error / notes
    ├── transcripts\              # 逐字稿 + 分段时间戳
    ├── frames\                   # 抽出的关键帧（成功后自动删除）
    └── notes\
        ├── tutorial.md           # 详细教程
        ├── brief.md              # 速览卡片
        ├── checklist.md          # 可执行清单
        └── raw.json              # 结构化原始数据 + 成本明细
```

同时会同步一份到仓库根的 `outputs\公众号视频项目\` 下，按类型分到 `项目复刻教学/` 或 `功能提升教学/`。

清理策略：

- **成功**：自动删除本次下载的 mp4、中间音频、关键帧，只留笔记
- **失败**：保留 mp4 方便直接重跑
- **想留着中间产物**：加 `--keep` 参数

常用参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--keep` | 保留视频与中间文件 | 关 |
| `--no-publish` | 不复制到 `outputs/` 目录 | 关 |
| `--wait-manual <分钟>` | 等待手动下载的最长时间 | 30 |

---

## 8. 关键点与常见问题 FAQ

**Q：为什么一定要开着微信？**
视频号视频是加密的，解密必须在微信进程内完成。B站、抖音等平台没有这个限制，下载器自己就能搞定。

**Q：能用 GPU 吗？**
可以。改 `config.yaml`：`whisper.device: cuda`、`compute_type: int8_float16`。CPU 也能跑，只是慢。

**Q：没有 API Key 能跑吗？**
不能 —— 语义理解是核心环节。但 Whisper 转录完全本地免费。

**Q：多久出一篇？**
2 分钟视频约 4-6 分钟。10 分钟以上的视频转录占大头，脚本会自动切 small 模型保速度。

**Q：支持 macOS / Linux 吗？**
Python 部分跨平台，但视频号下载器只有 Windows 发行包。macOS/Linux 用户可以直接给本地视频文件路径，跳过下载环节，其余流程完全可用。

**Q：笔记质量不高怎么办？**
大概率是转录为空（纯幻灯片无解说）或帧太少。可以换 `Qwen3-VL-32B-Instruct`（更慢但更细），或调大 `config.yaml` 里的 `frames.max_frames`。

**Q：下载器起不来 / 端口连不上？**
十有八九是被沙箱或安全软件拦了 —— 它启动时要写系统代理。手动双击运行一次并允许修改，之后就正常了。

**Q：刚才还跑得好好的，突然报 wxchannels 不可用？**
微信退了或视频号页面关掉了。重新打开微信进入视频号页面即可。

---

## 9. 我实际踩过的坑与解决方案

这一节是整个项目最值钱的部分，全部是跑挂之后填回来的。

| # | 坑 | 现象 | 解决 |
|---|---|---|---|
| 1 | **SDK 默认重试放大超时** | LLM 偶发变慢，OpenAI SDK 默认 `max_retries=2`，实际等待变 3 倍后整批超时 | client 初始化时显式 `max_retries=0`，重试逻辑自己控 |
| 2 | **图片太大** | 1080×1920 原图 8 张 base64 后约 3MB，模型直接超时 | 送检前压到长边 768~1024、质量 85，体积降 70%+ |
| 3 | **视觉 prompt 塞长文本** | prompt 里放 6000 字逐字稿，注意力被稀释又巨慢 | 视觉批次只给标题和时间窗，正文交给独立的事实抽取阶段 |
| 4 | **模型选错** | `Qwen3-VL-32B-Instruct` 四图批次 73-120 秒且频繁超时 | 换成 MoE 版 `Qwen3-VL-30B-A3B`（激活 3B），快 5-8 倍，质量接近 |
| 5 | **后台任务被清理** | 平台后台任务约 10 分钟上限，转录跑到一半进程没了 | `DETACHED_PROCESS` 脱离进程启动，Agent 启动后立刻返回，轮询 `progress.json` |
| 6 | **沙箱杀掉下载器** | 下载器秒退、端口连不上。根因：它启动时要调 `reg.exe` 写系统代理 | Agent 侧请求沙箱外执行；或配置开机自启 VBS |
| 7 | **候选帧引发删除保护** | 抽帧候选写了 119 个文件，清理时被批量删除保护拦下 | 候选帧改 ffmpeg `image2pipe` 直读内存，零临时文件 |
| 8 | **cv2.imwrite 不支持中文路径** | 抽帧静默失败：不报错，就是没文件 | 改用 `np.fromfile` + `imencode` 绕过 |
| 9 | **Windows 编码崩溃** | GBK 解码中文名炸掉（`UnicodeEncodeError`） | 必设 `PYTHONIOENCODING=utf-8` 和 `PYTHONLEGACYWINDOWSSTDIO=utf-8` |
| 10 | **文件名非法字符** | `# ! ? * < > : " / \` 竖线 导致建目录失败 | 建目录前先 `sanitize()` 清洗 |
| 11 | **YAML 被 cookie 里的双引号打断** | CF cookie 常含 `curr-account={"xxx"}`，双引号包裹直接解析失败，程序还静默走默认值 | 用块标量 `>-` 或单引号包住 |
| 12 | **本地服务被代理拦截** | 系统开着代理时访问 127.0.0.1 返回 502 | `urllib.request.build_opener(ProxyHandler({}))` 强制直连 |
| 13 | **workers.dev 域名被污染** | 解析服务连不上 | 挂自定义域名绕开 |
| 14 | **MCP 参数传错** | 给 `download_wxchannels_video` 传 `wait_for_completion` 报 unknown field，任务根本没创建 | 该工具只收 `url` / `download_dir`；等待要轮询 `get_download_tasks`（那两个参数是 `download_content` 的） |
| 15 | **系统 python 静默崩溃** | 直接 `python auto_run.py` 看起来"秒退无报错"，其实 stderr 进了 DEVNULL | 一律走 `run_auto.cmd`，它固定用 venv python |
| 16 | **Python 3.13 装不上依赖** | `tokenizers` 等 Rust 扩展没轮子 | 换 Python 3.12 |
| 17 | **抽帧后半段全是盲区** | 抽满上限后视频后半段一帧没有 | 按帧数分桶，每桶保底出 1 帧，再桶内打分择优 |
| 18 | **服务端时段限流导致降级** | 偶发某几章内容偏薄 | 每阶段独立 try/except 降级；排查先看 `raw.json` 的成本与失败记录 |

---

## 10. 隐私与安全

**本仓库提交前已做完整脱敏**，以下内容**不会**出现在 Git 里：

| 敏感项 | 处理方式 |
|---|---|
| LLM API Key | 只存在于本地 `config.yaml`，已被 `.gitignore` 排除，仓库只有 `.example` 模板 |
| 下载器凭据 / Cloudflare Token / 各类 cookie | 同上，一律模板化 |
| 设备指纹 `hardware_fingerprint.json` | 忽略 |
| 下载历史 `data.db` / `cache/` / 日志 | 忽略 |
| 本机绝对路径 | 代码改为相对解析（`modules/paths.py`），文档统一用 `<项目根>` 占位 |
| 内网 IP / 个人域名 | 移除或占位符化 |
| 个人笔记产物 `outputs/` | 忽略 |

给使用者的三条提醒：

1. **永远别把真实 `config.yaml` 提交到仓库** —— 只提交 `.example`。
2. **API Key 优先走环境变量**（安装脚本读 `LLM_API_KEY`），能不落盘就不落盘。
3. **若曾误提交过 Key，第一时间去平台吊销重签** —— Git 历史里的东西删不干净。

---

## 11. 目录结构

```
video-tutorial-generator/
├── README.md                    # 你正在看的文件
├── CHANGELOG.md                 # 版本记录
├── installer/                   # 第三方下载器安装包（Windows）
├── scripts/
│   ├── setup.py / setup.cmd     # 一键安装配置
│   └── start_downloader.vbs     # 开机自启 / 静默启动
├── docs/
│   └── prompts.md               # 提示词合集
├── skill/video-analyzer/        # 可直接安装的 Agent Skill
│   ├── SKILL.md
│   └── references/
├── tools/
│   ├── weixin-favor-kb/         # 流水线主体（Python）
│   │   ├── auto_run.py          # 一键入口
│   │   ├── run_auto.cmd         # Windows 启动器
│   │   ├── modules/             # 下载 / 转录 / 抽帧 / OCR / 分析
│   │   └── config.example.yaml  # 配置模板
│   ├── wx_channels_download/    # 下载器（setup 解压生成）
│   └── ffmpeg/                  # ffmpeg（可选，或装到 PATH）
└── outputs/                     # 笔记发布目录（本地生成，不入库）
```

---

## 12. 许可证与致谢

- 视频下载能力来自开源项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download)，遵循其原始许可证。
- 转录与分析核心思路参考 [weixin-favor-kb](https://github.com/dlv2008/weixin-favor-kb) 项目，在此基础上新增：视觉理解、教程生成、Skill 封装、MCP 自动下载、数据隔离、长视频自动降级。
- **请仅对自己拥有权利或已获授权的内容使用本工具**，尊重原创作者著作权，不要用于搬运、盗用或商业再分发他人视频内容。
- 本项目仅供学习研究与交流使用，合规性责任由使用者自行承担。

---

<div align="center">

**一条链接，一份教程。** 🎬 → 📝

如果帮到了你，点个 Star 就是最大的鼓励。有问题欢迎提 Issue，带着完整报错来，好定位。

</div>
