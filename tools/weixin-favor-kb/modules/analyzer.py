"""内容分析器：章节切分 → 分批视觉理解 → 事实抽取 → 教程生成 → 质量自检。

相比旧版的关键改进：
1. 不再把转录硬截断到 4000 字，改为按上下文预算动态切片。
2. 关键帧分批送检（每批 <= 15 张），不再只发 5 张。
3. 章节切分 + 时间轴对齐：帧 / OCR / 转录按章节绑定，笔记可回溯时间点。
4. 两阶段生成：先抽事实清单（不压缩），再基于清单写教程，避免一次成型的压缩损失。
5. 硬信息抽取：命令 / 包名 / URL / 版本号用正则 + LLM 双路提取。
6. 分类体系对齐交付物：项目复刻 / 效率提升。
7. 成本与耗时记录到 cost.json。
8. 质量自检：覆盖率不足时自动重跑一次。
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from openai import OpenAI

INITIAL_DELAY = 5
MAX_DELAY = 60
BACKOFF_FACTOR = 2
MAX_ATTEMPTS = 3

# 实测：Qwen3-VL-32B 单请求 1-2 图约 10-25s，4 图可达 60s+，
# 并发过高会触发服务端 500。因此限制每批 6 张、并发 2。
# 实测 Qwen3-VL-32B 服务端稳定性随图片数下降：1-2 图最稳（约 10-25s），
# 4 图以上常超过 120s 或返回 500。因此固定每批 2 张、并发 2。
# 请求最小间隔（秒）。实测连续密集调用会触发服务端限流，
# 表现为纯文本请求也超时。节流后稳定性明显提升。
MIN_CALL_INTERVAL = 1.5

MAX_FRAMES_PER_BATCH = 4
MAX_IMAGE_SIDE = 768
VISUAL_WORKERS = 2
TUTORIAL_WORKERS = 2
REQUEST_TIMEOUT = 150.0
CONTEXT_CHAR_BUDGET = 60000  # 转录送入上限（约 3 万汉字）

PRICE_INPUT_PER_M = 1.4    # 硅基流动 Qwen3-VL-32B 输入 元/百万 token
PRICE_OUTPUT_PER_M = 4.2   # 输出 元/百万 token

VIDEO_TYPE_PROJECT = "项目复刻"
VIDEO_TYPE_SKILL = "效率提升"


# ---------------------------------------------------------------- prompts

_CLASSIFY_PROMPT = """判断这个视频属于哪一类，只输出 JSON。

视频标题: {title}
转录开头:
{transcript}

规则:
- 「{t_project}」: 视频讲的是完整搭建/复刻某个东西（项目、网站、小程序、工具、工作流），观众要动手做出自己的版本。
- 「{t_skill}」: 视频讲的是某个工具/软件/技巧的用法、提效方法、经验分享，观众学方法即可，不需要做出一个成品。

输出: {{"type": "{t_project}" 或 "{t_skill}", "domain": "领域标签，如 小程序/AI编程/自动化/设计/办公", "reason": "一句话理由"}}"""


# 关键：视觉请求的 prompt 必须短。实测 Qwen3-VL-32B 处理「长文本 + 图片」时
# 推理极慢（数千字转录会让单次请求超过 120s 并超时），而纯画面描述请求只需 10-25s。
# 转录与 OCR 的整合放在后续的事实抽取阶段（纯文本，不受此限制）。
_VISUAL_BATCH_PROMPT = """你在分析一个视频的第 {ci}/{total} 个片段（{start} ~ {end}）。
视频类型: {vtype}

下面是该片段按时间顺序排列的关键帧截图。只描述画面本身，不要推测语音内容：
- 界面/软件/代码编辑器的具体状态与变化
- 命令行、代码、配置内容（尽量原文转录）
- 图表、架构图、PPT 标题
- 操作步骤的先后顺序

输出 JSON:
{{
  "observations": "这一段画面上发生了什么，按时间顺序描述",
  "screen_text": ["画面中出现的关键文字/代码/命令，原文"],
  "key_actions": ["具体操作动作"],
  "timestamps": ["值得注意的时间点，如 03:12"]
}}"""


_FACT_EXTRACT_PROMPT = """从以下视频材料中提取完整事实清单。不要概括、不要省略，尽量保留细节。

