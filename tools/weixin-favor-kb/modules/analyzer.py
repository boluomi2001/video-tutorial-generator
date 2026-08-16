"""LLM 内容分析器：摘要、要点、资源、行动建议（含重试 + 视觉分析）"""

import base64
import json
import time
from pathlib import Path

from loguru import logger
from openai import OpenAI

INITIAL_DELAY = 5
MAX_DELAY = 120
BACKOFF_FACTOR = 2

_ANALYZE_PROMPT = """分析以下视频内容，生成知识笔记。

类别: {category}
标签: {tags}

转录文字:
{transcript}

画面文字:
{ocr_text}

输出 JSON:
{{
  "summary": "3-5句摘要",
  "key_points": ["要点1", "要点2"],
  "resources": ["工具/网站/书籍"],
  "action_items": {{
    "dev": ["开发相关建议"],
    "life": ["生活相关建议"],
    "tech_summary": ["技术总结建议"]
  }}
}}"""

_VISUAL_PROMPT = """你正在分析一个视频的完整内容。以下是该视频的关键帧截图（按时间顺序排列）。

类别: {category}
标签: {tags}

📝 视频语音转录:
{transcript}

📊 画面中识别到的文字（OCR）:
{ocr_text}

请仔细观察每一张截图中的：
- 界面操作、软件演示、代码编辑器
- 图表、数据、架构图
- PPT 幻灯片、文字标题
- 产品界面、网站页面
- 人物、场景、道具

结合语音转录和画面内容，输出 JSON:
{{
  "visual_observations": "描述你在画面中看到了什么，重点关注操作流程和关键信息",
  "summary": "3-5句摘要，结合画面和语音全面概括视频核心内容",
  "key_points": ["要点1", "要点2", ...],
  "resources": ["提到的工具/网站/书籍"],
  "action_items": {{
    "dev": ["开发者可立即实践的建议"],
    "life": ["生活/工作相关建议"],
    "tech_summary": ["核心技术总结"]
  }}
}}"""


def _llm_call_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> str:
    delay = INITIAL_DELAY
    attempt = 0
    while True:
        attempt += 1
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=180.0,
            )
            msg = response.choices[0].message
            content = msg.content or msg.reasoning or ""
            if content.strip():
                return content
            logger.warning("LLM 分析返回空内容 (第{}次), {}s 后重试", attempt, delay)
        except Exception as e:
            err_str = str(e)
            # 格式错误不重试（如图片格式不支持）
            if "400" in err_str or "deserialize" in err_str:
                raise RuntimeError(f"LLM 格式错误(不重试): {err_str[:200]}")
            logger.warning(
                "LLM 分析失败 (第{}次), {}s 后重试: {}", attempt, delay, err_str[:120]
            )
        time.sleep(delay)
        delay = min(delay * BACKOFF_FACTOR, MAX_DELAY)
    return None


def _normalize_list(val) -> list[str]:
    """确保值为列表：字符串 → 按换行拆成多项，单字符串 → 包装为列表。"""
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    if isinstance(val, str) and val.strip():
        if "\n" in val:
            return [line.strip() for line in val.split("\n") if line.strip()]
        return [val.strip()]
    return []


def _normalize_action_items(raw: dict) -> dict[str, list[str]]:
    return {
        "dev": _normalize_list(raw.get("dev", [])),
        "life": _normalize_list(raw.get("life", [])),
        "tech_summary": _normalize_list(raw.get("tech_summary", [])),
    }


def _extract_json(text: str) -> dict | None:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


