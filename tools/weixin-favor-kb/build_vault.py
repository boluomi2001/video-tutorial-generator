#!/usr/bin/env python3
"""将 output/notes 整理为 Obsidian Vault，含 LLM 分类汇总和行动计划"""

import os
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path

import yaml
from loguru import logger
from openai import OpenAI

NOTES_DIR = Path("output/notes")
VAULT_DIR = Path("obsidian_vault")


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
        cat_emoji = {c["name"]: c.get("emoji", "📎") for c in cats_cfg}
    else:
        cat_names = cats_cfg
        cat_emoji = {}

    return api_key, base_url, model, cat_names, cat_emoji


LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, CATEGORY_ORDER, CATEGORY_EMOJI = _load_config()

INITIAL_DELAY = 5
MAX_DELAY = 120
BACKOFF_FACTOR = 2


def llm_call(prompt: str, max_tokens: int = 8192) -> str:
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=300.0)
    delay = INITIAL_DELAY
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
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


def parse_notes(notes_dir: Path) -> tuple[dict, list[dict]]:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    all_notes: list[dict] = []
    seen: set[str] = set()

    for md_file in sorted(notes_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            continue

        category = fm.get("category", "其他")
        tags = fm.get("tags") or []
        title = fm.get("title", md_file.stem)
        author = fm.get("author", "")
        date = fm.get("date", "")
        content = parts[2]

        stem = md_file.stem
        short_id = stem.rsplit("_", 1)[-1] if "_" in stem else stem[:12]
        safe_title = (
            re.sub(r'[#*?:"<>|/\\（）【】｛｝《》「」：；！？…—\-]', "", title)[
                :50
            ].strip()
            or short_id
        )
        filename = f"{safe_title}.md"

        if short_id in seen:
            continue
        seen.add(short_id)

        summary = ""
        m = re.search(
            r"> \[!summary\] 摘要\n> (.+?)(?=\n##|\n#|\Z)", content, re.DOTALL
        )
        if m:
            summary = m.group(1).strip().replace("> ", " ")

        resources: list[str] = []
        rm = re.search(r"## 🔗 资源与工具\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if rm:
            for line in rm.group(1).strip().split("\n"):
                line = line.strip().lstrip("- ").strip()
                if line:
                    resources.append(line)

        note = {
            "title": title,
            "author": author,
            "date": date,
            "category": category,
            "tags": tags,
            "filename": filename,
            "id": short_id,
            "summary": summary,
            "resources": resources,
        }
        by_cat[category].append(note)
        all_notes.append(note)

    return by_cat, all_notes


def copy_notes_to_vault(all_notes: list[dict], vault_notes: Path):
    vault_notes.mkdir(parents=True, exist_ok=True)
    for note in all_notes:
        # 通过 ID 找到源文件
        src_files = list(NOTES_DIR.glob(f"*{note['id']}*"))
        if src_files:
            shutil.copy2(src_files[0], vault_notes / note["filename"])


def build_category_pages(by_cat: dict[str, list[dict]], vault_cat: Path):
    vault_cat.mkdir(parents=True, exist_ok=True)

    for cat, notes in by_cat.items():
        emoji = CATEGORY_EMOJI.get(cat, "📎")
        safe_cat = cat.replace("/", "-")
        logger.info("生成分类: {} ({} 篇)", cat, len(notes))

        note_summaries = []
        for i, n in enumerate(notes, 1):
            res_str = ", ".join(n["resources"][:5]) if n["resources"] else "无"
            note_summaries.append(
                f"{i}. [{n['author']}] {n['title'][:60]}\n"
                f"   摘要: {n['summary'][:200]}\n"
                f"   资源: {res_str}"
            )
        all_summaries = "\n".join(note_summaries)

        is_tech = cat in ("RAG", "AGENT", "AI/ML", "前端", "后端", "DevOps")

        if is_tech:
            prompt = f"""你是一位资深技术顾问。以下是微信视频号收藏中「{cat}」分类下的 {len(notes)} 篇技术笔记摘要。

请完成以下任务：

## 任务一：知识全景总结
对这 {len(notes)} 篇笔记进行综合性总结，包括：
1. 这个领域的核心关注点和趋势
2. 关键技术/方法的共性发现
3. 值得深入学习的方向

用 200-400 字的 Markdown 段落总结。

## 任务二：学习与行动计划
制定一个由浅入深的行动计划，分三个阶段：
- **🌱 入门了解**：基础概念理解，推荐阅读哪些笔记
- **🔧 动手实践**：可以立即尝试的工具和项目，引用具体 GitHub 仓库名或工具名
- **🚀 深入研究**：进阶方向，适合深入研究的主题

每个行动项用引用标注来源笔记编号（如来自笔记3写 [来源:3]），如果笔记提到了具体的开源项目名称（如 GitHub 仓库名、工具名），必须写明。

笔记列表：
{all_summaries}

输出纯 Markdown，不要输出 JSON 或代码块。"""
        else:
            prompt = f"""你是一位智慧的生活顾问。以下是微信视频号收藏中「{cat}」分类下的 {len(notes)} 篇内容摘要。

请完成：

## 任务一：内容全景总结
对这 {len(notes)} 篇内容进行综合性总结，提取共同的智慧或主题。

## 任务二：实践建议
给出 3-8 条可执行的实践建议，每条标注来源笔记编号（如 [来源:3]）。

笔记列表：
{all_summaries}

输出纯 Markdown。"""

        llm_result = llm_call(prompt, max_tokens=4096)

        lines = [
            f"# {emoji} {cat}",
            "",
            f"> 共 **{len(notes)}** 篇笔记",
            "",
            llm_result,
            "",
            "---",
            "",
            "## 📂 全部笔记",
            "",
            "| # | 标题 | 作者 | 标签 |",
            "|---|------|------|------|",
        ]

        for i, note in enumerate(notes, 1):
            tag_str = " ".join(f"`{t}`" for t in note["tags"][:3])
            lines.append(
                f"| {i} | [[{note['filename']}]] | {note['author']} | {tag_str} |"
            )

        cat_file = vault_cat / f"{emoji} {safe_cat}.md"
        cat_file.write_text("\n".join(lines), encoding="utf-8")
        logger.success("完成: {} ({})", cat, len(notes))


def build_home(by_cat: dict[str, list[dict]], all_notes: list[dict], vault_dir: Path):
    all_tags: dict[str, int] = defaultdict(int)
    for n in all_notes:
        for t in n["tags"]:
            all_tags[t] += 1
    total_resources = sum(len(n["resources"]) for n in all_notes)

    lines = [
        "# 📖 视频号知识库",
        "",
        f"> 共 **{len(all_notes)}** 篇笔记 · {len(all_tags)} 个标签 · {total_resources} 条资源",
        "",
        "## 📂 分类浏览",
        "",
        "每个分类包含 **AI 知识全景总结** + **由浅入深的行动计划**，点击进入查看。",
        "",
    ]

    for cat in CATEGORY_ORDER:
        count = len(by_cat.get(cat, []))
        if count == 0:
            continue
        emoji = CATEGORY_EMOJI.get(cat, "📎")
        safe_cat = cat.replace("/", "-")
        lines.append(f"### {emoji} [[分类/{emoji} {safe_cat}|{cat}]] ({count} 篇)")
        lines.append("")

    lines.extend(["---", f"*最后更新: 2026-04-12*"])

    (vault_dir / "🏠 首页.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if VAULT_DIR.exists():
        shutil.rmtree(VAULT_DIR)
    VAULT_DIR.mkdir(parents=True)
    (VAULT_DIR / ".obsidian").mkdir()

    logger.info("解析笔记...")
    by_cat, all_notes = parse_notes(NOTES_DIR)
    logger.info("共 {} 篇笔记, {} 个分类", len(all_notes), len(by_cat))

    logger.info("复制笔记到 vault 根目录...")
    copy_notes_to_vault(all_notes, VAULT_DIR)

    logger.info("生成分类页（含 LLM 汇总）...")
    build_category_pages(by_cat, VAULT_DIR / "分类")

    logger.info("生成首页...")
    build_home(by_cat, all_notes, VAULT_DIR)

    logger.success("✅ Vault 完成: {}", VAULT_DIR)
    print(f"\n复制到 Windows:")
    print(f"  cp -r {VAULT_DIR} /mnt/c/Users/YOUR_USERNAME/Documents/")


if __name__ == "__main__":
    main()
