# 更新日志

## v0.6.0 —— 2026-10-15

**新增「飞书文档 / 知识库」通道：能把飞书文档复刻进自己的飞书知识库（含内嵌视频）**

起因：飞书知识库里经常有内嵌视频，而 ima 不支持视频附件。既然目标本身就是飞书知识库，
就不必绕「下载 → 本地归档 → 再上传」，直接文档级复刻。

### 新增

- **`modules/lark_doc.py`**（新模块，对标 `modules/wx_article.py`）
  - `is_lark_url()` / `host_of()`：识别 `feishu.cn` / `feishu.net` / `larksuite.com` /
    `larkoffice.com` 的 `/wiki/`、`/docx/`、`/docs/` 链接
  - `_cli_argv()`：定位 `lark-cli`。Windows 上 npm 风格的 `lark-cli` 是无扩展名 sh 脚本，
    CreateProcess 跑不了 → 优先改走 `node node_modules/@larksuite/cli/scripts/run.js`
  - `replicate()`：抓文档 XML → `docs +create --doc-format xml` 到自己的知识库
    → **回读校验**（比对 img/source 数量与每个 source 的字节数）
  - `archive_markdown()` / `xml_to_markdown()`：本地归档（素材落 `assets/`）
  - `clone_record_md()`：复刻留痕 md（来源 / 复刻后链接 / 媒体统计 / 校验结果）
- **`auto_run.py`**
  - `run_one()` 新增 `is_lark` 分流 → `run_lark()`；`source_type` 增加 `"lark_doc"`
  - `publish()` 增加飞书分支 → `outputs/飞书文档/`（`dest=md` 时连 `assets/` 一起）
  - `write_lark_pending()`：仅 `dest=md` 才产出 ima 待上传清单
  - 新参数 `--lark-dest {wiki,md}`、`--lark-target <位置|节点 token>`
  - 配置 `/ 环境变量：`lark.dest` / `lark.target` / `lark.parent_node_token`
    （`LARK_DEST` / `LARK_TARGET` / `LARK_PARENT_NODE`）
- **`modules/platforms.py`**：`PLATFORM_HOSTS` 增加 `lark`，标签「飞书」
- 配置模板与 `config.yaml` 增加 `lark` 段；README 增加「附录 A.4 飞书通道」

### 关键实现结论（实测，勿走回头路）

- 飞书 docx 的 `<img src="token"/>` 与 `<source token="..."/>`
  （含 `<figure view-type="Preview">`）**支持跨文档复用** → CLI 会自动把源媒体
  **重新上传**到新文档。实测 15 个媒体全换新 token；6 段 mp4 字节数逐一一致
  （`10350779 / 13797442 / 24945556 / 6496692 / 8895781 / 9035072`）；
  9 img / 3 table / 40 td / 4 cite / 4 callout / 2 bookmark / 1 pre 结构与源完全一致。
- ⛔ **`wiki +node-copy` 不能用来复制别人分享的文档**：报 `131006`。
  根因不是「允许复制」开关，而是**用户不是源知识库成员**
  （`wiki +node-list --space-id <源>` 同样 `131006`）。对照测试：从有成员权限的空间复制 → 成功。
- `docs +media-download` 对分享文档无导出权限 → 一律用 `docs +media-preview`。
- `wiki +node-delete` 必须带 `--obj-type wiki --yes`；lark-cli 的文件参数只接受 **cwd 相对路径**。
- 本地归档里，**单元格含图/视频的表**不转 Markdown 表格（块级媒体会把表格截断），
  改写为「`### 第 N 组` + 字段」块结构。

### ⚠️ 已知限制

- **不是开箱即用**：需要本机有 `lark-cli`（WorkBuddy 飞书连接器自带）并
  `lark-cli auth login --as user` 扫码授权。
- 只能读写该账号**有权限**的文档；整库搬运需对方把你加为知识库**成员**，且**不递归**。
- 不搬迁评论、历史版本、权限设置、封面。
- 内嵌视频在新文档里是**附件块**（可播可下），非源文档那种原位媒体属性。

## v0.5.2 —— 2026-10-15

**重要更正：小红书不需要登录、不需要 Cookie（零配置）**

用户凭直觉提出质疑后实测推翻了我上一版的结论：

| 请求 | 结果 |
|---|---|
| App 分享链接（带 `xsec_token`）+ **无 Cookie** | ✅ 拿到图文详情 |
| App 分享链接 + 完整 Cookie | ✅ 拿到详情 |
| 裸链接 `/explore/<id>`（不带 token）+ 无 Cookie | ❌ 拿不到 |

**真正的前提是「链接带 `xsec_token`」，与登录无关。**

