#!/usr/bin/env python
"""下载器根证书加固 —— 用「本机自生成 CA」替换发行包内置的 SunnyNet 旧证书。

为什么必须做（对外分享时尤其）：
    下载器要抓浏览器的 HTTPS 流量，所以会往系统里装一个中间人根证书。
    它**内置的 SunnyNet 证书用的是硬编码私钥**——下载器自己的接口都在警告：
        「该证书为旧版SunnyNet证书，使用硬编码密钥对，存在安全风险」
    私钥公开 = 任何知道你该根证书存在的人都能解你的 HTTPS。
    自己用问题不大；但把这套装给别人，等于给别人装了个"公开私钥的根证书"。

本脚本做什么：
    1. 在下载器目录下生成本机专有 CA（RSA 2048 / SHA-256 / 10 年）
       `certs/local-ca.pem` + `certs/local-ca.key`（私钥只留在本机）
    2. 把 CA 导入**当前用户**的「受信任的根证书颁发机构」（不需要管理员）
    3. 写入下载器配置 `cert.file` / `cert.key` / `cert.name`（这三个字段支持热重载）
    4. 校验：通过下载器代理请求一次 HTTPS，确认链路用的是我们的 CA
    5. `--remove-legacy` 时删除旧 SunnyNet 证书（用户存储 + 机器存储）

用法：
    python scripts/make_ca.py                    # 生成 + 导入 + 配置 + 校验
    python scripts/make_ca.py --remove-legacy    # 顺带删掉旧 SunnyNet 证书
    python scripts/make_ca.py --status           # 只看现状，不动任何东西
    python scripts/make_ca.py --rollback         # 清空 cert.* 配置（回退到内置证书）

注意：
    · 导入根证书时 Windows 可能弹一次安全确认，**要允许**，否则证书不生效；
    · 需要下载器在跑（配置热重载 + 校验都要连 MCP，端口 2022）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_ROOT.parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

OK, WARN, FAIL, STEP = "[OK]", "[WARN]", "[FAIL]", "[STEP]"
CA_CN = "WX Video Download Local CA"
CA_ORG = "Local Machine Generated"
CA_ORG_UNIT = "wx-tutorial-generator"


def say(tag: str, msg: str) -> None:
    print(f"{tag} {msg}", flush=True)


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------- #
# 定位下载器
# --------------------------------------------------------------------------- #

def find_downloader_dir() -> Path | None:
    for d in (REPO_ROOT / "tools" / "wx_channels_download",
              REPO_ROOT / "tools" / "wx_channels_download_v260907"):
        if d.exists() and list(d.glob("*.exe")):
            return d
    return None


def mcp_call(name: str, args: dict | None = None, timeout: int = 60) -> dict:
    from modules.wx_download import MCPClient
    return MCPClient().call(name, args or {})


def port_open() -> bool:
    try:
        with opener().open("http://127.0.0.1:2022/", timeout=2):
            return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 1. 生成 CA
# --------------------------------------------------------------------------- #

def generate_ca(out_pem: Path, out_key: Path, days: int = 3650) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, CA_CN),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORG),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, CA_ORG_UNIT),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False,
                          decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .sign(key, hashes.SHA256())
    )

    out_pem.parent.mkdir(parents=True, exist_ok=True)
    out_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    out_key.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,   # 下载器要 RSA PRIVATE KEY
        encryption_algorithm=serialization.NoEncryption(),
    ))
    try:
        os.chmod(out_key, 0o600)
    except Exception:
        pass
    print(f"      CA:  CN={CA_CN}")
    print(f"      指纹: {cert.fingerprint(hashes.SHA256()).hex()[:32]}…")
    print(f"      有效期: {now.date()} → {(now + dt.timedelta(days=days)).date()}")


def export_der(pem: Path, der: Path) -> None:
    """certutil / Import-Certificate 用 DER 更稳。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_pem_x509_certificate(pem.read_bytes())
    der.write_bytes(cert.public_bytes(serialization.Encoding.DER))


# --------------------------------------------------------------------------- #
# 2. 导入「当前用户」根证书存储（无需管理员）
# --------------------------------------------------------------------------- #

