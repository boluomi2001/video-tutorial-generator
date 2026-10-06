"""单文件上传 COS（ima 上传第 2 步）。

用法: python up_one.py <本地 md 路径>   ← 从 stdin 读一行 JSON 凭证
凭证字段: token/secret_id/secret_key/region/bucket_name/cos_key/media_id
"""
import json
import sys

from qcloud_cos import CosConfig, CosS3Client


def main() -> None:
    path = sys.argv[1]
    c = json.loads(sys.stdin.read())
    cfg = CosConfig(Region=c["region"], SecretId=c["secret_id"],
                    SecretKey=c["secret_key"], Token=c["token"], Scheme="https")
    client = CosS3Client(cfg)
    with open(path, "rb") as fp:
        resp = client.put_object(Bucket=c["bucket_name"], Body=fp,
                                 Key=c["cos_key"], ContentType="text/markdown")
    print("ETag:", resp.get("ETag"), "| media_id:", c.get("media_id", ""))


if __name__ == "__main__":
    main()
