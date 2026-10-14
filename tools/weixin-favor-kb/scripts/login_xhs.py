#!/usr/bin/env python
"""小红书 Cookie 获取助手（**可选工具**，一般用不到）—— 扫码优先，粘贴兜底。

先看清楚：**抓小红书笔记不需要登录，也不需要 Cookie。**

    2026-10-15 实测更正：只要链接里带 `xsec_token`（= 小红书 App 里
    「分享 → 复制链接」产出的那种分享链接），**匿名就能解析**图文/视频详情。
    裸链接 `/explore/<id>`、浏览器地址栏直接复制的链接拿不到详情 —— 那是缺 token，
    不是缺登录。所以**遇到解析失败请先让用户重新分享，而不是来跑这个脚本**。

本脚本只在下面这些少见情况才需要：
    · 笔记被设为私密 / 仅关注可见
    · 链接的 xsec_token 已失效，且无法重新分享

两种获取方式（脚本自动选）：
    1) 扫码（默认）：用系统自带的 Edge 打开小红书登录页，屏幕上出现二维码，
       你用小红书 App 扫一次 → 脚本自动检测登录完成并把 Cookie 写进 config.yaml。
       —— 不需要 F12，不需要复制粘贴。
    2) 粘贴（--paste）：扫码不可用（没装浏览器/无 GUI/扫码一直不过）时，
       按提示从浏览器开发者工具复制 Cookie 粘贴进来。

⚠️ 三个实测坑（改代码前先看）：
    · **未登录也会下发 `web_session`** → 不能用"有 web_session"判断登录成功；
      扫码模式改用"登录弹窗已消失"判定，功能校验交给 `--check-url`。
    · **headless 会被风控**（页面变「安全限制 IP存在风险 300012」）→ 必须 headless=False。
    · **写配置要保留注释**：只替换 `xhs:` 段里的 cookie 行，不要 load/dump 整个 yaml。

用法：
    python scripts/login_xhs.py                 # 扫码（推荐）
    python scripts/login_xhs.py --browser chrome
    python scripts/login_xhs.py --paste          # 交互粘贴
    python scripts/login_xhs.py --paste --cookie "web_session=...; a1=..."   # 非交互
    python scripts/login_xhs.py --validate-only  # 只校验现有 Cookie，不重新登录
    python scripts/login_xhs.py --check-url "<笔记链接>"   # 写入后顺带做功能校验

依赖 playwright（用 channel=msedge/chrome 复用系统浏览器，**不额外下载 Chromium**）：
    缺包时脚本会提示，并自动降级到粘贴模式，不会直接崩。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]      # tools/weixin-favor-kb
CONFIG = TOOL_ROOT / "config.yaml"
CONFIG_TPL = TOOL_ROOT / "config.example.yaml"
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

REQUIRED_KEYS = ("web_session", "a1", "webId")
LOGIN_URL = "https://www.xiaohongshu.com/login"

# ⚠️ 实测（2026-10-14）两个坑，别改回去：
#   1) **未登录也会下发 `web_session`**（38 位、同样以 030037ad 开头），
#      所以「有 web_session 就算登录」是错的，会导致误判成功；
#   2) **headless 会被风控**：页面直接变成「安全限制 IP存在风险…300012」，
#      必须 headless=False 才拿得到真正的登录页。
# 因此登录成功改用「登录弹窗已消失」这个 UI 事实来判定（见 PROBE_JS）。
PROBE_JS = """() => {
  const txt = (document.body.innerText || '').replace(/\\s+/g, ' ');
  return {
    modal:  /我已阅读并同意|小红书如何扫码|扫码登录/.test(txt),
    qr:     document.querySelectorAll('canvas, [class*=qrcode]').length,
    lsUser: localStorage.getItem('userId') || '',
    url:    location.href,
  };
}"""

OK, WARN, FAIL, STEP = "[OK]", "[WARN]", "[FAIL]", "[STEP]"


def say(tag: str, msg: str) -> None:
    print(f"{tag} {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Cookie 串处理
# --------------------------------------------------------------------------- #

def cookie_to_str(cookies: list[dict]) -> str:
    """Playwright 的 cookie 列表 → 请求头里那种 `k=v; k=v` 形态。"""
    seen: dict[str, str] = {}
    for c in cookies:
        name = str(c.get("name") or "").strip()
        if name and name not in seen:
            seen[name] = str(c.get("value") or "")
    return "; ".join(f"{k}={v}" for k, v in seen.items())


def parse_cookie_str(raw: str) -> dict[str, str]:
    """解析任意形态的粘贴内容。

    容忍：带 `Cookie: ` 前缀、多行、用换行/分号/`&` 分隔、值里带 `=`。
    """
    s = (raw or "").strip()
    s = re.sub(r"(?i)^\s*cookie\s*:\s*", "", s)
    s = s.replace("\r", "\n").replace("\n", ";")
    out: dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k and k not in out:
            out[k] = v.strip()
    return out


def check_cookie(cookie: str) -> tuple[bool, list[str]]:
    """**结构性**校验：关键键是否齐全。

    ⚠️ 注意这只是"必要条件"，不是充分条件——小红书**未登录也会下发
    `web_session`**（实测），所以键齐不代表已登录。想要确定能用，
    请用 `--check-url <笔记链接>` 做一次真实解析（功能校验）。
    """
    kv = parse_cookie_str(cookie)
    missing = [k for k in REQUIRED_KEYS if not kv.get(k)]
    return (not missing), missing


# --------------------------------------------------------------------------- #
# 写回 config.yaml（就地改，保留注释）
# --------------------------------------------------------------------------- #

def upsert_cookie(text: str, cookie: str) -> str:
    """把 Cookie 写进 yaml 文本的 `xhs.cookie`，保留其余内容与注释。

    不做 yaml load/dump —— 那会把用户配置里的注释全抹掉。
    改为定位 `xhs:` 段（到下一个顶格行为止），只动段内的 `cookie:` 行。
    """
    val = '"%s"' % cookie.replace("\\", "\\\\").replace('"', '\\"')
    lines = text.splitlines()

    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"^xhs:\s*$", ln)), None)
    if start is None:
        # 没有 xhs 段 → 追加一份
        if text and not text.endswith("\n"):
            text += "\n"
        return text + (
            "\n# 小红书：网页端笔记详情需登录态；由 scripts/login_xhs.py 自动写入\n"
            f"xhs:\n  cookie: {val}\n"
        )

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j][:1].isspace():
            end = j
            break

    blk = lines[start:end]
    for k, ln in enumerate(blk):
        if re.match(r"\s*cookie\s*:", ln):
            blk[k] = f"  cookie: {val}"
            break
    else:
        blk.append(f"  cookie: {val}")
    lines[start:end] = blk
    return "\n".join(lines) + "\n"


def write_cookie(cookie: str) -> Path:
    if not CONFIG.exists():
        if CONFIG_TPL.exists():
            CONFIG.write_text(CONFIG_TPL.read_text(encoding="utf-8"),
                              encoding="utf-8")
            say(WARN, f"config.yaml 不存在，已从模板生成：{CONFIG}")
        else:
            raise SystemExit(f"{FAIL} 找不到 {CONFIG}，也没有模板可复制")
    text = CONFIG.read_text(encoding="utf-8")
    CONFIG.write_text(upsert_cookie(text, cookie), encoding="utf-8")
    return CONFIG


# --------------------------------------------------------------------------- #
# 扫码登录
# --------------------------------------------------------------------------- #

def import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        return None


def scan_login(browser: str, timeout: int) -> str:
    """打开浏览器让用户扫码，登录成功后返回 cookie 串。失败/超时返回空串。"""
    sync_playwright = import_playwright()
    if sync_playwright is None:
        say(WARN, "未安装 playwright，无法扫码。安装：")
        print("        <venv>/Scripts/pip install playwright")
        print("      或用粘贴模式：python scripts/login_xhs.py --paste")
        return ""

    say(STEP, f"启动 {browser}（第一次启动可能要几秒）…")
    try:
        pw = sync_playwright().start()
        browser_obj = pw.chromium.launch(
            channel=browser, headless=False,      # ⚠️ 不能 headless，会被风控
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
    except Exception as e:
        say(FAIL, f"启动浏览器失败：{type(e).__name__} {str(e)[:120]}")
        print("      可能原因：没装该浏览器 / 无图形界面 / 被安全软件拦截。")
        print("      换一个：--browser chrome，或改用粘贴模式 --paste")
        return ""

    ctx = browser_obj.new_context(locale="zh-CN", viewport=None)
    page = ctx.new_page()
    try:
        page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
        page.bring_to_front()
    except Exception as e:
        say(WARN, f"打开登录页失败（{str(e)[:80]}），可在窗口里手动打开")

    print()
    print("=" * 62)
    print("  请在弹出的浏览器窗口里完成登录：")
    print("    1) 窗口里已经打开小红书登录页，中央就是二维码")
    print("    2) 用手机小红书 App 扫码（也可改用微信/手机号登录）")
    print(f"  脚本会自动检测登录结果（最多等 {timeout} 秒）")
    print("=" * 62)
    print()

    cookie = ""
    deadline = time.time() + timeout
    last_tip = 0
    while time.time() < deadline:
        if page.is_closed():
            break
        # ① 登录弹窗是否已消失（匿名时必然存在）
        modal = True
        try:
            sig = page.evaluate(PROBE_JS) or {}
            modal = bool(sig.get("modal")) or bool(sig.get("qr"))
        except Exception:
            pass
        # ② web_session 存在（必要条件，非充分）
        try:
            kv = {c.get("name"): c.get("value") for c in ctx.cookies()}
        except Exception:
            kv = {}
        has_session = bool(kv.get("web_session"))

        if has_session and not modal:
            time.sleep(2.0)                    # 等其余 cookie 落齐
            cookie = cookie_to_str(ctx.cookies())
            say(OK, "检测到登录态（登录弹窗已消失）")
            break

        waited = int(time.time() - (deadline - timeout))
        if waited - last_tip >= 20:
            last_tip = waited
            print(f"      …仍在等待扫码（已等 {waited}s）", flush=True)
        time.sleep(2)

    try:
        browser_obj.close()
        pw.stop()
    except Exception:
        pass

    if not cookie:
        say(WARN, f"等待超时（{timeout}s），没有检测到登录完成")
    return cookie


# --------------------------------------------------------------------------- #
# 粘贴
# --------------------------------------------------------------------------- #

PASTE_HOWTO = f"""      获取方式（约 30 秒）：
        1) 浏览器打开并登录 xiaohongshu.com
        2) 按 F12 → 顶部「网络 / Network」标签
        3) 刷新页面，点任意一条 xiaohongshu.com 请求
        4) 右侧「标头 / Headers」→ 往下找「请求标头 / Request Headers」
        5) 复制「Cookie:」后面那一整串"""


def paste_login(given: str) -> str:
    if given:
        return given.strip()
    print()
    say(STEP, "粘贴模式：请在下面粘贴完整 Cookie")
    print(PASTE_HOWTO)
    print()
    try:
        raw = input("Cookie> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return raw


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

def validate(cookie: str, check_url: str = "") -> bool:
    ok, missing = check_cookie(cookie)
    if not ok:
        say(FAIL, f"Cookie 缺少关键项：{', '.join(missing)}")
        return False
    kv = parse_cookie_str(cookie)
    say(OK, f"结构校验通过（{len(kv)} 个键，web_session 长度 "
            f"{len(kv.get('web_session', ''))}）")

    if not check_url:
        say(WARN, "结构校验 ≠ 一定能用（未登录也会下发 web_session）。"
                  "要确定可用，请加 --check-url <一条小红书笔记链接>")
        return True
    say(STEP, f"功能校验：真实解析一次 → {check_url[:60]}")
    try:
        from modules import xhs_note
        note = xhs_note.fetch(check_url, cookie=cookie)
        say(OK, f"解析成功：{note['kind']}「{note['title'][:30]}」@ {note['author']}")
        return True
    except Exception as e:
        say(FAIL, f"解析失败：{type(e).__name__} {str(e)[:140]}")
        print("      Cookie 多半已失效（过期/换设备），重跑本脚本扫码即可。")
        return False


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="小红书 Cookie 获取助手")
    ap.add_argument("--paste", action="store_true",
                    help="直接走粘贴模式（不启动浏览器）")
    ap.add_argument("--cookie", default="",
                    help="配合 --paste：直接传入 Cookie 串（适合 Agent 非交互调用）")
    ap.add_argument("--browser", default="msedge", choices=["msedge", "chrome"],
                    help="扫码用的系统浏览器（默认 msedge）")
    ap.add_argument("--timeout", type=int, default=300,
                    help="扫码等待秒数，默认 300")
    ap.add_argument("--check-url", default="",
                    help="写入后用一条真实笔记链接做端到端校验")
    ap.add_argument("--validate-only", action="store_true",
                    help="只校验 config.yaml 里现有的 Cookie，不重新登录")
    args = ap.parse_args()

    if args.validate_only:
        from modules import xhs_note
        cur = xhs_note.cookie_from_config()
        if not cur:
            say(FAIL, "config.yaml 里 xhs.cookie 为空")
            return 2
        return 0 if validate(cur, args.check_url) else 2

    cookie = paste_login(args.cookie) if args.paste else scan_login(
        args.browser, args.timeout)

    if not cookie:
        if not args.paste:
            say(WARN, "扫码没拿到 Cookie，切换到粘贴模式")
            cookie = paste_login("")
    if not cookie:
        say(FAIL, "未获得 Cookie，配置未改动")
        return 2

    ok, missing = check_cookie(cookie)
    if not ok:
        say(WARN, f"Cookie 缺少：{', '.join(missing)}")
        print("      （仍会写入，但多半不可用；确认是在**已登录**状态下复制的）")

    path = write_cookie(cookie)
    kv = parse_cookie_str(cookie)
    say(OK, f"已写入 {path}（xhs.cookie，{len(kv)} 个键）")
    validate(cookie, args.check_url)
    print()
    say(STEP, "之后直接跑流水线即可：run_agent.cmd \"<小红书链接>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