def install_ca(der: Path) -> bool:
    ps = ("Import-Certificate -FilePath '{f}' "
          "-CertStoreLocation 'Cert:\\CurrentUser\\Root' | Out-Null").format(f=der)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps],
                           capture_output=True, text=True, timeout=90,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            say(OK, "已导入当前用户「受信任的根证书颁发机构」")
            return True
        say(WARN, f"Import-Certificate 失败：{(r.stderr or r.stdout or '')[:150]}")
    except Exception as e:
        say(WARN, f"Import-Certificate 异常：{str(e)[:120]}")

    # 兜底：certutil（可能弹一次确认框）
    try:
        r = subprocess.run(["certutil", "-user", "-addstore", "Root", str(der)],
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            say(OK, "已导入（certutil）")
            return True
        say(FAIL, f"certutil 也失败：{(r.stdout or r.stderr or '')[-200:]}")
    except Exception as e:
        say(FAIL, f"certutil 异常：{str(e)[:120]}")
    print("      可手动导入：双击 certs\\local-ca.pem → 安装证书 → "
          "本地计算机/当前用户 → 受信任的根证书颁发机构")
    return False


def remove_store_cert(store: str, cn: str, user: bool = True) -> bool:
    cmd = (["certutil", "-user", "-delstore", store, cn] if user
           else ["certutil", "-delstore", store, cn])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0 and "错误" not in out and "Error" not in out
        return ok
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 3. 写入下载器配置（热重载）
# --------------------------------------------------------------------------- #

def get_config() -> dict:
    return mcp_call("get_config")


def apply_cert_config(pem: Path, key: Path, dl: Path) -> bool:
    """写 cert.file / cert.key / cert.name。

    优先走 MCP `update_config`（这三个字段 hotReload，能立即生效）；
    下载器没在跑时**直接改 config.yaml**，等它下次启动时读取。
    两条路都走，保证"改了就一定落盘"。
    """
    ok = False
    if port_open():
        try:
            r = mcp_call("update_config", {"values": {
                "cert.file": str(pem),
                "cert.key": str(key),
                "cert.name": CA_CN,
            }})
            token = (r or {}).get("restart_token")
            say(OK, "已通过 MCP 写入 cert.*（热重载）"
                    + (f"，restart_token={str(token)[:12]}…" if token else ""))
            ok = True
        except Exception as e:
            say(WARN, f"MCP 写配置失败（{str(e)[:90]}），改为直接改 config.yaml")
    else:
        say(WARN, "下载器没在跑 → 直接改 config.yaml（下次启动生效）")

    if patch_config_yaml(dl, str(pem), str(key), CA_CN):
        say(OK, f"config.yaml 已更新：{dl / 'config.yaml'}")
        ok = True
    return ok


def patch_config_yaml(dl: Path, cert_file: str, cert_key: str,
                      cert_name: str) -> bool:
    """在下载器 config.yaml 里就地写 cert 段（保留其它内容）。

    下载器默认不带 cert 段（用内置证书），所以通常是"追加"。
    """
    path = dl / "config.yaml"
    if not path.exists():
        return False
    path.with_suffix(".yaml.bak").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    quoted = lambda s: '"%s"' % str(s).replace("\\", "/").replace('"', '\\"')

    start = next((i for i, ln in enumerate(lines)
                  if ln.strip() == "cert:" and not ln[:1].isspace()), None)
    if start is not None:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip() and not lines[j][:1].isspace():
                end = j
                break
        blk = lines[start:end]
        want = {"file": cert_file, "key": cert_key, "name": cert_name}
        for k, ln in enumerate(blk):
            for key, val in want.items():
                if ln.strip().startswith(f"{key}:"):
                    blk[k] = f"  {key}: {quoted(val)}"
                    want.pop(key)
                    break
        for key, val in want.items():          # 段内缺的键补上
            blk.append(f"  {key}: {quoted(val)}")
        lines[start:end] = blk
    else:
        lines += ["", "# 本机自生成 CA（scripts/make_ca.py 写入；空值 = 用内置证书）",
                  "cert:",
                  f"  file: {quoted(cert_file)}",
                  f"  key: {quoted(cert_key)}",
                  f"  name: {quoted(cert_name)}"]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def clear_cert_config() -> bool:
    try:
        mcp_call("update_config", {"values": {"cert.file": "", "cert.key": ""}})
        say(OK, "已清空 cert.file / cert.key（回退到内置证书）")
        return True
    except Exception as e:
        say(FAIL, f"清空失败：{str(e)[:120]}")
        return False


# --------------------------------------------------------------------------- #
# 4. 校验
# --------------------------------------------------------------------------- #

def verify_proxy(ca_pem: Path, host: str = "https://www.baidu.com") -> bool:
    """通过下载器代理发一次 HTTPS，并把「我们的 CA」作为唯一信任根。

    能通 = 说明代理换了证书并且我们的 CA 被信任；返回的证书链也就是我们的。
    """
    proxy = urllib.request.ProxyHandler({"https": "http://127.0.0.1:2023",
                                        "http": "http://127.0.0.1:2023"})
    import ssl
    ctx = ssl.create_default_context(cafile=str(ca_pem))
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    req = urllib.request.Request(host, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.build_opener(proxy,
                                        urllib.request.HTTPSHandler(context=ctx)
                                        ).open(req, timeout=20) as r:
            say(OK, f"代理链路校验通过（经 127.0.0.1:2023 访问 {host} → {r.status}，"
                    "证书链 = 我们的 CA）")
            return True
    except Exception as e:
        say(FAIL, f"代理链路校验失败：{type(e).__name__} {str(e)[:160]}")
        print("      若报证书不匹配 → 说明下载器还没用我们的证书（重启下载器再试）")
        return False


def show_status() -> None:
    try:
        st = mcp_call("get_certificate_status")
    except Exception as e:
        say(WARN, f"读不到证书状态（下载器没在跑）：{str(e)[:80]}")
        return
    cur = st.get("detail") or {}
    print(f"      当前生效证书: {cur.get('subject_common_name')} "
          f"| 来源={st.get('source') or (st.get('detail') or {}).get('source')} "
          f"| 已装={cur.get('installed')} 受信={cur.get('trusted')}")
    for w in st.get("risk_warnings") or []:
        say(WARN, f"风险提示：{w}")


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="下载器根证书加固（本机自生成 CA）")
    ap.add_argument("--status", action="store_true", help="只看现状")
    ap.add_argument("--rollback", action="store_true", help="清空 cert.* 配置")
    ap.add_argument("--remove-legacy", action="store_true",
                    help="顺带删除旧 SunnyNet 证书（用户存储 + 机器存储）")
    ap.add_argument("--days", type=int, default=3650, help="CA 有效期天数")
    args = ap.parse_args()

    say(STEP, "读当前证书状态")
    show_status()
    if args.status:
        return 0

    dl = find_downloader_dir()
    if not dl:
        say(FAIL, "找不到下载器目录（tools/wx_channels_download*）")
        return 2
    certs = dl / "certs"
    pem, key, der = certs / "local-ca.pem", certs / "local-ca.key", certs / "local-ca.cer"

    if args.rollback:
        return 0 if clear_cert_config() else 2

    if not port_open():
        say(WARN, "下载器没在跑（连不上 127.0.0.1:2022）")
        print("      · 生成 / 导入 / 写配置仍会完成，只是暂不能校验代理链路；")
        print("      · 等你启动下载器后再跑一次本脚本（或加 --status）即可校验。")
        print("      · ⚠️ 若下载器启动后秒退，多半是它的 reg.exe 被安全策略拦了：")
        print("        安全中心 → 命令安全 → 程序黑名单，把 reg.exe 移除，或手动双击")
        print(f"        {dl / 'run_downloader.cmd'}")

    if pem.exists() and key.exists():
        say(OK, f"复用已有 CA：{pem}")
    else:
        say(STEP, f"生成本机专有 CA → {certs}")
        generate_ca(pem, key, days=args.days)
        say(OK, "CA 已生成（私钥仅存本机，未外发）")
    export_der(pem, der)

    say(STEP, "导入到当前用户根证书存储")
    install_ca(der)

    say(STEP, "写入下载器配置（cert.file / cert.key / cert.name）")
    if not apply_cert_config(pem, key, dl):
        return 2

    if port_open():
        say(STEP, "校验代理链路")
        verify_proxy(pem)
    else:
        say(WARN, "跳过代理链路校验（下载器没在跑）")

    if args.remove_legacy:
        say(STEP, "删除旧 SunnyNet 证书")
        for store, user in (("Root", True), ("Root", False)):
            scope = "当前用户" if user else "本机"
            if remove_store_cert(store, "SunnyNet", user=user):
                say(OK, f"已从{scope}存储删除 SunnyNet")
            else:
                say(WARN, f"{scope}存储里没找到 SunnyNet（或需要管理员权限）")
        say(STEP, "再读一次状态")
        show_status()

    print()
    say(STEP, "完成。回退方式：python scripts/make_ca.py --rollback")
    say(STEP, "注意：换证书后，之前信任过旧证书的程序可能又提示不安全，"
              "重启一次浏览器即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
