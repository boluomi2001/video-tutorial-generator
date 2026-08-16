#!/usr/bin/env python3
"""用 LLM 重新分类所有 261 篇笔记"""

import json
import os
import re
import time
from pathlib import Path

import yaml
from loguru import logger
from openai import OpenAI

NOTES_DIR = Path("output/notes")


def _load_config():
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        logger.error("config.yaml 不存在")
        raise SystemExit(1)
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    llm = cfg.get("llm", {})
    api_key = llm.get("api_key", "") or os.environ.get("LLM_API_KEY", "")
    base_url = llm.get("base_url", "https://api.openai.com/v1")
    model = llm.get("model", "gpt-4o")

    cats_cfg = cfg.get("categories", [])
    if cats_cfg and isinstance(cats_cfg[0], dict):
        cat_names = [c["name"] for c in cats_cfg]
        cat_desc = {c["name"]: c.get("description", "") for c in cats_cfg}
    else:
        cat_names = cats_cfg
        cat_desc = {}

    classify_rules = cfg.get("classify_rules", "")
    return api_key, base_url, model, cat_names, cat_desc, classify_rules


LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, NEW_CATEGORIES, CATEGORY_DESC, CLASSIFY_RULES = (
    _load_config()
)

INITIAL_DELAY = 5
MAX_DELAY = 120
BACKOFF_FACTOR = 2

NEW_CATEGORIES = [
    "RAG",
    "AGENT",
    "AI/ML",
    "前端",
    "后端",
    "DevOps",
    "开发",
    "创富",
    "文史哲",
    "感悟",
    "运动",
    "生活技巧",
    "其他",
]


def _build_classify_prompt():
    cat_lines = []
    for i, name in enumerate(NEW_CATEGORIES, 1):
        desc = CATEGORY_DESC.get(name, "")
        cat_lines.append(f"{i}. **{name}** — {desc}" if desc else f"{i}. **{name}**")

    return """你是一个内容分类专家。请将以下视频笔记归入最合适的类别，并提取 2-5 个标签。

## 预设类别（含分类标准）

{cat_block}

{classify_rules}

## 视频信息
标题: {title}
作者: {author}
原分类: {old_category}
原标签: {old_tags}

摘要: {summary}

只输出 JSON: {{"category": "类别", "tags": ["标签1", "标签2"]}}"""


_CLASSIFY_PROMPT = _build_classify_prompt()


def llm_call(prompt: str, max_tokens: int = 4096) -> str:
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=300.0)
    delay = INITIAL_DELAY
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            msg = resp.choices[0].message
            content = msg.content or msg.reasoning or ""
            if content.strip():
                return content
            logger.warning("LLM 返回空 (第{}次)", attempt)
        except Exception as e:
            logger.warning(
                "LLM 失败 (第{}次), {}s 后重试: {}", attempt, delay, str(e)[:120]
            )
        time.sleep(delay)
        delay = min(delay * BACKOFF_FACTOR, MAX_DELAY)


def extract_json(text: str) -> dict | None:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


def main():
    notes = []
    for f in sorted(NOTES_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            continue

        title = fm.get("title", f.stem)
        author = fm.get("author", "")
        old_cat = fm.get("category", "其他")
        old_tags = fm.get("tags") or []

        m = re.search(
            r"> \[!summary\] 摘要\n> (.+?)(?=\n##|\n#|\Z)", parts[2], re.DOTALL
        )
        summary = m.group(1).strip()[:200] if m else ""

        notes.append(
            {
                "file": f,
                "title": title,
                "author": author,
                "old_cat": old_cat,
                "old_tags": old_tags,
                "summary": summary,
                "frontmatter": fm,
                "content": parts[2],
            }
        )

    logger.info("共 {} 篇笔记需要重新分类", len(notes))

    changed = 0
    for i, note in enumerate(notes):
        prompt = _CLASSIFY_PROMPT.format(
            cat_block="\n".join(
                f"{j}. **{n}** — {CATEGORY_DESC.get(n, '')}"
                for j, n in enumerate(NEW_CATEGORIES, 1)
            ),
            classify_rules=CLASSIFY_RULES,
            title=note["title"][:80],
            author=note["author"],
            old_category=note["old_cat"],
            old_tags=", ".join(note["old_tags"]),
            summary=note["summary"][:300] if note["summary"] else "（无摘要）",
        )

        result_text = llm_call(prompt)
        result = extract_json(result_text)

        if result is None:
            logger.error(
                "[{}/{}] JSON 解析失败: {}", i + 1, len(notes), note["title"][:40]
            )
            continue

        new_cat = result.get("category", "其他")
        new_tags = result.get("tags", note["old_tags"])

        if new_cat not in NEW_CATEGORIES:
            logger.warning(
                "未知类别 '{}'，回退到 其他: {}", new_cat, note["title"][:40]
            )
            new_cat = "其他"

        if new_cat != note["old_cat"]:
            changed += 1
            logger.info(
                "[{}/{}] {} → {} | {}",
                i + 1,
                len(notes),
                note["old_cat"],
                new_cat,
                note["title"][:50],
            )

        # 写回 frontmatter
        note["frontmatter"]["category"] = new_cat
        note["frontmatter"]["tags"] = new_tags

        new_fm = yaml.dump(
            note["frontmatter"], allow_unicode=True, default_flow_style=False
        ).strip()
        new_content = f"---\n{new_fm}\n---{note['content']}"
        note["file"].write_text(new_content, encoding="utf-8")

    logger.success("重新分类完成: {} 篇, {} 篇变更", len(notes), changed)

    # 统计新分类
    from collections import Counter

    cats = []
    for f in sorted(NOTES_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                cats.append(fm.get("category", "其他"))
            except:
                cats.append("其他")

    for cat, count in Counter(cats).most_common():
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