### 更正

- `modules/xhs_note.py`
  - `fetch()` 默认**匿名请求**；错误提示改为按「是否缺 xsec_token」分流，
    不再误导用户去配 Cookie
  - `XhsNeedCookie` → 更名 **`XhsUnavailable`**（保留旧名别名）；模块 docstring 重写
  - 新增 `has_xsec_token(url)`；无 Cookie 路径改用**进程级访客 jar**，
    自动带上服务端下发的 `abRequestId`/`acw_tc`（更像正常浏览器，减少风控）
- `auto_run.py::xhs_prepare()` 降级链改为 **匿名 → 配置的 Cookie → 下载器 MCP**；
  `XHS_COOKIE_HINT` 重写为「先查 xsec_token」
- `scripts/login_xhs.py` 降级为**可选工具**（docstring 与提示语全部改写）
- 配置模板/`config.yaml`：`xhs.cookie` 默认留空；注释改为「一般不用配」；
  **用户的实值 Cookie 已从 config.yaml 清除**（不再需要，且早前被测扫码脚本误覆盖过）

### 文档

- README：平台表小红书改「✅ 零配置」；§2.3 注意事项与 §3.6 章节重写为
  「零配置 + 三类失败原因排查表 + 什么时候才真的需要登录」
- SKILL §14.1 重写为「前置条件：零配置」，并显式记录**早前错误结论**与真实原因；
  §14.4 表格中「带 Cookie 时唯一可靠」改为「移动端 UA 首选（匿名即可）」；
  §15.4 补认知更正条目
- `config.example.yaml` 小红书段落重写

### 早前错误结论复盘

把「下载器适配器解析失败」+「裸链接失败」两个现象归因成了「笔记详情需登录态」，
据此写成"硬约束：必须配 Cookie"并让用户 F12 复制。真实原因有两个：
1. 当时的解析器**只认桌面形态** `note.noteDetailMap`；补上移动端
   `noteData.data.noteData` 支持后，匿名就通了 —— 补完我没有回归测「无 Cookie」路径；
2. 失败的那两条链接本来就不带 `xsec_token`。

**教训：改动解析逻辑后，必须把「无凭据路径」也回归一遍。**

---

## v0.5.1 —— 2026-10-14

**安装体验：给 Agent 一键配好，Key 可选、Cookie 扫码**

### 新增

- **根证书加固 `scripts/make_ca.py`**：用「本机自生成 CA」替换下载器内置的
  SunnyNet 旧证书（**硬编码私钥**，下载器自己都在警告有安全风险；
  对外分发时等于给别人装了个"公开私钥的根证书"）
  - 生成 RSA2048/SHA-256/10 年的本机 CA → `<下载器目录>/certs/`
  - 导入「当前用户」受信任根（**不需要管理员**），并写入 `cert.file/key/name`
  - 校验：经代理 `127.0.0.1:2023` 访问 HTTPS，**把我们的 CA 当唯一信任根**，
    能通即证明链路已换成我们的证书
  - `--status` / `--rollback` / `--remove-legacy`（清理旧 SunnyNet）
  - ⚠️ 实测：PowerShell `Import-Certificate` 在非交互模式会报「此操作不支持使用 UI」
    而失败 → 已做成「先 PS、失败自动转 `certutil`」双路
  - ⚠️ 机器存储里的 SunnyNet 需要**管理员**删除：
    `certutil -delstore Root SunnyNet`（用户存储部分脚本已自动清掉）
- **小红书扫码登录 `login_xhs.cmd` / `scripts/login_xhs.py`**
  - 用系统自带 Edge（`channel=msedge`，**不额外下载 Chromium**）弹出登录页二维码，
    用户手机扫一次 → 自动检测登录完成 → 写入 `config.yaml` 的 `xhs.cookie`
  - 扫码不可用（无浏览器/无 GUI/超时）时**自动降级为粘贴模式**并给出 F12 指引
  - `--validate-only` 只校验；`--check-url <笔记链接>` 做**功能校验**（真实解析一次）
  - 写配置用「定位 `xhs:` 段只改 cookie 行」的方式，**保留全部注释**（不 load/dump yaml）
- **`scripts/setup.py` 自动注册 MCP**：合并写入 `~/.workbuddy/mcp.json`
  （以及 Claude Desktop 配置），**先备份 `.bak`、保留其它 server 与顶层键**、幂等；
  `--no-mcp` 只打印片段，`--mcp-config <路径>` 指定位置
- **`scripts/setup.py --login-xhs`**：安装完直接串上扫码登录，一步到位

### 改进

