# -*- coding: utf-8 -*-
"""
列出指定微信视频号账号的全部视频（含链接与互动数据）。

数据来源：本机下载器 MCP（http://127.0.0.1:2022/mcp）暴露的视频号页面连接。
本脚本直接调用下载器的 HTTP API 等价接口需要 socket 会话，故此处通过
「下载器 API 的 /api/wxchannels/account_videos」类接口翻页抓取。

用法:
    python list_account_videos.py <username> [-o out.json]

输出:
    - 控制台：按发布时间倒序的清单表
    - JSON 文件：完整元数据（默认 account_videos.json）
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:2022"


def _call(path: str, payload: dict, timeout: int = 60) -> dict:
    """调用下载器 HTTP API。"""
    url = API_BASE + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all(username: str, verbose: bool = True) -> list:
    """翻页拉取账号全部视频。"""
    all_items = []
    marker = None
    page = 0
    total_expected = None

    while True:
        page += 1
        payload = {"username": username}
        if marker:
            payload["next_marker"] = marker
        resp = _call("/api/wxchannels/account_videos", payload)

        data = resp.get("data", {}) if "data" in resp else resp
        if total_expected is None:
            total_expected = data.get("feedsCount")
            if verbose:
                print(f"[i] 账号总视频数 feedsCount = {total_expected}")

        objs = data.get("object") or data.get("objectList") or []
        all_items.extend(objs)
        if verbose:
            print(f"[i] 第 {page} 页: +{len(objs)} 条, 累计 {len(all_items)} 条")

        cont = data.get("continueFlag", 0)
        marker = data.get("lastBuffer") or ""
        if not cont or not marker or not objs:
            break
        time.sleep(0.6)  # 轻微限速，避免触发风控

    return all_items


def normalize(o: dict) -> dict:
    """把原始 object 归一化为精简结构。"""
    ct = o.get("createtime") or 0
    try:
        pub = datetime.fromtimestamp(int(ct)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pub = ""
    desc = (o.get("objectDesc") or "").strip()
    oid = o.get("id") or o.get("displayid") or ""
    return {
        "id": oid,
        "displayid": o.get("displayid") or "",
        "object_nonce_id": o.get("objectNonceId") or "",
        "desc": desc,
        "desc_first_line": desc.splitlines()[0] if desc else "",
        "createtime": int(ct) if ct else 0,
        "pub_date": pub,
        "like_count": o.get("likeCount", 0),
        "comment_count": o.get("commentCount", 0),
        "forward_count": o.get("forwardCount", 0),
        "fav_count": o.get("favCount", 0),
        "object_type": o.get("objectType"),
        "username": o.get("username") or "",
        "nickname": o.get("nickname") or "",
    }


def main():
    ap = argparse.ArgumentParser(description="列出视频号账号全部视频")
    ap.add_argument("username", help="账号 username（可省略 @finder 后缀）")
    ap.add_argument("-o", "--out", default="account_videos.json", help="输出 JSON 路径")
    args = ap.parse_args()

    items = fetch_all(args.username)
    norm = [normalize(o) for o in items]
    norm.sort(key=lambda x: x["createtime"], reverse=True)

    Path(args.out).write_text(
        json.dumps(norm, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"===== 共 {len(norm)} 条 =====")
    for i, v in enumerate(norm, 1):
        print(
            f"{i:>3}. [{v['pub_date']}] ♥{v['like_count']:>6} "
            f"💬{v['comment_count']:>5} ↪{v['forward_count']:>6} | "
            f"{v['desc_first_line'][:50]}"
        )
    print()
    print(f"[✓] 已写入 {args.out}")


if __name__ == "__main__":
    main()
