import json, os, sys
from qcloud_cos import CosConfig, CosS3Client

HERE = os.path.dirname(os.path.abspath(__file__))
Q = json.load(open(os.path.join(HERE, "_q2.json"), encoding="utf-8"))
QMAP = {o["seq"]: o for o in Q}


def save_and_upload(payload: dict) -> None:
    """payload: {"seq": {"token","secret_id","secret_key","region","bucket_name","cos_key","media_id"}}"""
    # 持久化凭证
    for seq, c in payload.items():
        json.dump(c, open(os.path.join(HERE, "_c2_%s.json" % seq), "w", encoding="utf-8"), ensure_ascii=False)
    # 逐个上传 COS
    for seq in payload:
        c = payload[seq]
        item = QMAP[int(seq)]
        cfg = CosConfig(Region=c["region"], SecretId=c["secret_id"], SecretKey=c["secret_key"],
                        Token=c["token"], Scheme="https")
        client = CosS3Client(cfg)
        with open(item["path"], "rb") as fp:
            resp = client.put_object(Bucket=c["bucket_name"], Body=fp, Key=c["cos_key"],
                                     ContentType="text/markdown")
        print("seq %s ETag: %s | media_id: %s" % (seq, resp.get("ETag"), c.get("media_id", "")))


if __name__ == "__main__":
    data = json.loads(sys.stdin.read())
    save_and_upload(data)