- **LLM API Key 不再是必填**：默认「Agent 写笔记」模式纯本地、零 API 调用，
  安装时不再要求 Key（只有 `--with-vision` 传统模式才需要）。README 工具表同步改为「可选」
- **Whisper 模型下载默认走镜像**：`auto_run.py` 启动时 `HF_ENDPOINT=hf-mirror.com`
  （已显式设置则尊重用户设置），解决国内首次下载 medium（1.5GB）过慢
- `setup.py` 收尾提示重写：把视频号两条通道讲清楚
  （① 推荐：CF Worker，需注册；② 零注册：微信兜底），并说明其它平台无需 Cookie
- README §3.6 小红书段落重写：扫码优先、粘贴兜底、如何验证；
  §5.1 说明 MCP 已自动注册 + 必须重启 Agent 并点「信任」

### 实测记录（重要，勿踩回去）

| 发现 | 结论 |
|---|---|
| **未登录也会下发 `web_session`**（38 位、同样 `030037ad` 开头） | ❌ 不能用它判定登录成功 → 扫码脚本改用「登录弹窗已消失」 |
| **headless 浏览器触发风控** | 页面变「安全限制 IP存在风险 300012」→ 扫码必须 `headless=False` |
| 有头模式下 `/login` 正常渲染二维码（2 个 canvas + 「扫码」文案） | ✅ 作为扫码目标页 |
| 注入 Cookie 无法恢复网页登录态（仍显示登录弹窗） | 网页 UI 不能作为功能校验口径 → 用 `--check-url` 真实解析 |

### 证书加固实测结果（2026-10-14）

| 项 | 结果 |
|---|---|
| 生成 CA | ✅ CN=`WX Video Download Local CA`，指纹 `02835fe5…`，2026-10-14 → 2036-10-11 |
| 导入当前用户根存储 | ✅ `certutil -user -store Root` 回读确认 |
| 下载器生效证书 | ✅ `get_certificate_status` → `source=generated`，**风险警告已消失**（原为 `sunny_net` + 硬编码密钥警告） |
| 代理链路校验 | ✅ 经 `127.0.0.1:2023` 访问 baidu/bing 均 200，证书链 = 我们的 CA |
| 删除旧 SunnyNet（用户存储） | ✅ 已删；代理链路复查仍通过（证明不再依赖旧证书） |
| 删除旧 SunnyNet（机器存储） | ⏳ 需管理员：`certutil -delstore Root SunnyNet` |

### 待办（需用户配合，未做）

- 下载器机器存储里的 SunnyNet 需**管理员**删除：`certutil -delstore Root SunnyNet`
- 视频号本地（微信）通道的**完整跑批**未跑（下载与路由已实测，见下）

### 视频号微信兜底通道实测（2026-10-14，**通过**）

用户在微信里打开视频号任意视频页后：

| 项 | 结果 |
|---|---|
| `wxchannels:page` | ✅ `available=True`（原为「等待视频号页面连接」） |
| `wxchannels:sph` | ✅ `available=True`（下载器从微信流量里顺带抓到了 yuanbao Cookie） |
| `fetch_content`（视频号链接） | ✅ platform=wxchannels，拿到标题/类型 |
| 实际下载 18 分钟视频 | ✅ **9 秒**完成（1080p / 155MB） |
| 流水线路由降级 | ✅ 禁用 Worker 后自动落到 MCP 本地通道，**1 秒复用**已下载产物 |

**结论：零注册也能抓视频号** —— 代价是「下载器常驻 + 微信停在视频号页面」，且仅 Windows。
新增 `--no-worker` / `SPH_NO_WORKER=1` 供强制走这条通道。

### 本期修掉的坑（视频号本地通道）

| 现象 | 原因 | 修复 |
|---|---|---|
| 重跑同一条视频号链接**卡满 2 分钟被掐断** | `download_wxchannels_video` 的 `existing_action` 默认 `error`，而我们封装时没传；冲突后客户端一直等 | 封装默认传 `existing_action="overwrite"` |
| 视频号任务**无法复用已有产物** | 这类任务的 `source_url` 常是空串，按 URL 匹配永远不中 | 新增 `content_id_of()` 先解析出 `wxchannels:<id>`，`find_finished_task(content_id=…)` 据此命中（实测 1 秒复用） |
| 客户端超时比服务端短 | `MCPClient` 固定 120s，大文件下载会被自己掐断 | `call()/_rpc()` 支持超时参数，下载类放到 960s |
| 视频号本地通道**没写标题旁路** | 只有 Worker 通道写了 `.title.txt` | 本地通道也用 `fetch_content` 的标题/作者写旁路 |

---

## v0.5.0 —— 2026-10-14

