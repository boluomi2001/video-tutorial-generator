# -*- coding: utf-8 -*-
"""
批量把视频 oid 换成可分享的 H5 链接，生成完整清单。

输入:  dali_shufang_videos.json（mcp_list_videos.py 产出）
输出:
  - <name>_links.txt      纯链接（一行一条，可直接喂给 auto_run.py --from-file）
  - <name>_index.md       人读清单表（序号/日期/标题/互动/链接）
  - <name>_full.json      完整数据（含链接）
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_list_videos import mcp_call  # noqa: E402


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "D:/work/2026-07-12-13-31-07/output/dali_shufang_videos.json")
    rows = json.load(open(src, encoding="utf-8"))
    out_base = src.with_name(src.stem.replace("_videos", ""))

    ok, fail = 0, 0
    for i, v in enumerate(rows, 1):
        if v.get("h5_url"):
            ok += 1
            continue
        try:
            r = mcp_call("get_wxchannels_video_share_url", {"oid": v["oid"]})
            data = r.get("data", r) if isinstance(r, dict) else {}
            url = data.get("feedH5Url") or ""
            if not url:
                urls = data.get("urlList") or []
                url = urls[0].get("feedH5Url") if urls else ""
            v["h5_url"] = url
            ok += 1 if url else 0
            fail += 0 if url else 1
            print(f"[{i:>3}/{len(rows)}] {'✓' if url else '✗'} {url}  {v['title'][:34]}")
        except Exception as e:
            v["h5_url"] = ""
            fail += 1
            print(f"[{i:>3}/{len(rows)}] ✗ 失败: {type(e).__name__}: {e}")
        time.sleep(0.35)

    # 1) links.txt
    links = [v["h5_url"] for v in rows if v.get("h5_url")]
    (out_base.parent / (out_base.name + "_links.txt")).write_text(
        "\n".join(links) + "\n", encoding="utf-8")

    # 2) full.json
    (out_base.parent / (out_base.name + "_full.json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) index.md
    total_like = sum(v.get("like", 0) for v in rows)
    total_fwd = sum(v.get("forward", 0) for v in rows)
    lines = [
        "# 大李书房一盏灯 · 视频清单",
        "",
        f"- 账号：大李书房一盏灯（科普博主，新疆 乌鲁木齐）",
        f"- 简介：为了逼自己一把 Learn in public 而开了这个账号，专门采用费曼学习法来和各位一起学习 AI。日拱一卒，无限进步",
        f"- 抓到视频数：**{len(rows)}** 条（账号页面标记 89 条，差 3 条为置顶/已删除/受限）",
        f"- 时间范围：{rows[-1]['pub_date']} ~ {rows[0]['pub_date']}",
        f"- 累计点赞 {total_like:,}  累计转发 {total_fwd:,}",
        f"- 链接成功换取：{ok} 条，失败 {fail} 条",
        "",
        "| # | 发布日期 | 点赞 | 评论 | 转发 | 标题 | 链接 |",
        "|---:|---|---:|---:|---:|---|---|",
    ]
    for i, v in enumerate(rows, 1):
        t = (v["title"] or "").replace("|", "\\|")[:44]
        lines.append(
            f"| {i} | {v['pub_date']} | {v.get('like',0):,} | {v.get('comment',0):,} | "
            f"{v.get('forward',0):,} | {t} | [{v.get('h5_url','')}]({v.get('h5_url','')}) |"
        )
    (out_base.parent / (out_base.name + "_index.md")).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    print()
    print(f"[✓] links : {out_base.name}_links.txt ({len(links)} 条)")
    print(f"[✓] index : {out_base.name}_index.md")
    print(f"[✓] full  : {out_base.name}_full.json")


if __name__ == "__main__":
    main()
