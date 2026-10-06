#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ima upload helper (project-local, durable).

Subcommands:
  queue                       -- regenerate _upqueue.json from the batch manifest
  info <seq>                  -- print {seq,path,name,size} for one item
  save-cred <json_file>       -- persist COS credential returned by create_media
  upload <local_md_path>      -- put_object using saved credential
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # tools/weixin-favor-kb
MANIFEST = os.path.join(
    ROOT, "output", "batch_20261005_005801", ".ima_pending_batch.json"
)
QUEUE = os.path.join(HERE, "_upqueue.json")
CRED = os.path.join(HERE, "_cred.json")
DONE = os.path.join(HERE, "_done.json")


def build_queue():
    d = json.load(open(MANIFEST, encoding="utf-8"))
    out = []
    for it in d["items"]:
        fp = it["final_md"]
        out.append(
            {
                "seq": it["seq"],
                "path": fp,
                "name": it["title"],
                "size": os.path.getsize(fp),
            }
        )
    json.dump(out, open(QUEUE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("queued", len(out))


def info(seq):
    q = json.load(open(QUEUE, encoding="utf-8"))
    for o in q:
        if o["seq"] == seq:
            print(json.dumps(o, ensure_ascii=False))
            return
    print("not found")


def save_cred(path):
    data = json.load(open(path, encoding="utf-8"))
    if "cos_credential" in data:
        data = data["cos_credential"]
    json.dump(data, open(CRED, "w", encoding="utf-8"), ensure_ascii=False)
    print("saved cred")


def upload(local_path):
    from qcloud_cos import CosConfig, CosS3Client

    c = json.load(open(CRED, encoding="utf-8"))
    cfg = CosConfig(
        Region=c["region"],
        SecretId=c["secret_id"],
        SecretKey=c["secret_key"],
        Token=c["token"],
        Scheme="https",
    )
    client = CosS3Client(cfg)
    with open(local_path, "rb") as fp:
        resp = client.put_object(
            Bucket=c["bucket_name"], Body=fp, Key=c["cos_key"], ContentType="text/markdown"
        )
    print("ETag:", resp.get("ETag"))


def upload_seq(seq):
    """Upload the file for a given seq using scripts/_ima_up/_cred_<seq>.json"""
    from qcloud_cos import CosConfig, CosS3Client

    q = {o["seq"]: o for o in json.load(open(QUEUE, encoding="utf-8"))}
    item = q[int(seq)]
    c = json.load(open(os.path.join(HERE, "_cred_%s.json" % seq), encoding="utf-8"))
    cfg = CosConfig(
        Region=c["region"],
        SecretId=c["secret_id"],
        SecretKey=c["secret_key"],
        Token=c["token"],
        Scheme="https",
    )
    client = CosS3Client(cfg)
    with open(item["path"], "rb") as fp:
        resp = client.put_object(
            Bucket=c["bucket_name"],
            Body=fp,
            Key=c["cos_key"],
            ContentType="text/markdown",
        )
    print("seq", seq, "ETag:", resp.get("ETag"), "| media_id:", c.get("media_id", ""))


def mark_done(seq):
    arr = []
    if os.path.exists(DONE):
        arr = json.load(open(DONE, encoding="utf-8"))
    if seq not in arr:
        arr.append(seq)
    json.dump(arr, open(DONE, "w", encoding="utf-8"))
    print("done total:", len(arr))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "queue":
        build_queue()
    elif cmd == "info":
        info(int(sys.argv[2]))
    elif cmd == "save-cred":
        save_cred(sys.argv[2])
    elif cmd == "upload":
        upload(sys.argv[2])
    elif cmd == "upseq":
        upload_seq(sys.argv[2])
    elif cmd == "mark-done":
        mark_done(int(sys.argv[2]))