class ContentAnalyzer:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0)
        self.model = model

    def analyze(
        self,
        transcript: str,
        ocr_text: str,
        category: str,
        tags: list[str],
        keyframe_paths: list[str] | None = None,
    ) -> dict:
        max_chars = 4000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars]

        # 有关键帧时，选 3 张代表性的发给视觉模型
        if keyframe_paths and any(Path(p).exists() for p in keyframe_paths):
            return self._visual_analyze(transcript, ocr_text, category, tags, keyframe_paths)

        prompt = _ANALYZE_PROMPT.format(
            category=category,
            tags=", ".join(tags),
            transcript=transcript or "（无转录文字）",
            ocr_text=ocr_text[:1000] if ocr_text else "（无画面文字）",
        )

        logger.info("调用 LLM 分析: category={}, model={}", category, self.model)

        content = _llm_call_with_retry(
            self.client,
            self.model,
            messages=[{"role": "user", "content": prompt}],
        )

        result = _extract_json(content)
        if result is None:
            logger.error("分析 JSON 解析失败: {}", content[:200])
            return _empty_analysis(category, tags)

        action_items = _normalize_action_items(result.get("action_items", {}))
        analysis: dict = {
            "summary": result.get("summary", ""),
            "key_points": result.get("key_points", []),
            "resources": result.get("resources", []),
            "action_items": action_items,
            "category": category,
            "tags": tags,
        }

        logger.success(
            "分析完成: {} 个要点, {} 个资源, {} 条建议",
            len(analysis["key_points"]),
            len(analysis["resources"]),
            sum(len(v) for v in action_items.values()),
        )
        return analysis

    def _visual_analyze(
        self,
        transcript: str,
        ocr_text: str,
        category: str,
        tags: list[str],
        keyframe_paths: list[str],
    ) -> dict:
        """使用视觉模型分析关键帧 + 转录文字。"""
        # Qwen3-VL 256K 上下文，选 5 张均匀分布的代表性帧
        existing = [p for p in keyframe_paths if Path(p).exists()]
        max_frames = 5
        if len(existing) > max_frames:
            step = max(1, len(existing) // max_frames)
            selected = [existing[i] for i in range(0, len(existing), step)][:max_frames]
        else:
            selected = existing

        logger.info("视觉分析: {}/{} 张关键帧, model={}", len(selected), len(existing), self.model)

        # 构建多模态消息
        content_blocks: list[dict] = [
            {
                "type": "text",
                "text": _VISUAL_PROMPT.format(
                    category=category,
                    tags=", ".join(tags),
                    transcript=transcript[:4000] or "（无转录文字）",
                    ocr_text=ocr_text[:2000] if ocr_text else "（无画面文字）",
                ),
            }
        ]

        for path in selected:
            try:
                img_data = Path(path).read_bytes()
                b64 = base64.b64encode(img_data).decode("utf-8")
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except Exception as e:
                logger.warning("读取关键帧失败: {} - {}", path, e)

        try:
            content = _llm_call_with_retry(
                self.client,
                self.model,
                messages=[{"role": "user", "content": content_blocks}],
            )
        except RuntimeError as e:
            logger.warning("视觉模型不支持图片，回退纯文本: {}", e)
            return self.analyze(transcript, ocr_text, category, tags)

        result = _extract_json(content)
        if result is None:
            logger.warning("视觉分析 JSON 解析失败，回退纯文本分析")
            return self.analyze(transcript, ocr_text, category, tags)

        action_items = _normalize_action_items(result.get("action_items", {}))
        analysis: dict = {
            "summary": result.get("summary", ""),
            "visual_observations": result.get("visual_observations", ""),
            "key_points": result.get("key_points", []),
            "resources": result.get("resources", []),
            "action_items": action_items,
            "category": category,
            "tags": tags,
        }

        logger.success(
            "视觉分析完成: {} 个要点, {} 个资源, {} 条建议",
            len(analysis["key_points"]),
            len(analysis["resources"]),
            sum(len(v) for v in action_items.values()),
        )
        return analysis


def _empty_analysis(category: str, tags: list[str]) -> dict:
    return {
        "summary": "",
        "key_points": [],
        "resources": [],
        "action_items": {"dev": [], "life": [], "tech_summary": []},
        "category": category,
        "tags": tags,
    }
