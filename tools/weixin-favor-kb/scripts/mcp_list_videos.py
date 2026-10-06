# -*- coding: utf-8 -*-
"""
通过本机下载器 MCP(JSON-RPC, http://127.0.0.1:2022/mcp) 翻页抓取
指定微信视频号账号的全部视频，输出精简清单。

用法:
    python mcp_list_videos.py <username> [-o account_videos.json]

依赖：下载器在跑 + 视频号页面已连接（get_wxchannels_status.available == true）
"""
import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

MCP_URL = "http://127.0.0.1:2022/mcp"
_rid = [0]


def mcp_call(name: str, args: dict, timeout: int = 90):
    """调用一个 MCP 工具，返回解析后的结果对象。"""
    _rid[0] += 1
    payload = {
        "jsonrpc": "2.0",
        "id": _rid[0],
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        MCP_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")

    # 可能返回 SSE (data: {...}) 或纯 JSON
    data = None
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)
    else:
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                cand = line[5:].strip()
                if cand.startswith("{"):
                    data = json.loads(cand)
                    break
    if data is None:
        raise RuntimeError("无法解析 MCP 响应: " + raw[:300])

    if "error" in data:
        raise RuntimeError(f"MCP error: {data['error']}")

    result = data.get("result", {})
    # MCP 工具结果：content[0].text 通常是 JSON 字符串
    content = result.get("content") or []
    if content:
        txt = content[0].get("text", "")
        try:
            return json.loads(txt)
        except Exception:
            return txt
    return result


def _as_text(v) -> str:
    """objectDesc 是 {description: ..., ...} 结构；兼容 str/dict/list。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("description", "text", "content", "desc", "value"):
            if k in v and v[k]:
                return _as_text(v[k])
        return ""
    if isinstance(v, list):
        return "\n".join(_as_text(x) for x in v if _as_text(x))
    return str(v).strip()


def norm(o: dict) -> dict:
    ct = o.get("createtime") or 0
    try:
        pub = datetime.fromtimestamp(int(ct)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pub = ""
    desc = _as_text(o.get("objectDesc"))
    first = desc.splitlines()[0] if desc else ""
    # 视频号 H5 分享链接由 oid 生成
    oid = o.get("id") or o.get("displayid") or ""
    return {
        "oid": str(oid),
        "desc": desc,
        "title": first,
        "pub_date": pub,
        "createtime": int(ct) if ct else 0,
        "like": o.get("likeCount", 0),
        "comment": o.get("commentCount", 0),
        "forward": o.get("forwardCount", 0),
        "fav": o.get("favCount", 0),
        "object_nonce_id": o.get("objectNonceId") or "",
    }


def fetch_all(username: str):
    items, marker, page = [], None, 0
    feeds_count = None
    while True:
        page += 1
        args = {"username": username}
        if marker:
            args["next_marker"] = marker
        resp = mcp_call("get_wxchannels_account_videos", args)
        data = resp.get("data", resp) if isinstance(resp, dict) else {}
        if feeds_count is None:
            feeds_count = data.get("feedsCount")
        objs = data.get("object") or []
        items.extend(objs)
        print(f"[i] 第 {page} 页: +{len(objs)}, 累计 {len(items)}"
              + (f" / 共 {feeds_count}" if feeds_count else ""))
        cont = data.get("continueFlag", 0)
        marker = data.get("lastBuffer") or ""
        if not cont or not marker or not objs:
            break
        time.sleep(0.8)
    return feeds_count, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("-o", "--out", default="account_videos.json")
    a = ap.parse_args()

    feeds_count, objs = fetch_all(a.username)
    rows = [norm(o) for o in objs]
    rows.sort(key=lambda x: x["createtime"], reverse=True)
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n===== 抓取 {len(rows)} 条 (账号标记总数 {feeds_count}) =====")
    for i, v in enumerate(rows, 1):
        print(f"{i:>3}. [{v['pub_date']}] ♥{v['like']:>6} 💬{v['comment']:>5} "
              f"↪{v['forward']:>6} | {v['title'][:56]}")
    print(f"\n[✓] 已写入 {a.out}")


if __name__ == "__main__":
    main()