**多平台渠道扩展：新增 B站 / 小红书 / 抖音等平台，支持小红书图文笔记**

### 新增

- **平台识别与路由层 `modules/platforms.py`**
  - 20+ 域名规则 → 平台 key（bilibili / xiaohongshu / douyin / kuaishou /
    weibo / zhihu / youtube / x / instagram / cctv），与下载器
    `get_platform_status` 的 key 对齐
  - 短链展开（xhslink / v.douyin / b23.tv …）；小红书必须用移动端 UA 展开，
    桌面 UA 会被 302 到登录页并丢掉目标路径
  - 标题清洗 + `<媒体文件>.title.txt` 旁路约定（与视频号通道一致），
    解决 B站/抖音产出文件名叫整段描述导致 md 命名不可读的问题
  - 落盘产物检测：从「只认 `*.mp4`」扩展为视频/图片/文本三类多扩展名扫描 +
    图集「写满静止 N 秒」判定 + 复用已下载产物
- **小红书通道 `modules/xhs_note.py`**（与 `wx_article.py` 同级的独立通道）
  - 支持**图文笔记**（默认）与**视频笔记**（转交视频流水线）
  - 解析 `window.__INITIAL_STATE__` → `note.noteDetailMap`，取标题/正文/
    作者/发布时间/话题标签/原图列表/视频直链
  - 图集下载 + **保真归档 md**（原文不改写，配图本地化，离线可读）
  - 无登录态时抛 `XhsNeedCookie`（专门异常，带可操作提示），不静默产出空笔记
- **归档目录**：`outputs/小红书笔记/`（md + `images/` 同目录）
- **`--publish-by-platform`**：发布时在类型目录下再按平台分子目录
  （如 `outputs/公众号视频项目/功能提升教学/B站/`），默认关闭以保持历史路径不变

### 改进

- `resolve_source()` 返回结构化产物字典 `{video, images, title, is_temp}`，
  不再只认单个视频路径；**通用图集兜底**：非小红书平台若下载器只吐图片，
  也走归档而不是报错
- `_mcp_try_download()` 失败快速失败：
  - 解析层失败即短路（下载依赖解析出的直链），小红书不再白等 900s 超时
  - 解析失败时下载超时降到 120s
- `MCPClient.download_content()` 默认 `existing_action="overwrite"`：
  下载器默认遇「已存在该下载内容」返回冲突错误，而 `wait_for_completion`
  会一直等到超时（实测卡满 900s）；重跑同一条链接是常态操作
- `MCPClient.find_finished_task()`：重跑同一条链接时先查已完成任务，
  文件仍在磁盘上就直接复用（实测 2.1s vs 重新下载）
- `agent_input.json` 新增 `platform` / `platform_label`，Agent 可知来源平台
- run 目录命名带平台：`<时间戳>_bilibili_video` / `_douyin_video` / `_xhs_note`

### 已知限制

- **小红书需要一次性配置 Cookie**：网页端对笔记详情做了登录态校验，
  无 Cookie 时页面数据里只有 feed、没有笔记详情，
  因此任何纯匿名解析（含第三方下载器的小红书适配器）都会失败。
  配置方式见 README §3.6（`xhs.cookie` 或环境变量 `XHS_COOKIE`）
- ⚠️ **同一笔记有两种页面形态**：实测带 Cookie 时**只有移动端页面**内联了笔记详情
  （`noteData.data.noteData`），桌面端 `__INITIAL_STATE__` 仍只有 feed。
  解析器已两种都认（移动端优先），UA 顺序**不要**改回桌面优先
- 视频号 `wxchannels:sph` 本地通道仍缺 `yuanbao.tencent.com` Cookie；
  现有云端 Worker 通道不受影响

### 测试

| 链接 | 结果 |
|---|---|
| 抖音 `v.douyin.com/q-37jo2pDZc` | ✅ 解析 + 下载 + 转录 + 抽帧 + OCR + 干净源（30 帧 / 1390 字） |
| B站 `bilibili.com/video/BV1nK4y1M73T` | ✅ 解析 + 下载 + 转录 + 抽帧 + OCR + 干净源（22 帧 / 958 字） |
| 小红书 `xhslink.cn/o/7yskqwB6yDB`（**视频笔记**） | ✅ 解析 + 下载 + 转视频通道（23 帧 / 1196 字） |
| 小红书 **图文笔记**（`xhslink.cn/o/6WVwhOhlzS1`） | ✅ 端到端：解析 + 3 张原图本地化 + 保真 md → `outputs/小红书笔记/` |
| 小红书无 Cookie | ✅ 按预期快速失败并给出配置指引（不再白等超时） |

---

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