视频标题: {title}
视频类型: {vtype}
总时长: {duration} 秒

【完整转录】
{transcript}

【各片段画面观察】
{observations}

【OCR 文字（带秒数）】
{ocr}

请输出 JSON:
{{
  "chapters": [
    {{"start": 秒数, "end": 秒数, "title": "章节标题", "summary": "这段讲了什么", "details": "具体步骤/细节"}}
  ],
  "commands": ["出现过的命令，原文"],
  "packages": ["提到的工具/库/框架/软件名"],
  "urls": ["提到的网址"],
  "versions": ["提到的版本号"],
  "configs": ["关键参数/配置项及其取值"],
  "pitfalls": ["作者提到的坑、注意事项"],
  "prerequisites": ["开始前需要准备的环境/账号/前置条件"]
}}"""


_TUTORIAL_PROMPT = """基于事实清单，为第 {ci}/{total} 章写详细教程内容。

视频类型: {vtype}
章节: {title}（{start} ~ {end}）

本章事实:
{facts}

要求:
1. 详细操作步骤（逐步，可执行）
2. 关键注意事项
3. 常见问题及解决方案
4. 参数配置说明（含默认值和调整建议）
5. 原理解释（为什么这样做）

直接输出 Markdown 正文，不要包裹在代码块里，不要重复章节标题。"""


_SELFCHECK_PROMPT = """检查这份教程的质量。

事实清单中的章节:
{chapters}

教程正文:
{tutorial}

输出 JSON:
{{
  "covered": ["已覆盖的章节标题"],
  "missing": ["遗漏的章节标题"],
  "unexplained_terms": ["出现但未解释的术语"],
  "score": 0到100的整数,
  "issues": ["具体问题描述"]
}}"""


_BRIEF_PROMPT = """把这份教程压缩成速览卡片，300 字以内。

教程:
{tutorial}

输出 JSON: {{"one_line": "一句话核心", "bullets": ["要点1", "要点2", "要点3"], "who": "适合谁看"}}"""


_CHECKLIST_PROMPT = """把这份教程转成可执行清单，供读者照着做。

视频类型: {vtype}
教程:
{tutorial}

