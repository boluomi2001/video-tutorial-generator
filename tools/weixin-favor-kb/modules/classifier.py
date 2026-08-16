"""内容分类器：预设类别 + LLM 智能分类（含重试）"""

import json
import time

from loguru import logger
from openai import OpenAI

_CLASSIFY_PROMPT = """将以下视频内容归入最合适的类别，并提取 2-5 个标签。

类别列表: {categories}

{classify_rules}

视频内容:
{text}

只输出 JSON: {{"category": "类别", "tags": ["标签1", "标签2"]}}"""

INITIAL_DELAY = 5
MAX_DELAY = 120
BACKOFF_FACTOR = 2


def _llm_call_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.1,
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
                timeout=120.0,
            )
            msg = response.choices[0].message
            content = msg.content or msg.reasoning or ""
            if content.strip():
                return content
            logger.warning("LLM 返回空内容 (第{}次), {}s 后重试", attempt, delay)
        except Exception as e:
            logger.warning(
                "LLM 分类失败 (第{}次), {}s 后重试: {}", attempt, delay, str(e)[:120]
            )
        time.sleep(delay)
        delay = min(delay * BACKOFF_FACTOR, MAX_DELAY)
    return None


def _extract_json(text: str) -> dict | None:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def classify_content(
    text: str,
    llm_client: OpenAI,
    model: str,
    categories: list[str] | None = None,
    classify_rules: str = "",
) -> tuple[str, list[str]]:
    if categories is None:
        categories = ["其他"]

    truncated = text[:1500] if len(text) > 1500 else text

    prompt = _CLASSIFY_PROMPT.format(
        categories=", ".join(categories),
        classify_rules=classify_rules,
        text=truncated,
    )

    content = _llm_call_with_retry(
        llm_client,
        model,
        messages=[{"role": "user", "content": prompt}],
    )

    result = _extract_json(content)
    if result is None:
        logger.error("分类 JSON 解析失败: {}", content[:200])
        return "其他", []

    category = result.get("category", "其他")
    tags = result.get("tags", [])

    if category not in categories:
        logger.warning("未知类别 '{}', 回退到 '其他'", category)
        category = "其他"

    logger.info("分类结果: {} | 标签: {}", category, tags)
    return category, tags
