# 视频解析工作流 · video-tutorial-generator

> 丢一个视频链接或公众号文章进去，出来一篇能照着做的中文教学笔记。
> 视频号 / 公众号文章 / B站 / 抖音 / 快手 / YouTube / 本地文件，一条命令全自动。

![流程](https://img.shields.io/badge/流程-下载%20→%20转录%20→%20抽帧%20→%20视觉理解%20→%20教学笔记-blue)
![Python](https://img.shields.io/badge/Python-3.10%20~%203.12-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20推荐-orange)

---

## 目录

- [1. 项目介绍与目标](#1-项目介绍与目标)
- [2. 从零开始：完整搭建步骤](#2-从零开始完整搭建步骤)
- [3. 需要下载 / 安装的工具清单（含版本要求）](#3-需要下载--安装的工具清单含版本要求)
- [4. 如何结合 Agent 安装配置](#4-如何结合-agent-安装配置)
- [5. 提示词（Prompts）与使用方法](#5-提示词prompts与使用方法)
- [6. 安装关键点与常见问题 FAQ](#6-安装关键点与常见问题-faq)
- [7. 我实际踩过的坑与解决方案](#7-我实际踩过的坑与解决方案)
- [附录 A. 效果与产出](#附录-a-效果与产出)
- [附录 B. 输出结构说明](#附录-b-输出结构说明)
- [附录 C. 隐私与安全](#附录-c-隐私与安全)
- [附录 D. 目录结构](#附录-d-目录结构)
- [附录 E. 许可证与致谢](#附录-e-许可证与致谢)

---
## 1. 项目介绍与目标

收藏夹里塞满了"回头再看"的视频，最后一条也没看完。这套工作流就是为了解决这件事。

**目标：从「一个视频链接」到「一篇能照着做的中文教学笔记」，中间不需要人参与。**

### 1.1 它做了什么

**三条通道，按链接类型自动分流：**

```
① 视频链接 / 本地文件  →  视频通道
   │  支持：微信视频号 / B站 / 抖音 / 快手 / 微博 / 知乎 / YouTube / X / Instagram …
   │
   ├─ ① 下载        视频号走云端解析（免登录）或微信内解密；其他平台直接下载
   │
   ├─ ② 转录        Whisper（medium / small 自适应）本地转写，不上云
   │
   ├─ ③ 抽帧 + OCR  分桶择优抽帧 + 本地 OCR 识别画面文字（零成本、带时间戳）
   │
   ├─ ④ 缩略图拼版  全部关键帧拼成一张图，一眼纵览全片画面
   │
   └─ ⑤ 写笔记      ⭐ 默认「Agent 写笔记」：按这条视频的内容定结构
                    （可选传统模式：Qwen3-VL 视觉理解 → 事实抽取 → 生成教程）

② 小红书笔记链接  →  小红书通道（xiaohongshu.com / xhslink.cn …）
   ├─ 图文笔记 → 下载原图 + 保真归档 Markdown（原文不改写）  ← 需一次性配置 Cookie
   └─ 视频笔记 → 转交「视频通道」，走转录 + 抽帧 + OCR + Agent 写笔记

③ 公众号文章链接  →  文章通道（mp.weixin.qq.com/s/...）
   └─ 抓取 + 保真转 Markdown（原文归档，一字不改写）
```

**两种运行模式：**

| 模式 | 命令 | 成本 | 说明 |
|---|---|---|---|
| **Agent 写笔记（默认）** | `run_agent.cmd "<链接>"` | **¥0** | 流水线只产干净素材，理解与写作交给 Agent，结构按内容自适应 |
| 传统 Qwen 模式 | `run_auto.cmd --with-vision "<链接>"` | ¥0.04–0.11/条 | 全自动，适合无人值守的超大批量 |

**支持的平台一览：**

| 平台 | 链接形态 | 通道 | 备注 |
|---|---|---|---|
| 微信视频号 | `weixin.qq.com/sph/...` | 视频 | 云端 Worker 解析，免登录 |
| **B站** | `bilibili.com/video/BV...` / `b23.tv/...` | 视频 | 免登录，可直接下载 |
| **抖音** | `v.douyin.com/...` / `douyin.com/video/...` | 视频 | 免登录，可直接下载 |
| **小红书** | `xiaohongshu.com/...` / `xhslink.cn/...` | 图文 + 视频 | ✅ 零配置（**链接必须带 xsec_token**，即 App 里「分享→复制链接」） |
| 快手 / 微博 / 知乎 / YouTube / X / Instagram / 央视频 | 各自主站链接 | 视频 | 免登录（部分需 Cookie 才能拿高清） |
| 公众号文章 | `mp.weixin.qq.com/s/...` | 文章归档 | 纯 HTTP，秒级 |
| 本地文件 | 任意 mp4/mkv/webm… | 视频 | 直接进流水线 |

> 平台识别与短链展开由 `modules/platforms.py` 负责，**下载能力全部复用
> 已在跑的 wx_video_download 下载器（MCP 端口 2022）**，不引入任何新下载依赖。

### 1.2 和"看视频记笔记"的区别

| 传统方式 | 本项目 |
|---|---|
| 反复听、暂停、手打 | 一键转录 + AI 整理 |
| 只听得到"说了什么" | 既听又看：界面操作、终端输出、PPT 图表都能进笔记 |
| 笔记散乱无结构 | 分章节、带时间戳、含参数表 |
| 看完还是不会动手 | 直接产出可执行清单 |

### 1.3 笔记结构：按内容自适应，不套模板

早期版本用"每个知识点强套五要素（步骤 / 注意 / FAQ / 参数 / 原理）"，
结果在短视频上严重注水重复 —— 实测：**861 字转录 → 17,692 字笔记**，
其中一条安装命令被复制 **14 次**，还编造出视频里根本没提的参数表。

**现在是：一篇笔记只用一个结构，结构随这条视频的内容走。**

| 内容类型 | 骨架 |
|---|---|
| 观点说理 | 核心观点 → 论证 → 论据 → 适用边界（**不列执行清单**） |
| 操作步骤 | 适用场景 → 前置条件 → 步骤 → 执行清单 → 避坑 |
| 概念科普 | 是什么 → 怎么运作 → 例子 → 常见误解 |
| 方法体系 | 方法论主张 → 框架 → 关键动作 → 易错点 |
| 工具项目 | 是什么 → 上手步骤 → 执行清单 → 限制 / 对比 |
| 记录随笔 | 背景 → 内容脉络 → 观点与留白 → 金句 |
| 风格图鉴 | 清单表 → 家族归类 → 用法 |

**硬规则**：作者没讲的不许补｜参数/数值一律以转录 + 画面为准｜执行清单只在操作类出现｜
同一观点全文只出现一次｜同音误听要校正｜`## 音频文案` 原样保留。

### 1.4 设计原则

| 原则 | 做法 |
|---|---|
| 本地优先 | 转录、抽帧、OCR 全在本地跑；**默认模式连一次云端模型都不调** |
| 免登录 | 视频号走云端解析通道，不需打开微信（**需先部署一次 Worker，见 §2.7**；未部署会自动回落旧通道） |
| 便宜 | **默认 ¥0**（Agent 模式零 API 调用）；传统模式约 ¥0.1/条；文章通道 0 成本 |
| 结构自适应 | 按每条视频的内容定结构，不套模板（见 §1.3） |
| 抗超时 | 各阶段独立失败降级，不会一步崩全盘 |
| 免人工 | 视频号可全自动；其他平台偶需打开微信 |
| 可归档 | 产出可上传 ima 知识库，回读校验通过才算完成 |

> ⚠️ **关于「云端解析」的重要前提**：视频号免登录解析依赖一个**自己的 Cloudflare Worker**，
> 它是**一次性部署**的，不属于开箱即用。仓库出于安全考虑**不含**该 Worker 的凭据与配置。
> 不部署也能用，但视频号会退回「下载器 + 微信内解密」通道（需要开着微信、且仅 Windows）。
> 部署步骤见 **§2.7**。

---

## 2. 从零开始：完整搭建步骤

> 全程约 20 分钟，其中大部分在等下载。**按顺序做，每步都有验证方法。**
> 想偷懒可直接跳到第 3.3 节的自动安装脚本（一次搞定依赖、下载器、配置三件事）。

### 2.1 Step 0 —— 准备 API Key（**可选，可以先跳过**）

**默认的「Agent 写笔记」模式完全本地运行、零 API 调用，所以不需要任何 Key。**
语义理解与写作交给你的 Agent 完成，流水线只负责下载、转录、抽帧、OCR。

只有你要用**传统全自动模式**（`--with-vision`，让云端模型自己写笔记）时才需要 Key。
推荐[硅基流动 SiliconFlow](https://cloud.siliconflow.cn/)，新用户有免费额度：

1. 注册 → 控制台 → API 密钥 → 新建密钥
2. 得到形如 `sk-xxxxxxxx` 的 Key

用传统模式时默认模型（可在 `config.yaml` 里换）：

| 用途 | 模型 | 说明 |
|---|---|---|
| 视觉理解 / 教程生成 | `Qwen/Qwen3-VL-30B-A3B-Instruct` | MoE，4 图批次 13-19 秒，稳定 |
| 分类 / 速览 / 清单 / 自检 | `Qwen/Qwen2.5-7B-Instruct` | 纯文本，约 0.5 秒，便宜 |

> 💡 **没有 Key 也能跑完整个流程** —— 后面所有步骤都不依赖它。
> 装完先跑通默认模式，想省事再补 Key。

### 2.2 Step 1 —— 拉取代码

```powershell
git clone https://github.com/boluomi2001/video-tutorial-generator.git
cd video-tutorial-generator
```

不想用 Git 也行：直接下载 ZIP 解压，效果一样。

### 2.3 Step 2 —— 一键安装（依赖 / 下载器 / 配置）

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

### 2.4 Step 3 —— 安装 ffmpeg

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

### 2.5 Step 4 —— 安装视频下载器

**仓库已经内置了 Windows 安装包**，不用去别处找：

```
installer\wx_video_download_v260907_windows_x86_64.zip
```

它由 `scripts/setup.py` 自动解压到 `tools/wx_channels_download/`，手动解压效果等价。

> 这是第三方开源项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download) 的官方发行包，本项目只做整合，功劳归原作者。

### 2.5.1 ⚠️ 根证书：请换成「本机自生成证书」

下载器要抓浏览器的 HTTPS 流量来实现「视频号页面注入下载按钮」，所以它会往系统里装一个**中间人根证书**。它内置的是旧版 SunnyNet 证书，**用的是硬编码私钥**——下载器自己的接口都在警告：

> "该证书为旧版SunnyNet证书，使用硬编码密钥对，存在安全风险，建议替换为本机生成的证书"

自己的机器上问题不大，但**如果你要把这套装给别人，等于给别人装了一个"私钥公开的根证书"**，任何知道你 IP 的人都能中间人解密的 HTTPS 流量。所以本项目提供一条命令换成你自己的：

```powershell
cd tools\weixin-favor-kb
venv\Scripts\python.exe scripts\make_ca.py              # 生成 + 导入 + 写配置 + 校验
venv\Scripts\python.exe scripts\make_ca.py --status     # 只看现状
venv\Scripts\python.exe scripts\make_ca.py --rollback   # 回退到内置证书
```

它做的事：生成 RSA2048/10 年的本机 CA → 导入**当前用户**受信任根（不需要管理员）→
写入下载器 `cert.file/key/name` → 经代理访问 HTTPS **把我们的 CA 当唯一信任根**做校验。
私钥只落在 `tools/wx_channels_download*/certs/`，已在 `.gitignore` 里（绝不上传）。

**还需要你手动做一步**（需要管理员权限，脚本删不掉机器存储）：

```powershell
# 以管理员身份运行
certutil -delstore Root SunnyNet
```

删之前先确认下载器已用上新证书（`make_ca.py --status` 应显示
`来源=generated` 且**没有**风险警告）。

> 换证书后，之前"信任过旧证书"的程序可能重新提示不安全，重启一次浏览器即可。
> 回退：`make_ca.py --rollback`，下载器下次启动会重新安装它自带的证书。

<details>
<summary>Whisper 模型要不要手动下？</summary>

不用。首次转录时会自动下载 medium（约 1.5GB）。

**国内网络已默认走镜像**：流水线启动时会把 `HF_ENDPOINT` 设为
`https://hf-mirror.com`（若你已显式设置则尊重你的设置）。
想改回官方源：`set HF_ENDPOINT=https://huggingface.co`。

如果还是慢，可以从 [ModelScope](https://www.modelscope.cn/) 搜 `faster-whisper-medium` 手动下载 4 个文件（`model.bin` / `config.json` / `tokenizer.json` / `vocabulary.txt`）放进 `tools/weixin-favor-kb/models/whisper-medium/`。实测 ModelScope 比 hf-mirror 快十几倍。

</details>

### 2.6 Step 5 —— 配置文件

安装脚本会帮你生成，手动的情况下照这样复制：

```powershell
copy tools\weixin-favor-kb\config.example.yaml tools\weixin-favor-kb\config.yaml
copy tools\wx_channels_download\config.example.yaml tools\wx_channels_download\config.yaml
```

然后编辑 `tools\weixin-favor-kb\config.yaml` 的这一行：

```yaml
llm:
  # ⚠️ 现在这一步是「可选」的：
  #   默认的「Agent 写笔记」模式完全本地、零 API 调用，因此**不需要任何 Key**。
  #   只有要用传统 Qwen 自动生成模式（--with-vision）时才需要填。
  api_key: "sk-你的硅基流动APIKey"
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

**小红书：零配置，不需要 Cookie 也不需要登录**

只要链接里带 `xsec_token` 就能匿名解析 —— 也就是**在小红书 App 里「分享 → 复制链接」
拿到的链接**（App 分享链接天然带 token）。图文笔记和视频笔记都实测通过。

```powershell
# 直接丢链接就行，不需要任何配置
run_agent.cmd "https://xhslink.cn/o/xxxx"
```

**下面这些情况才会拿不到，按顺序排查：**

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 报「链接里没有 xsec_token」 | 你复制的是浏览器地址栏链接、或手打的裸 `/explore/<id>` | 回 App 里「分享 → 复制链接」重新复制 |
| 拿不到笔记详情 | 笔记已删除 / 被设为私密 / 仅关注可见 / token 已失效 | 先重新分享；仍是私密内容才需要登录态 |

**确实需要登录态时（少见）**，两种方式任选：

```powershell
# 方式一：扫码（会弹出浏览器窗口显示二维码，扫完自动写入配置）
tools\weixin-favor-kb\login_xhs.cmd

# 方式二：手动粘贴
login_xhs.cmd --paste
```

也可以直接把 Cookie 填进 `config.yaml`：

```yaml
xhs:
  cookie: "web_session=xxxxxx; a1=yyyyyy; webId=zzzzzz"   # 一般留空即可
```

> 环境变量 `XHS_COOKIE` 优先级更高。**流水线默认匿名请求，只有匿名失败才用这个 Cookie**，
> 所以留空是推荐状态；不配也不影响其它平台。

> ⚠️ **实测坑（改代码别踩）**：
> 1. **UA 必须移动端** —— 笔记详情内联在移动端页面的 `noteData.data.noteData`；
>    桌面端 `__INITIAL_STATE__` 目前只有 feed（见 SKILL §14.4）。
> 2. **不要用「有 web_session」判断登录** —— 未登录也会下发它。
> 3. **headless 浏览器会被风控**（页面变「安全限制 IP存在风险 300012」）→ 扫码必须用有头窗口。

### 2.7 Step 6 ——（可选，强烈推荐）部署云端解析 Worker

**为什么需要这一步？**

视频号的视频是加密的，只有在微信里才能解密。为了做到「不用打开微信也能下载」，本项目使用一条**云端解析**通道：一个跑在 Cloudflare 上的 Worker 调用微信官方免鉴权接口 `get_feed_info`，直接拿到**未加密直链**，再透传给你。全程不碰登录态。

这个 Worker 需要**你自己的 Cloudflare 账号**，所以**必须自己部署一次** —— 仓库里不含它（也不该含，因为要填 API Token）。

> **不部署可以吗？可以，已实测通过。** 程序会自动回落到「下载器 + 微信页面」通道：
>
> 1. 下载器保持运行（它会设置系统代理）；
> 2. 在**电脑版微信里打开「视频号」并点开任意一个视频**（保持页面开着）；
> 3. 之后直接丢视频号链接即可 —— 下载器会从微信的流量里自动抓到所需凭据，
>    `wxchannels:page` 与 `wxchannels:sph` 两条都会变成可用。
>
> 实测：打开视频号页面后，18 分钟的视频 **9 秒**下载完成（1080p / 155MB），零注册、零 Cookie。
> 重跑同一条链接会**复用已下载产物**（1 秒），不会重复下载。
>
> 也就是说：**要免登录就必须注册 Cloudflare；不想注册，就让微信开着。** 二选一。
> 强制走微信那条（跳过 Worker）用 `--no-worker`，或设环境变量 `SPH_NO_WORKER=1`。
> 注意这条通道**仅 Windows**（下载器只有 Windows 发行包）。

**前置：** 一个免费的 [Cloudflare](https://dash.cloudflare.com) 账号。

**第一步：创建 API Token**

Cloudflare 控制台 → 右上角头像 → **My Profile** → **API Tokens** → **Create Token** → 选 **Edit Cloudflare Workers** 模板 → 创建并复制 Token。

再记下 **Account ID**（控制台右侧栏，或 Workers 页面 URL 里那串十六进制）。

**第二步：填配置**

复制模板并填写：

```powershell
copy tools\wx_channel\config.example.yaml tools\wx_channel\config.yaml
```

```yaml
# tools/wx_channel/config.yaml
cloudflare:
  accountid: "你的 Account ID"
  apitoken: "上一步创建的 API Token"
  sphcookie: "dash.cloudflare.com 的 cookie"   # 见下方说明
  sphhostname: ""    # 留空，部署成功后自动回填
  sphworkername: "sph-parse"
```

> **sphcookie 怎么拿**：登录 dash.cloudflare.com，按 F12 打开开发者工具 →
> Network 标签 → 刷新页面 → 随便点一个发往 `api.cloudflare.com` 的请求 →
> 在 Request Headers 里复制整条 `cookie` 值。
>
> ⚠️ **坑（踩过）**：这条 cookie 里常含英文双引号（如 `curr-account={"xxx"}`），
> 用双引号包裹会让 YAML 解析失败，导致整个 `cloudflare` 段静默失效。
> **务必用单引号**，或用块标量 `>-` 折行写。

**第三步：执行部署**

```powershell
cd tools\wx_channel
.\wx_channel.exe sph_deploy --config config.yaml
```

> ⚠️ **坑（踩过）**：CLI 默认去读 `$HOME/.wx_channel/config.yaml`，
> **必须**加 `--config config.yaml` 指向程序目录里的配置，否则会静默走默认值。

部署成功后 `sphhostname` 会被自动回填（例如 `https://sph-parse.你的子域.workers.dev`），流水线会**自动读取**它，无需再改任何代码。

**验证：**

```powershell
# 把 <你的域名> 换成回填后的 sphhostname
curl -X POST "https://<你的域名>/api/fetch_video_profile" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://weixin.qq.com/sph/XXXXXXXX\"}"
```

返回 JSON 里含 `videoUrl` 即部署成功。

**其他部署方式 / 备注：**

| 项 | 说明 |
|---|---|
| 手动改地址 | 也可用环境变量 `SPH_WORKER_URL` 覆盖，优先级高于配置文件 |
| workers.dev 被污染 | 中国大陆部分网络可能连不上 `*.workers.dev`，可给 Worker 绑一个自己的自定义域名 |
| 重新部署 | 重复执行上面的 `sph_deploy` 命令即可 |
| 可靠性 | Worker 跑在你的账号下，与作者无关，长期可用性由你自己控制 |

### 2.8 Step 7 —— 跑通第一个视频

```powershell
# 方式 A（推荐，免登录）：直接丢视频号链接，云端解析
#   —— 需要先完成 Step 6 的 Worker 部署
tools\weixin-favor-kb\run_auto.cmd "https://weixin.qq.com/sph/XXXXXXXX"

# 方式 B（兜底）：未部署 Worker，或云端不可用时
tools\wx_channels_download\wx_video_download.exe
# 打开电脑版微信，随便进到一个视频号页面，让微信保持运行
tools\weixin-favor-kb\run_auto.cmd "https://channels.weixin.qq.com/..."
```

处理**公众号文章**（原文归档，不改写）：

```powershell
tools\weixin-favor-kb\run_auto.cmd "https://mp.weixin.qq.com/s/XXXXXXXX"
```

想跳过下载先验证其余流程，直接喂本地文件：

```powershell
tools\weixin-favor-kb\run_auto.cmd "D:\我的视频\demo.mp4"
```

链接类型自动分流，无需额外参数。第一次会下载 Whisper 模型，耐心等。
跑完到 `tools\weixin-favor-kb\output\<run_id>\notes\` 收成果。

### 2.9 Step 8 —— 批量处理（一次给很多链接）

一条条喂太慢？可以直接批量。**串行执行**，逐条独立，**单条失败不影响后面的**。

**方式一：直接传多条链接**

```powershell
tools\weixin-favor-kb\run_auto.cmd "链接1" "链接2" "链接3" ...
```

**方式二：从文件读（推荐，30 条就写 30 行）**

```powershell
# links.txt 一行一条；空行与 # 开头的注释行会被忽略
tools\weixin-favor-kb\run_auto.cmd --from-file links.txt
```

`links.txt` 示例：

```
# 视频号
https://weixin.qq.com/sph/AAAAAAAA
https://weixin.qq.com/sph/BBBBBBBB
# 公众号文章
https://mp.weixin.qq.com/s/CCCCCCCC
# 本地文件也可以
D:\我的视频\demo.mp4
```

自动去重（重复链接只跑一次）。

**跑完看哪？** 除了每条自己的 `output/<run_id>/`，批次目录里会多出三份：

```
tools\weixin-favor-kb\output\batch_<时间戳>\
├── batch_state.json          # 执行状态（供断点续跑）
├── _batch_<时间戳>.md        # 📊 汇总表：成功/失败/类型/耗时/成本/产物
└── .ima_pending_batch.json   # ima 待上传清单（汇总全部成功项）
```

汇总表长这样，一眼看全：

| # | 状态 | 类型 | 标题 | 耗时 | 成本 | 产物 / 失败原因 |
|---|---|---|---|---|---|---|
| 1 | ✅ | 效率提升教学 | 作者A：某技巧 | 214.0s | ¥0.0490 | `作者A：某技巧.md` |
| 2 | ❌ | — | — | 1.2s | ¥0.0000 | `DownloadError: Worker HTTP 500` |

**断了怎么办？—— 断点续跑**

跑到第 20 条崩了、或者你手动 Ctrl+C 了，**不用从头再来**：

```powershell
tools\weixin-favor-kb\run_auto.cmd --from-file links.txt --resume output\batch_20261004_210000
```

它会读取 `batch_state.json`，**跳过已成功的**，只重试失败项和没轮到的。
状态是**每跑完一条就落盘**的，所以即使在最坏时机被打断也能接上。

汇总表的失败明细区还给出了「可直接复制重跑」的链接段，粘进一个 txt 用 `--from-file` 就能单独补跑。

> **性能预期**：串行模式下，2 分钟视频约 4-6 分钟/条，30 条大致 2-3 小时。
> 公众号文章是秒级（2-5 秒），混在批量里几乎不占时间。
> 脚本跑的时候可以关掉终端窗口吗？**不行**，进程结束任务就停了；但被打断可以续跑。

---

## 3. 需要下载 / 安装的工具清单（含版本要求）

| 工具 | 版本要求 | 必须？ | 用途 | 获取 |
|---|---|---|---|---|
| Python | **3.10 ~ 3.12**（勿用 3.13） | ✅ | 运行流水线 | python.org |
| Git | 任意新版 | ✅ | 拉代码 | git-scm.com |
| ffmpeg | 6.x / 8.x essentials | ✅ | 抽音频、抽帧 | `winget install Gyan.FFmpeg` |
| 硅基流动 API Key | — | ⬜ **可选** | 仅传统 Qwen 模式需要；默认 Agent 模式零 Key | cloud.siliconflow.cn |
| wx_video_download | v260907+ | 其他平台/兜底 | MCP 下载服务（端口 2022） | 仓库 `installer/` 内置 |
| wx_channel | v5.6.9+ | 云端解析需要 | 部署 Cloudflare Worker（见 §2.7） | 自行获取（第三方工具） |
| Cloudflare 账号 | 免费版即可 | 云端解析需要 | 托管解析 Worker | dash.cloudflare.com |
| 微信 PC 客户端 | 新版 | 仅兜底 | 提供解密环境 | pc.weixin.qq.com |
| Windows | 10 / 11 | 推荐 | 下载器只有 Windows 发行包 | — |
| NVIDIA GPU + CUDA | — | 可选 | 转录提速 5-10 倍 | 配 `device: cuda` |

> **视频号已支持云端解析**：不需要打开微信、不需要本地下载器进程，免登录直接拿直链。
> **但这条通道需要你自己部署一次 Worker**（见 **§2.7**），仓库不含其凭据与配置。
> 未部署时，微信 PC 客户端与下载器即为兜底方案。

Python 依赖（`requirements.txt`）：

```
faster-whisper>=1.1.0         # Whisper 转写
opencv-python-headless>=4.9.0 # 抽帧与画面打分
rapidocr-onnxruntime>=1.3.0   # 画面文字识别（本地、零成本、带时间戳）
openai>=1.30.0                # 调模型（仅传统模式用；OpenAI 兼容接口都能用）
cos-python-sdk-v5>=1.9.0      # ima 知识库上传（COS）
playwright>=1.40              # 小红书扫码登录用（可选；复用系统 Edge，不下载浏览器）
cryptography                  # 生成本机 CA，替换下载器自带的旧根证书
pyyaml / jinja2 / numpy / loguru / rich
```

磁盘预算：依赖约 2GB + Whisper medium 约 1.5GB + 视频缓存。**建议留 15GB 以上。**

---

## 4. 如何结合 Agent 安装配置

装完之后最好用的方式，是把它接进你的 Agent（WorkBuddy / Claude Desktop / 任何支持 Skill 与 MCP 的客户端）。接好之后，你只需要把链接丢过去说"分析这个视频"。

### 4.1 三步接线

**① 安装 Skill**（让 Agent 知道怎么用这套流水线）

把 `skill/video-analyzer/` 整个目录复制到你的 Agent 技能目录：

| 客户端 | 目标路径 |
|---|---|
| WorkBuddy | `C:\Users\<你>\.workbuddy\skills\video-analyzer\` |
| Claude Desktop | 放进 Project Knowledge，或在对话里直接引用 `SKILL.md` |

**② 注册 MCP**（让 Agent 能直接调用下载能力）

**`scripts/setup.py` 已经帮你写好了**（合并不覆盖，会先备份 `.bak`）：
它会自动探测并更新 WorkBuddy 的 `~/.workbuddy/mcp.json`（以及 Claude Desktop 的配置）。

你也可以手动加，内容就是这一段：

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
> **加完后必须重启 Agent，并去自定义连接器入口对新服务器点一次「信任」才会生效。**
> 不想让它自动写：`setup.py --no-mcp`（只打印片段）
> 想写到别的位置：`setup.py --mcp-config <路径>`

**③ 设置开机自启**（可选，但强烈建议）

把 `scripts/start_downloader.vbs` 的快捷方式放进 `shell:startup`（Win+R 输入该命令打开目录）。
之后开机下载器就在后台跑着，连手动启动这一步都省了。

### 4.2 直接让 Agent 帮你装（推荐）

把**整个仓库文件夹**（或解压后的目录）丢给你的 Agent，附上这一句：

```text
这个仓库是视频解析工作流。请帮我装好并配置到能用的状态：
1. 先读 README.md 的第 2 章，按顺序执行安装；
2. 用 python scripts/setup.py --login-xhs 一键装依赖、解压下载器、写配置、注册 MCP；
3. 需要我配合的操作（比如启动下载器时弹出的系统代理授权）请明确告诉我；
4. 装完跑一条测试链接给我看结果。

注意：默认的「Agent 写笔记」模式是纯本地的，不需要任何 API Key。
遇到要填 API Key 的地方先跳过，告诉我就行。
```

Agent 会自己读文档、跑脚本、处理报错。**你只需要做两件它做不了的事**：

1. **首次启动下载器**时，双击 `tools/wx_channels_download/` 里的 exe（或 `scripts/start_downloader.vbs`）
   —— 它要改系统代理，需要你本机授权；之后配好开机自启就不用管了。
2. **唯一需要注册的东西是视频号通道**（二选一，见 §2.7）：
   - 想免登录：注册一个免费 Cloudflare 账号，部署 Worker（约 5 分钟）
   - 不想注册：什么都不用做，保持微信在跑即可（见 §2.7 的兜底说明）

> 其它平台（B站 / 抖音 / 快手 / 微博 / YouTube / 小红书 / 公众号文章）**零配置**，
> 装完直接丢链接就能用。

### 4.3 Agent 侧的硬性约束

这几条是实战教训，违反就会**静默失败**（没报错，但也没结果）：

1. **必须脱离进程启动**，启动后立刻返回。转录可能跑 20-30 分钟，前台或平台后台任务会被超时清理。
2. **轮询 `progress.json` 汇报进度**，不要阻塞等待。
3. **不要用系统 python 跑 `auto_run.py`**，缺依赖会 import 即崩，表现为"秒退无报错"。一律走 `run_auto.cmd`。
4. Agent 拉起下载器时要走**沙箱外**通道 —— 它启动时要调 `reg.exe` 写系统代理，沙箱内会被安全策略杀掉。

---

## 5. 提示词（Prompts）与使用方法

完整版见 [`docs/prompts.md`](docs/prompts.md)，下面是高频几条。

### 5.1 让 Agent 安装的提示词

```
这是个把我收藏的视频自动转成教学笔记的工作流，请帮我装到能用的状态：

- 先读 README.md 的「第 2 章 从零开始：完整搭建步骤」，按顺序做，每步做完向我报告结果
- 尽量用一键脚本：python scripts/setup.py --login-xhs
  （它会装依赖、解压下载器、写配置、注册 MCP，并把小红书登录串起来）
- Python 必须是 3.10~3.12；如果本机是 3.13，先告诉我别硬装
- 依赖走清华源，ffmpeg 没装就 winget install Gyan.FFmpeg
- **API Key 先不用管**：默认模式纯本地、零 API 调用。真要填时我会用环境变量 LLM_API_KEY 给，别回显完整内容
- 需要我手动做的操作（比如第一次启动下载器时的系统代理授权）请明确列出来给我
- 失败就把原始报错贴给我，不要自己猜也不要跳过
```

### 5.2 日常使用的提示词

| 你想干嘛 | 这么说 |
|---|---|
| 出完整教程 | `分析这个视频：<链接>` |
| 出公众号文章归档 | `归档这篇公众号文章：<mp.weixin.qq.com 链接>` |
| 归档小红书笔记 | `扒一下这篇小红书笔记：<xhslink.cn 分享链接>` |
| 只想知道讲啥 | `这个视频讲了什么？给我速览：<链接>` |
| 想照着做 | `把它展开成可直接执行的清单：<链接>` |
| 指定输出类型 | `按项目复刻教程的方式展开这个视频：<链接>` |
| 处理本地文件 | `分析这个本地视频：D:\videos\demo.mp4` |
| 批量处理 | `依次分析这几个链接，每个出完速览再给下一个：<链接1> <链接2>` |
| 问中途状态 | `现在跑到哪一步了？` |
| 失败重跑 | `上次失败了，视频还在 downloads 里，重试一次` |

### 5.3 修改 Skill 时的约定

`skill/video-analyzer/SKILL.md` 头部的 `description` 字段决定触发时机。
遇到新的说法（比如同事说"扒一下这个视频"），把它加进 `description`，Agent 才会在该出现时出现。

---

## 6. 安装关键点与常见问题 FAQ

**Q：处理视频号还需要开着微信吗？**
通常不需要。视频号已支持**云端解析**通道 —— 免登录、免本地下载器进程，几秒拿直链。

**但这个通道不是开箱即用的**：它依赖一个部署在你**自己的 Cloudflare 账号**下的 Worker，
需要按 **§2.7** 一次性配置（约 5 分钟）。仓库出于安全考虑不含该 Worker 的配置与凭据。

没有部署的话，程序会自动回落到「下载器 + 微信内解密」这条老路，
需要**开着电脑版微信并停在视频号页面**，且**仅 Windows 可用**。

**Q：为什么仓库里没有 `tools/wx_channel/` 这个目录？**
因为它里面装着**你的** Cloudflare Account ID、API Token 和 cookie，
属于敏感凭据，已被 `.gitignore` 排除，**绝不入库**。
使用者需要自己放一份 `wx_channel.exe` 并按 §2.7 生成 `config.yaml`。

**Q：公众号文章会被 AI 改写吗？**
不会。文章走独立通道，**只做原文归档**，仅还原标题/加粗/列表/配图等格式，一个字都不改。

**Q：产出会自动存到哪里？**
本地 md 会上传到 ima 知识库；**回读校验命中（标题或 media_id 一致）后才删本地**，避免上传失败丢内容。

**Q：能用 GPU 吗？**
可以。改 `config.yaml`：`whisper.device: cuda`、`compute_type: int8_float16`。CPU 也能跑，只是慢。

**Q：没有 API Key 能跑吗？**
视频通道不能 —— 语义理解是核心环节（文章通道不需要 Key，纯抓取转换）。
Whisper 转录完全本地免费。

**Q：多久出一篇？**
2 分钟视频约 4-6 分钟；公众号文章秒级（2-5 秒）。
10 分钟以上的视频转录占大头，脚本会自动切 small 模型保速度。

**Q：支持 macOS / Linux 吗？**
Python 部分跨平台，但视频号下载器只有 Windows 发行包。macOS/Linux 用户可以直接给本地视频文件路径，跳过下载环节，其余流程完全可用。

**Q：笔记质量不高怎么办？**
大概率是转录为空（纯幻灯片无解说）或帧太少。可以换 `Qwen3-VL-32B-Instruct`（更慢但更细），或调大 `config.yaml` 里的 `frames.max_frames`。

**Q：下载器起不来 / 端口连不上？**
十有八九是被沙箱或安全软件拦了 —— 它启动时要写系统代理。手动双击运行一次并允许修改，之后就正常了。

**Q：刚才还跑得好好的，突然报 wxchannels 不可用？**
微信退了或视频号页面关掉了。重新打开微信进入视频号页面即可。

---

## 7. 我实际踩过的坑与解决方案

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

### 多平台 / 登录态 相关的坑（v0.5.x 新增）

| # | 坑 | 现象 | 解决 |
|---|---|---|---|
| 19 | **「未登录也会下发 `web_session`」被当成登录信号** | 扫码后明明没扫，脚本却报「检测到登录态」，还把访客 Cookie 写进了配置 | 别拿单个 cookie 判断登录；扫码脚本改用「登录弹窗已消失」判定，功能校验交给真实解析一次 |
| 20 | **headless 浏览器被风控** | 无头模式打开小红书，页面直接变「安全限制 IP存在风险 300012」，连二维码都没有 | 扫码必须 `headless=False`（有头窗口） |
| 21 | **短链展开被 302 到登录页** | 桌面 UA 请求 `xhslink.cn/...` 跳到 `/login?redirectPath=...`，目标路径被吞，拿不到笔记 | 短链展开用**移动端 UA**；结果若仍是登录页，从 `redirectPath` 参数把真实地址捞回来 |
| 22 | **⚠️ 把「解析失败」误判成「需要登录」**（本项目最大的一次判断失误） | 据此写成"硬约束：必须配 Cookie"，还让用户去 F12 复制、写扫码脚本 —— 全都白做 | 真实原因有两个：① 解析器当时**只认桌面形态** `note.noteDetailMap`；② 失败的那两条链接**本来就不带 `xsec_token`**。补上移动端 `noteData.data.noteData` 后匿名就通了。**与登录无关** |
| 23 | **配图下成了缩略图** | 图文笔记归档的图只有 5KB | `imageList[].infoList` 里 **H5_DTL 在前、H5_PRV 在后**，取「末位」拿到的是 `!style_xxx` 缩略图（同一张 61,959B → 5,495B，**差 11 倍**）。改成优先 `imageScene=H5_DTL`；URL 的 `!xxx` 后缀**不能删**（删了 403） |
| 24 | **重跑视频号链接卡满 2 分钟被掐断** | 任务明明已完成，再跑一次就长时间无响应 | `download_wxchannels_video` 的 `existing_action` 默认是 `error`，重跑直接判冲突，而调用方还在等文件落盘。封装里默认传 `existing_action="overwrite"` |
| 25 | **视频号任务无法复用已有产物** | 按 URL 匹配"最近一条已完成任务"永远命不中 | 这类任务的 `source_url` 常是**空字符串**。改用 `content_id`（先用 `fetch_content` 拿到 `wxchannels:<id>`）匹配，实测复用只要 1 秒 |
| 26 | **大文件下载被自己的超时掐断** | 小文件正常，稍大就报超时 | MCP 客户端超时固定 120s，比服务端还短。加超时参数，下载类调用放到 960s |
| 27 | **下载器内置根证书用的是硬编码私钥** | 下载器自己的接口都在警告「旧版 SunnyNet 证书，硬编码密钥对，存在安全风险」 | 自己生成一份本机 CA 换上（`scripts/make_ca.py`）；私钥只留本机并加进 `.gitignore`。对分发出去的项目尤其重要 |
| 28 | **PowerShell 非交互模式下导证书失败** | `Import-Certificate` 报「此操作不支持使用 UI」 | 做成「先 PowerShell、失败自动转 `certutil -user -addstore Root`」双路 |
| 29 | **改了解析逻辑却没回归「无凭据路径」** | 上面第 22 条能错那么久，就是因为只在"带 Cookie"的路径上验证 | **教训**：改动凭据 / 解析逻辑后，必须把「不带任何凭据」的路径也跑一遍 |
| 30 | **写文档时用朴素规则切块，代码块里的 `## ` 被当成标题** | 重排 README 时内容被静默吞掉一段 | 切 Markdown 结构必须**围栏感知**（跟踪 ``` 状态）；改完用行数/片段数做一致性断言 |

---

## 附录 A. 效果与产出

### A.1 视频通道

**默认（Agent 写笔记模式）**产出「干净素材 + Agent 写的笔记」：

| 文件 | 内容 |
|---|---|
| `notes/agent_input.json` | **Agent 的唯一输入**：转录 + 画面 OCR + 关键帧路径 + 拼版 + 自适应信号 |
| `frames/contact_sheet.jpg` | **缩略图拼版**：一张图纵览全片画面（每格标时间戳） |
| `frames/keyframe_*.jpg` | 关键帧原图（Agent 模式保留，可按需细看） |
| `transcripts/*.txt` `*_segments.json` | 转录（带时间戳，写「音频文案」用） |
| `outputs/_新版笔记/<标题>.md` | **最终交付物**：按内容定结构的笔记 + `## 音频文案` |

**传统模式**（`--with-vision`）额外产出**一份单文件整合稿**（外加中间产物）：

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| `<标题>.md` | **最终交付物**：速览 + 执行清单 + 详细教程 + 音频文案 | 直接看这份 |
| `tutorial.md` | 中间产物：完整长文（分章节） | 排查 |
| `brief.md` | 中间产物：一屏速览 | 排查 |
| `checklist.md` | 中间产物：勾选清单 | 排查 |

整合稿结构（仅传统模式）：

```
# <标题>
> 类型｜时长｜章节｜帧数｜成本
## 速览          （一句话 + 要点 + 适合谁）
## 执行清单      （- [ ] 步骤 + 可选命令）
## 详细教程      （分章节正文）
## 音频文案      （视频原声逐句转录，带 mm:ss 时间戳）
```

文件名 = `{作者}：{描述首行}`（如 `王假乐：Minimax官方感谢H3开发者.md`），不再用无意义的分享 ID。

### A.2 文章通道

产出一份**原文归档 md**（元信息头 + 正文），不改写任何文字：

```
# <文章标题>
> **来源**：微信公众号「XXX」｜ **作者**：XXX
> **发布时间**：YYYY-MM-DD HH:MM
> **原文链接**：https://mp.weixin.qq.com/s/...
> **说明**：以下为原文归档，内容未作任何改写，仅还原标题、加粗、列表与配图等格式。
---
（正文）
```

### A.3 小红书通道

| 笔记类型 | 产物 |
|---|---|
| **图文笔记** | `outputs/小红书笔记/<标题>.md` + `images/note_01.jpg…`（原图本地化，离线可读） |
| **视频笔记** | 走「视频通道」，产出 `notes/agent_input.json`（转录 + 抽帧 + OCR + 拼版） |

归档 md 结构（图文笔记，原文不改写）：

```
# <笔记标题>
> **作者**：小红书 @XXX ｜ **发布时间**：YYYY-MM-DD HH:MM
> **原文链接**：https://www.xiaohongshu.com/explore/...
> **类型**：图文笔记
> **说明**：以下为原文归档，文字未作任何改写；配图已本地化保存。
---
（正文原文）
## 配图
![note_01](images/note_01.jpg)
…
## 标签
#话题1　#话题2
```

> ✅ **小红书零配置**：只要用 App「分享 → 复制链接」拿到的链接（带 `xsec_token`），
> 匿名就能解析，**不需要登录、不需要 Cookie**。
> 裸链接 `/explore/<id>`、地址栏直接复制的链接拿不到详情 —— 这类回 App 重新分享即可。
> 仅私密笔记 / 仅关注可见才需要配 Cookie 或跑 `login_xhs.cmd` 扫码（见 §2.6）。

实测参考（普通 CPU 机器，无 GPU）：

| 项目 | 数值 |
|---|---|
| 2 分钟视频全流程 | 约 4-6 分钟 |
| 公众号文章 | 秒级（约 2-5 秒） |
| Whisper medium 转录 | 约 113 秒 / 2 分钟视频 |
| 视觉理解（MoE 30B-A3B） | 4 图一批 13-19 秒 |
| 大模型花费 | 视频约 ¥0.07 ~ ¥0.11；文章 ¥0 |

---

## 附录 B. 输出结构说明

```
tools\weixin-favor-kb\
├── downloads\                    # 下载的视频（成功后自动删除）
└── output\<时间戳_标题>\
    ├── progress.json             # 进度：stage / percent / done / error / notes
    ├── transcripts\              # 逐字稿 + 分段时间戳
    ├── frames\                   # 抽出的关键帧（成功后自动删除）
    └── notes\
        ├── <标题>.md              # ★ 最终交付物（单文件整合稿）
        ├── tutorial.md           # 中间产物：详细教程
        ├── brief.md              # 中间产物：速览卡片
        ├── checklist.md          # 中间产物：可执行清单
        ├── .ima_pending.json     # 待上传 ima 清单
        └── raw.json              # 结构化原始数据 + 成本明细
```

公众号文章通道（`output\<时间戳>_wechat_article\`）：

```
    ├── article_raw.html          # 原始 HTML（便于复核/重转）
    └── notes\
        ├── <文章标题>.md          # ★ 最终交付物（原文归档）
        └── .ima_pending.json     # 待上传 ima 清单
```

同步与归档：

- 视频 → `outputs\公众号视频项目\`（按类型分 `项目复刻教学/` 或 `功能提升教学/`）
- 文章 → `outputs\公众号文章\`
- **每次产出自动上传 ima 知识库**：Agent 上传 → 回读校验 → **校验命中才删本地 md**

清理策略：

- **成功**：自动删除本次下载的 mp4、中间音频、关键帧，只留笔记
- **失败**：保留 mp4 方便直接重跑
- **想留着中间产物**：加 `--keep` 参数

常用参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--from-file <文件>` | 从文件批量读取链接（一行一条，`#` 注释） | — |
| `--resume <批次目录>` | 断点续跑：跳过已成功项，只补失败项 | — |
| `--keep` | 保留视频与中间文件 | 关 |
| `--no-publish` | 不复制到 `outputs/` 目录 | 关 |
| `--wait-manual <分钟>` | 等待手动下载的最长时间 | 30 |

> 传**多条**位置参数（`auto_run.cmd "链接1" "链接2" ...`）或用 `--from-file` 即进入批量模式；
> 只传一条时行为与以前完全一致。

---

## 附录 C. 隐私与安全

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

## 附录 D. 目录结构

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
│   │   ├── auto_run.py          # 一键入口（视频 / 小红书 / 文章自动分流）
│   │   ├── run_auto.cmd         # Windows 启动器
│   │   ├── modules/             # 下载 / 转录 / 抽帧 / OCR / 分析 / 文章 / 小红书 / ima
│   │   │   ├── wx_download.py   #   视频号下载（云端解析 + MCP + 兜底）
│   │   │   ├── platforms.py     #   多平台识别 / 短链展开 / 标题旁路 / 产物检测
│   │   │   ├── wx_article.py    #   公众号文章抓取 + 保真转 Markdown
│   │   │   ├── xhs_note.py      #   小红书笔记解析 + 图集下载 + 归档 md
│   │   │   └── ima_upload.py    #   ima 知识库上传 / 校验
│   │   └── config.example.yaml  # 配置模板
│   ├── wx_channel/              # 云端解析部署工具（需自行获取 wx_channel.exe）
│   │   └── config.example.yaml  #    Worker 凭据模板（真实 config.yaml 不入库）
│   ├── wx_channels_download/    # 下载器（setup 解压生成）
│   └── ffmpeg/                  # ffmpeg（可选，或装到 PATH）
└── outputs/                     # 笔记发布目录（本地生成，不入库）
    ├── 公众号视频项目/           #   视频通道（按类型分类，可再按平台分）
    ├── 公众号文章/               #   文章通道
    └── 小红书笔记/               #   小红书图文笔记（md + images/）
```

---

## 附录 E. 许可证与致谢

- 视频下载能力来自开源项目 [ltaoo/wx_channels_download](https://github.com/ltaoo/wx_channels_download)，遵循其原始许可证。
- 转录与分析核心思路参考 [weixin-favor-kb](https://github.com/dlv2008/weixin-favor-kb) 项目，在此基础上新增：视觉理解、教程生成、Skill 封装、MCP 自动下载、数据隔离、长视频自动降级。
- **请仅对自己拥有权利或已获授权的内容使用本工具**，尊重原创作者著作权，不要用于搬运、盗用或商业再分发他人视频内容。
- 本项目仅供学习研究与交流使用，合规性责任由使用者自行承担。

---

<div align="center">

**一条链接，一份教程。** 🎬 → 📝

如果帮到了你，点个 Star 就是最大的鼓励。有问题欢迎提 Issue，带着完整报错来，好定位。

</div>