输出 JSON: {{"items": [{{"step": "步骤描述", "command": "涉及的命令，没有则留空", "done": false}}]}}"""


# ---------------------------------------------------------------- helpers


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


class _Usage:
    """累计 token 消耗与费用。"""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, usage) -> None:
        if not usage:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        self.calls += 1

    @property
    def cost_cny(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * PRICE_INPUT_PER_M
            + self.completion_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_cny": round(self.cost_cny, 4),
        }


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def regex_hard_facts(text: str) -> dict:
    """正则预抽取硬信息：命令、URL、版本号、包名。"""
    urls = sorted(set(re.findall(r"https?://[\w\-./?=&%#]+", text)))
    versions = sorted(set(re.findall(r"\b[vV]?\d+\.\d+(?:\.\d+)?\b", text)))[:30]
    commands: list[str] = []
    for m in re.finditer(
        r"(?:^|\n)\s*(?:\$|>|#)\s*([^\n]{3,200})", text
    ):
        commands.append(m.group(1).strip())
    for kw in ("npm install", "pip install", "pnpm add", "yarn add", "git clone",
               "docker run", "curl ", "apt-get install", "brew install", "uv add"):
        for m in re.finditer(re.escape(kw) + r"[^\n]{0,120}", text):
            commands.append(m.group(0).strip())
    seen, cmds = set(), []
    for c in commands:
        if c not in seen:
            seen.add(c)
            cmds.append(c)
    packages: list[str] = []
    for m in re.finditer(r"(?:npm install|pip install|pnpm add|yarn add|uv add)\s+([^\s&|;]{1,60})", text):
        packages.append(m.group(1).strip())
    return {
        "urls": urls[:40],
        "versions": versions,
        "commands": cmds[:60],
        "packages": sorted(set(packages))[:40],
    }


def split_chapters(
    segments: list[dict],
    duration: float,
    target_count: int = 6,
) -> list[dict]:
    """按转录分段切分章节，保证每章时长均衡且不超过上限。"""
    if not segments:
        span = max(duration, 1) / max(target_count, 1)
        return [
            {"start": i * span, "end": min((i + 1) * span, duration), "title": f"第 {i+1} 段"}
            for i in range(target_count)
        ]

    total = segments[-1].get("end", duration) or duration
    span = max(total / max(target_count, 1), 30.0)
    chapters: list[dict] = []
    cur: dict | None = None
    for seg in segments:
        s = float(seg.get("start", 0) or 0)
        e = float(seg.get("end", s) or s)
        if cur is None or s - cur["start"] >= span:
            if cur:
                cur["end"] = s
                chapters.append(cur)
            cur = {"start": s, "end": e, "texts": []}
        cur["end"] = e
        cur["texts"].append(str(seg.get("text", "")).strip())
    if cur:
        cur["end"] = total
        chapters.append(cur)

    for i, c in enumerate(chapters, 1):
        c["transcript"] = " ".join(t for t in c.pop("texts", []) if t)
        c.setdefault("title", f"第 {i} 章")
    return chapters


def _slice_ocr(ocr_items: list[tuple[float, str]], start: float, end: float) -> str:
    parts = [f"[{_mmss(t)}] {txt}" for t, txt in ocr_items if start <= t < end]
    return "\n".join(parts) if parts else "（该片段无画面文字）"


# ---------------------------------------------------------------- analyzer


class ContentAnalyzer:
    def __init__(self, api_key: str, base_url: str, model: str,
                 fast_model: str = "") -> None:
        # max_retries=0：SDK 内部默认会重试 2 次，会把单次超时放大成 3 倍，
        # 由本模块自己的重试逻辑统一控制。
        self.client = OpenAI(api_key=api_key, base_url=base_url,
                             timeout=REQUEST_TIMEOUT, max_retries=0)
        self.model = model
        self.fast_model = fast_model or ""
        self.usage = _Usage()
        self._lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._last_call = 0.0
        self._fast_broken = False  # 快模型不可用时自动回退主模型

    def _throttle(self) -> None:
        """限制请求频率，避免触发服务端限流。"""
        with self._call_lock:
            now = time.time()
            wait = self._last_call + MIN_CALL_INTERVAL - now
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    # ---------- 底层调用 ----------

    def _resolve_model(self, heavy: bool) -> str:
        """heavy=True 走主模型（视觉/教程），否则优先走快模型。"""
        if heavy or not self.fast_model or self._fast_broken:
            return self.model
        return self.fast_model

    def _chat(self, messages: list[dict], max_tokens: int = 8192,
              temperature: float = 0.3, tag: str = "", heavy: bool = True) -> str:
        model = self._resolve_model(heavy)
        delay = INITIAL_DELAY
        last_err = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=REQUEST_TIMEOUT,
                )
                with self._lock:
                    self.usage.add(getattr(resp, "usage", None))
                msg = resp.choices[0].message
                content = msg.content or getattr(msg, "reasoning", "") or ""
                if content.strip():
                    return content
                logger.warning("{} 返回空内容（第{}次），{}s 后重试", tag, attempt, delay)
            except Exception as e:
                last_err = str(e)
                if "400" in last_err or "deserialize" in last_err:
                    # 快模型不支持时回退主模型重试
                    if model != self.model and not heavy:
                        logger.warning("快模型不可用，回退主模型: {}", last_err[:100])
                        self._fast_broken = True
                        return self._chat(messages, max_tokens, temperature, tag, heavy=True)
                    raise RuntimeError(f"LLM 格式错误(不重试): {last_err[:200]}")
                logger.warning("{} 调用失败（第{}次）{}s 后重试: {}", tag, attempt, delay, last_err[:120])
            time.sleep(delay)
            delay = min(delay * BACKOFF_FACTOR, MAX_DELAY)
        raise RuntimeError(f"{tag} 连续失败 {MAX_ATTEMPTS} 次: {last_err[:150]}")

    def _chat_json(self, messages: list[dict], max_tokens: int = 8192,
                   tag: str = "", heavy: bool = True) -> dict | None:
        text = self._chat(messages, max_tokens=max_tokens, tag=tag, heavy=heavy)
        data = _extract_json(text)
        if data is None:
            logger.warning("{} JSON 解析失败: {}", tag, text[:200])
        return data

    @staticmethod
    def _image_block(path: str) -> dict | None:
        """读取关键帧并压缩后转 base64。

        原图常为 1080x1920，单张 200KB+，多张一起发送会让请求体达到数 MB，
        视觉模型处理极慢甚至超时。这里把长边压到 1024 并用 85 质量重编码，
        体积可降到约 1/4，肉眼信息几乎无损。
        """
        try:
            raw = Path(path).read_bytes()
            img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                scale = min(1.0, MAX_IMAGE_SIDE / max(h, w))
                if scale < 1.0:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                     interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    raw = buf.tobytes()
            b64 = base64.b64encode(raw).decode("utf-8")
            return {"type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        except Exception as e:
            logger.warning("读取关键帧失败 {} - {}", path, e)
            return None

    # ---------- 主流程 ----------

    def run_full(
        self,
        transcript: str,
        segments: list[dict],
        frames: list[str],
        frames_meta: dict,
        ocr_items: list[tuple[float, str]],
        title: str = "",
        duration: float = 0.0,
        output_dir: str = "",
    ) -> dict:
        """完整分析流程，返回教程 / 速览 / 清单 / 事实 / 成本。"""
        t0 = time.time()
        transcript = (transcript or "").strip()
        if len(transcript) > CONTEXT_CHAR_BUDGET:
            logger.warning("转录 {} 字超出预算，按章节切片处理", len(transcript))
        duration = duration or float(frames_meta.get("duration", 0) or 0)

        # 1. 分类
        vtype, domain = self._classify(title, transcript)
        logger.info("分类结果: {} / {}", vtype, domain)

        # 2. 章节切分
        chapters = split_chapters(segments, duration)
        logger.info("切分 {} 个章节", len(chapters))

        # 3. 逐章视觉理解（帧分批）
        observations = self._visual_pass(chapters, frames, frames_meta, ocr_items, vtype)

        # 4. 事实抽取
        facts = self._extract_facts(title, vtype, duration, transcript, observations, ocr_items)
        if not facts.get("chapters"):
            facts["chapters"] = [
                {"start": c["start"], "end": c["end"],
                 "title": c.get("title", f"第 {i} 章"),
                 "summary": "", "details": c.get("transcript", "")[:500]}
                for i, c in enumerate(chapters, 1)
            ]

        # 5. 逐章写教程
        tutorial = self._write_tutorial(vtype, title, facts, duration)

        # 6. 质量自检，不达标重跑一次
        check = self._self_check(facts, tutorial)
        if check and check.get("score", 100) < 70:
            logger.warning("自检 {} 分不达标，重跑教程生成", check.get("score"))
            tutorial = self._write_tutorial(vtype, title, facts, duration)
            check = self._self_check(facts, tutorial)

        # 7. 三形态输出
        brief = self._brief(tutorial)
        checklist = self._checklist(vtype, tutorial)

        elapsed = time.time() - t0
        result = {
            "title": title,
            "video_type": vtype,
            "domain": domain,
            "duration": round(duration, 1),
            "chapters": facts.get("chapters", []),
            "facts": facts,
            "tutorial": tutorial,
            "brief": brief,
            "checklist": checklist,
            "quality": check or {},
            "frames_used": len(frames),
            "cost": self.usage.to_dict(),
            "elapsed_sec": round(elapsed, 1),
        }

        if output_dir:
            self._save_cost(output_dir, result)
        return result

    # ---------- 各阶段 ----------

    def _classify(self, title: str, transcript: str) -> tuple[str, str]:
        prompt = _CLASSIFY_PROMPT.format(
            title=title or "（无标题）",
            transcript=(transcript[:2500] or "（无转录）"),
            t_project=VIDEO_TYPE_PROJECT,
            t_skill=VIDEO_TYPE_SKILL,
        )
        data = self._chat_json(
            [{"role": "user", "content": prompt}], max_tokens=512, tag="classify", heavy=False
        )
        if not data:
            return VIDEO_TYPE_SKILL, "未分类"
        vtype = data.get("type", "")
        if vtype not in (VIDEO_TYPE_PROJECT, VIDEO_TYPE_SKILL):
            vtype = VIDEO_TYPE_PROJECT if "复刻" in vtype or "项目" in vtype else VIDEO_TYPE_SKILL
        return vtype, str(data.get("domain", "未分类"))

    def _visual_pass(
        self,
        chapters: list[dict],
        frames: list[str],
        frames_meta: dict,
        ocr_items: list[tuple[float, str]],
        vtype: str,
    ) -> list[dict]:
        """按章节分批送帧做视觉理解。"""
        frame_ts = {f["file"]: f["timestamp"] for f in frames_meta.get("frames", [])}
        items: list[tuple[float, str]] = []
        for p in frames:
            name = Path(p).name
            if name in frame_ts:
                items.append((frame_ts[name], p))
            else:
                m = re.search(r"_t([\d.]+)s\.jpg$", name)
                items.append((float(m.group(1)) if m else 0.0, p))
        items.sort()

        total = len(chapters)
        tasks: list[tuple[int, int, dict, list[str]]] = []
        empty: list[dict] = []
        for ci, ch in enumerate(chapters, 1):
            picked = [p for ts, p in items if ch["start"] - 1 <= ts < ch["end"] + 1]
            if not picked:
                empty.append({
                    "start": ch["start"], "end": ch["end"],
                    "observations": "", "screen_text": [], "key_actions": [],
                    "timestamps": [], "_order": (ci, 0),
                })
                continue
            for bi in range(0, len(picked), MAX_FRAMES_PER_BATCH):
                tasks.append((ci, bi, ch, picked[bi:bi + MAX_FRAMES_PER_BATCH]))

        def run(task):
            ci, bi, ch, batch = task
            blocks = [{
                "type": "text",
                "text": _VISUAL_BATCH_PROMPT.format(
                    ci=ci, total=total,
                    start=_mmss(ch["start"]), end=_mmss(ch["end"]),
                    vtype=vtype,
                ),
            }]
            for p in batch:
                blk = self._image_block(p)
                if blk:
                    blocks.append(blk)
            # 单批失败不能拖垮整个流程：降级为空观察，后续靠转录与 OCR 兜底
            try:
                data = self._chat_json(
                    [{"role": "user", "content": blocks}],
                    max_tokens=2048, tag=f"visual-{ci}-{bi // MAX_FRAMES_PER_BATCH + 1}",
                ) or {}
            except Exception as e:
                logger.warning("视觉批次 {}-{} 失败，降级为空观察: {}", ci, bi, str(e)[:120])
                data = {}
            return {
                "start": ch["start"], "end": ch["end"],
                "observations": data.get("observations", ""),
                "screen_text": data.get("screen_text", []),
                "key_actions": data.get("key_actions", []),
                "timestamps": data.get("timestamps", []),
                "frames": len(batch),
                "_order": (ci, bi),
            }

        observations: list[dict] = []
        if tasks:
            with ThreadPoolExecutor(max_workers=VISUAL_WORKERS) as ex:
                for res in ex.map(run, tasks):
                    observations.append(res)

        observations.extend(empty)
        observations.sort(key=lambda x: x["_order"])
        for o in observations:
            o.pop("_order", None)
        return observations

    def _extract_facts(
        self,
        title: str,
        vtype: str,
        duration: float,
        transcript: str,
        observations: list[dict],
        ocr_items: list[tuple[float, str]],
    ) -> dict:
        obs_text = "\n".join(
            f"[{_mmss(o['start'])}-{_mmss(o['end'])}] {o.get('observations','')}\n"
            f"  画面文字: {' | '.join(o.get('screen_text', [])[:20])}\n"
            f"  操作: {' | '.join(o.get('key_actions', [])[:15])}"
            for o in observations
        ) or "（无画面观察）"

        ocr_text = "\n".join(f"[{_mmss(t)}] {txt}" for t, txt in ocr_items) or "（无画面文字）"
        # 事实抽取是全流程输入最大的一步，必须压缩：实测输入过长会让 MoE 模型
        # 生成阶段超过 120s 并超时。
        ocr_text = ocr_text[:5000]
        full_transcript = transcript[:20000]

        prompt = _FACT_EXTRACT_PROMPT.format(
            title=title or "（无标题）", vtype=vtype,
            duration=int(duration), transcript=full_transcript,
            observations=obs_text[:6000], ocr=ocr_text,
        )
        try:
            data = self._chat_json(
                [{"role": "user", "content": prompt}], max_tokens=2048, tag="facts"
            )
        except Exception as e:
            logger.warning("事实抽取失败，仅用正则结果: {}", str(e)[:100])
            data = None
        facts: dict = data or {}

        # 正则补抽硬信息，与 LLM 结果合并去重
        regexed = regex_hard_facts(full_transcript + "\n" + ocr_text)
        for key in ("urls", "versions", "commands", "packages"):
            merged = list(facts.get(key) or []) + list(regexed.get(key) or [])
            seen, out = set(), []
            for v in merged:
                v = str(v).strip()
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
            facts[key] = out[:60]
        return facts

    def _write_tutorial(self, vtype: str, title: str, facts: dict, duration: float) -> str:
        chapters = facts.get("chapters") or []
        if not chapters:
            chapters = [{"start": 0, "end": duration, "title": "全部内容",
                         "summary": "", "details": ""}]

        parts: list[str] = []
        header = [
            f"# {title or '视频教程'}",
            "",
            f"> 类型：{vtype} ｜ 时长：{_mmss(duration)} ｜ 章节：{len(chapters)}",
            "",
        ]
        if vtype == VIDEO_TYPE_PROJECT:
            header += ["## 前置条件", ""]
            pre = facts.get("prerequisites") or []
            header += [f"- {p}" for p in pre] if pre else ["- （视频未明确说明）"]
            header.append("")
        parts.extend(header)

        total = len(chapters)

        def write_chapter(arg):
            ci, ch = arg
            facts_blob = json.dumps(
                {
                    "summary": ch.get("summary", ""),
                    "details": ch.get("details", ""),
                    "commands": facts.get("commands", [])[:20],
                    "configs": facts.get("configs", [])[:20],
                    "pitfalls": facts.get("pitfalls", [])[:15],
                },
                ensure_ascii=False,
            )
            try:
                body = self._chat(
                    [{"role": "user", "content": _TUTORIAL_PROMPT.format(
                        ci=ci, total=total, vtype=vtype,
                        title=ch.get("title", f"第 {ci} 章"),
                        start=_mmss(float(ch.get("start", 0) or 0)),
                        end=_mmss(float(ch.get("end", 0) or 0)),
                        facts=facts_blob,
                    )}],
                    max_tokens=2560, tag=f"tutorial-{ci}",
                ) or ""
            except Exception as e:
                logger.warning("章节 {} 生成失败，用事实摘要兜底: {}", ci, str(e)[:100])
                body = "\n".join(filter(None, [
                    ch.get("summary", ""), ch.get("details", "")
                ])) or "（该章节生成失败，请参考原始转录）"
            return ci, ch, body

        bodies: dict[int, tuple[dict, str]] = {}
        with ThreadPoolExecutor(max_workers=TUTORIAL_WORKERS) as ex:
            for ci, ch, body in ex.map(write_chapter, list(enumerate(chapters, 1))):
                bodies[ci] = (ch, body)

        for ci in sorted(bodies):
            ch, body = bodies[ci]
            parts.append(f"## {ci}. {ch.get('title', f'第 {ci} 章')} "
                         f"（{_mmss(float(ch.get('start', 0) or 0))} - "
                         f"{_mmss(float(ch.get('end', 0) or 0))}）")
            parts.append("")
            parts.append(body.strip())
            parts.append("")

        tail = ["## 速查", ""]
        if facts.get("commands"):
            tail.append("**命令**")
            tail.append("")
            tail.append("```bash")
            tail.extend(f"{c}" for c in facts["commands"][:30])
            tail.append("```")
            tail.append("")
        if facts.get("packages"):
            tail.append("**工具 / 依赖**：" + "、".join(str(p) for p in facts["packages"][:30]))
            tail.append("")
        if facts.get("urls"):
            tail.append("**链接**")
            tail.append("")
            tail.extend(f"- {u}" for u in facts["urls"][:20])
            tail.append("")
        if facts.get("pitfalls"):
            tail.append("**避坑**")
            tail.append("")
            tail.extend(f"- {p}" for p in facts["pitfalls"][:20])
            tail.append("")
        parts.extend(tail)
        return "\n".join(parts)

    def _self_check(self, facts: dict, tutorial: str) -> dict | None:
        chapters = [c.get("title", "") for c in (facts.get("chapters") or [])]
        try:
            data = self._chat_json(
                [{"role": "user", "content": _SELFCHECK_PROMPT.format(
                    chapters="、".join(chapters)[:3000],
                    tutorial=tutorial[:14000],
                )}],
                max_tokens=1024, tag="selfcheck", heavy=False,
            )
        except Exception as e:
            logger.warning("质量自检失败: {}", str(e)[:100])
            return None
        return data

    def _brief(self, tutorial: str) -> dict:
        try:
            data = self._chat_json(
                [{"role": "user", "content": _BRIEF_PROMPT.format(tutorial=tutorial[:12000])}],
                max_tokens=512, tag="brief", heavy=False,
            )
        except Exception as e:
            logger.warning("速览生成失败: {}", str(e)[:100])
            data = None
        return data or {}

    def _checklist(self, vtype: str, tutorial: str) -> dict:
        try:
            data = self._chat_json(
                [{"role": "user", "content": _CHECKLIST_PROMPT.format(
                    vtype=vtype, tutorial=tutorial[:12000])}],
                max_tokens=2048, tag="checklist", heavy=False,
            )
        except Exception as e:
            logger.warning("清单生成失败: {}", str(e)[:100])
            data = None
        return data or {}

    def _save_cost(self, output_dir: str, result: dict) -> None:
        try:
            d = Path(output_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / "cost.json").write_text(
                json.dumps({
                    "model": self.model,
                    "usage": result["cost"],
                    "elapsed_sec": result["elapsed_sec"],
                    "frames_used": result["frames_used"],
                    "quality": result["quality"],
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("写入 cost.json 失败: {}", e)

    # ---------- 兼容旧接口 ----------

    def analyze(self, transcript: str, ocr_text: str, category: str,
                tags: list[str], keyframe_paths: list[str] | None = None) -> dict:
        """旧版接口：一次性摘要，保留兼容。"""
        prompt = (
            f"分析以下视频内容，生成知识笔记。\n\n类别: {category}\n标签: {', '.join(tags)}\n\n"
            f"转录文字:\n{transcript[:20000]}\n\n画面文字:\n{ocr_text[:4000]}\n\n"
            '输出 JSON: {"summary":"3-5句摘要","key_points":["要点"],'
            '"resources":["工具/网站"],"action_items":{"dev":[],"life":[],"tech_summary":[]}}'
        )
        blocks: list[dict] = [{"type": "text", "text": prompt}]
        if keyframe_paths:
            for p in keyframe_paths[:MAX_FRAMES_PER_BATCH]:
                blk = self._image_block(p)
                if blk:
                    blocks.append(blk)
        data = self._chat_json([{"role": "user", "content": blocks}], tag="legacy")
        data = data or {}
        return {
            "summary": data.get("summary", ""),
            "key_points": data.get("key_points", []),
            "resources": data.get("resources", []),
            "action_items": data.get("action_items", {}),
            "category": category,
            "tags": tags,
        }
