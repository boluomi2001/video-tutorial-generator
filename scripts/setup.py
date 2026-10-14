#!/usr/bin/env python
"""视频解析工作流 —— 一键安装 / 配置脚本。

只用标准库，可在刚 Clone 下来、还没建 venv 的环境里直接跑。

用法（Agent 或用户都可）：
    python scripts/setup.py --api-key sk-xxxxxxxx
    python scripts/setup.py --api-key sk-xxxxxxxx --pip-mirror tsinghua
    python scripts/setup.py --api-key sk-xxxxxxxx --skip-venv     # 已有虚拟环境
    python scripts/setup.py --api-key sk-xxxxxxxx --fetch-ffmpeg  # 顺便装 ffmpeg

做的事：
    1. 检查 Python 版本（3.10~3.12 最佳，3.13 有依赖兼容风险）
    2. 解压 installer/ 里的视频下载器到 tools/wx_channels_download/
    3. 生成下载器 config.yaml（落盘目录指向本项目 downloads/，开启 MCP 端口 2022）
    4. 生成流水线 config.yaml（大模型 Key 现在**可选**——默认 Agent 模式零 Key 可用）
    5. 检查 / 安装 ffmpeg
    6. 创建 venv 并安装依赖
    7. 自动把 MCP 注册进 Agent 的配置（~/.workbuddy/mcp.json），并把小红书扫码登录串上
    8. 打印后续操作（视频号两条通道怎么选）

设计前提：**默认「Agent 写笔记」模式不调用任何大模型 API**，所以安装阶段不需要
任何 Key。只有要用传统 Qwen 自动生成模式时才需要填 LLM Key。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = REPO_ROOT / "tools" / "weixin-favor-kb"
DOWNLOADER = REPO_ROOT / "tools" / "wx_channels_download"
INSTALLER_DIR = REPO_ROOT / "installer"
DOWNLOADS = TOOL_ROOT / "downloads"
DEFAULT_PORT = 2022
MCP_NAME = "wx_channels_download"

OK, WARN, FAIL, STEP = "[OK]", "[WARN]", "[FAIL]", "[STEP]"


def say(tag: str, msg: str) -> None:
    print(f"{tag} {msg}", flush=True)


def venv_python() -> Path:
    venv = TOOL_ROOT / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


# ---------------------------------------------------------------- 1. Python
def check_python() -> None:
    major, minor = sys.version_info[:2]
    v = f"{major}.{minor}"
    if (major, minor) >= (3, 10) and (major, minor) < (3, 13):
        say(OK, f"Python {v}（推荐区间 3.10~3.12）")
    elif (major, minor) >= (3, 13):
        say(WARN, f"Python {v} 可能踩 3.13 依赖坑（tokenizers/rust 轮子缺失），"
                  "强烈建议改用 3.12")
    else:
        say(FAIL, f"Python {v} 过低，请安装 3.10~3.12")
        sys.exit(1)


# ------------------------------------------------------- 2. 解压视频下载器
def unzip_downloader() -> bool:
    if DOWNLOADER.exists() and list(DOWNLOADER.glob("*.exe")):
        say(OK, f"下载器已就位：{DOWNLOADER}")
        return True

    zips = sorted(INSTALLER_DIR.glob("*.zip")) if INSTALLER_DIR.exists() else []
    if not zips:
        say(WARN, f"installer/ 下没有 zip 安装包，跳过。可自行把 wx_video_download "
                  "的 Windows 发行包放进 installer/ 后重跑本脚本")
        return False

    DOWNLOADER.mkdir(parents=True, exist_ok=True)
    say(STEP, f"解压 {zips[0].name} → {DOWNLOADER}")
    with zipfile.ZipFile(zips[0]) as zf:
        zf.extractall(DOWNLOADER)
    say(OK, "下载器解压完成")
    return True


# ------------------------------------------------- 3. 下载器 config.yaml
def write_downloader_config() -> bool:
    tpl = DOWNLOADER / "config.example.yaml"
    if not tpl.exists():
        # 安装包里自带的 config.yaml 也能当模板，但含发行者默认值，优先用仓库模板
        alt = DOWNLOADER / "config.yaml"
        if alt.exists():
            tpl = alt
        else:
            say(WARN, "找不到下载器配置模板，跳过")
            return False

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    text = tpl.read_text(encoding="utf-8")
    text = text.replace("__DOWNLOADS_DIR__", str(DOWNLOADS).replace("\\", "/"))
    (DOWNLOADER / "config.yaml").write_text(text, encoding="utf-8")
    say(OK, f"已生成 {DOWNLOADER / 'config.yaml'}（下载目录 → downloads/，MCP 端口 {DEFAULT_PORT}）")
    return True


# ------------------------------------------------- 4. 流水线 config.yaml
def write_pipeline_config(api_key: str, base_url: str, model: str,
                          fast_model: str) -> bool:
    tpl = TOOL_ROOT / "config.example.yaml"
    if not tpl.exists():
        say(FAIL, f"缺少模板 {tpl}")
        return False

    text = tpl.read_text(encoding="utf-8")
    text = text.replace("sk-你的硅基流动APIKey",
                        api_key or "sk-可选_默认Agent模式不需要")
    text = text.replace("https://api.siliconflow.cn/v1", base_url)
    if model:
        text = text.replace("Qwen/Qwen3-VL-32B-Instruct", model)
    target = TOOL_ROOT / "config.yaml"
    target.write_text(text, encoding="utf-8")
    say(OK, f"已生成 {target}")

    if api_key:
        say(OK, "已填入 LLM API Key（传统 Qwen 模式可用）")
    else:
        say(OK, "未填 LLM API Key —— 没关系：默认「Agent 写笔记」模式"
                "完全不需要 Key，零成本；只有要用 --with-vision 传统模式时才需要补")
    return True


# ------------------------------------------------- 5.5 注册 MCP + 小红书登录
def mcp_config_paths() -> list[Path]:
    """可能的 Agent MCP 配置文件位置（存在哪个就写哪个，都没有则只打印）。"""
    home = Path.home()
    cands = [home / ".workbuddy" / "mcp.json"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            cands.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        cands.append(home / "Library" / "Application Support" / "Claude"
                     / "claude_desktop_config.json")
    return cands


def register_mcp(explicit: str, disable: bool) -> None:
    """把下载器 MCP 合并进 Agent 配置（**合并不覆盖**，保留其它 server）。"""
    if disable:
        say(WARN, "--no-mcp：跳过 MCP 注册，详见下面打印的配置片段")
        return

    entry = {"type": "streamable-http", "url": f"http://127.0.0.1:{DEFAULT_PORT}/mcp"}
    targets = [Path(explicit)] if explicit else [p for p in mcp_config_paths()
                                                 if p.parent.exists()]
    if not targets:
        say(WARN, "没找到 Agent 的 MCP 配置文件，请手动把下面片段加进去")
        print(MCP_SNIPPET)
        return

    for path in targets:
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) or {}
                path.with_suffix(path.suffix + ".bak").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                say(WARN, f"{path} 解析失败（{str(e)[:60]}），已跳过，未改动原文件")
                continue
        servers = data.setdefault("mcpServers", {})
        old = servers.get(MCP_NAME)
        servers[MCP_NAME] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        say(OK, f"MCP 已{'更新' if old else '写入'} {path}")
        print("       ⚠️ 需要重启 Agent，并在「连接器 / 自定义连接器」里对本 MCP "
              "点一次「信任」才会生效。")


def login_xhs_hint(run: bool) -> None:
    """小红书 Cookie：扫码优先，粘贴兜底。"""
    script = TOOL_ROOT / "scripts" / "login_xhs.py"
    if not script.exists():
        return
    if not run:
        say(STEP, "抓小红书需要登录 Cookie（一次性）。随时可跑：")
        print(f"       {TOOL_ROOT / 'login_xhs.cmd'}")
        print("       或 python scripts/login_xhs.py  （扫码；失败会自动转粘贴模式）")
        return

    py = venv_python()
    if not py.exists():
        py = Path(sys.executable)
    say(STEP, "启动小红书扫码登录（浏览器会弹窗，用手机小红书 App 扫一次）…")
    try:
        subprocess.run([str(py), "-u", str(script)], cwd=str(TOOL_ROOT))
    except Exception as e:
        say(WARN, f"扫码登录未完成（{str(e)[:80]}），之后可手动重跑 login_xhs.cmd")


# ------------------------------------------------------------- 6. ffmpeg
FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for d in (REPO_ROOT / "tools" / "ffmpeg", TOOL_ROOT / "tools" / "ffmpeg"):
        if d.exists():
            for pattern in ("**/bin/ffmpeg.exe", "**/bin/ffmpeg"):
                hits = sorted(d.glob(pattern))
                if hits:
                    return str(hits[0])
    return None


def ensure_ffmpeg(fetch: bool) -> None:
    found = find_ffmpeg()
    if found:
        say(OK, f"ffmpeg: {found}")
        return

    say(WARN, "未检测到 ffmpeg")
    if not fetch:
        say(WARN, "请手动安装：winget install Gyan.FFmpeg，或重跑本脚本加 --fetch-ffmpeg")
        return

    dest = REPO_ROOT / "tools" / "ffmpeg"
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "ffmpeg.zip"
    say(STEP, f"下载 ffmpeg（约 100MB）→ {archive}")
    urllib.request.urlretrieve(FFMPEG_ZIP, archive)
    say(STEP, "解压中…")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    archive.unlink(missing_ok=True)
    hit = find_ffmpeg()
    say(OK, f"ffmpeg 就绪：{hit}" if hit else WARN + " ffmpeg 解压后未找到二进制，请检查目录结构")


# --------------------------------------------------------- 6. venv + deps
def setup_venv(mirror: str, skip: bool) -> None:
    if skip:
        say(WARN, "--skip-venv：跳过虚拟环境创建，自行确保依赖已装")
        return

    venv = TOOL_ROOT / "venv"
    py = sys.executable
    if not (venv / ("Scripts" if os.name == "nt" else "bin")).exists():
        say(STEP, f"创建虚拟环境 {venv}")
        subprocess.run([py, "-m", "venv", str(venv)], check=True)
    else:
        say(OK, "虚拟环境已存在")

    venv_py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    req = TOOL_ROOT / "requirements.txt"

    say(STEP, "升级 pip")
    subprocess.run([str(venv_py), "-m", "pip", "install", "-q", "-U", "pip"], check=False)

    cmd = [str(venv_py), "-m", "pip", "install", "-r", str(req)]
    if mirror == "tsinghua":
        cmd += ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
        say(STEP, "安装依赖（清华源）…首次需要 3-10 分钟")
    else:
        say(STEP, "安装依赖…首次需要 3-10 分钟")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        say(FAIL, "依赖安装失败：若是 Python 3.13，请换 3.12 重试；或加 --pip-mirror tsinghua")
        sys.exit(1)
    say(OK, "依赖安装完成")


# -------------------------------------------------------------- 7. 收尾
MCP_SNIPPET = """{
  "mcpServers": {
    "wx_channels_download": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:%(port)d/mcp"
    }
  }
}""" % {"port": DEFAULT_PORT}


def print_next(launch: bool) -> None:
    print()
    print("=" * 62)
    print("安装完成。视频号有两条通道，按需选一条（也可以两条都不配）:")
    print()
    print("  [推荐] 免除登录通道 —— 部署云端解析 Worker（一次性，约 5 分钟）")
    print("     优点：不用打开微信、几秒拿直链、最稳")
    print("     需要：一个免费的 Cloudflare 账号（要注册，这步躲不掉）")
    print("     步骤：")
    print("       1) copy tools\\wx_channel\\config.example.yaml "
          "tools\\wx_channel\\config.yaml")
    print("       2) 填入 cloudflare 段的 accountid / apitoken / sphcookie")
    print("       3) cd tools\\wx_channel && "
          ".\\wx_channel.exe sph_deploy --config config.yaml")
    print("     部署成功后 sphhostname 自动回填，流水线自动优先走它。")
    print("     详见 README.md §3.6")
    print()
    print("  [零注册] 微信兜底通道 —— 不想注册 Cloudflare 就用这条")
    print("     需要：保持微信在跑，并让下载器连上视频号页面")
    print("     1) 启动下载器（Windows 双击即可）：")
    print(f"        {DOWNLOADER}")
    print("        首次允许其修改系统代理，否则抓取功能不可用。")
    print("     2) 打开微信并进入「视频号」页面")
    print("     3) 配了 Worker 走 Worker；没配就自动降级走这条")
    print()
    print("  其它平台无需任何 Cookie，直接丢链接即可：")
    print("     B站 / 抖音 / 快手 / 微博 / YouTube …")
    print("     小红书需要一次登录 Cookie（见上方提示，扫码即可）")
    print()
    print("  跑一条命令出笔记：")
    print(f"     {TOOL_ROOT / 'run_agent.cmd'} \"<链接或本地文件>\"")
    print()
    print("  想在 Agent（如 WorkBuddy / Claude）里直接调用这套能力，")
    print("  把 skill/video-analyzer/ 复制到你的技能目录；MCP 已自动注册，")
    print("  记得重启 Agent 并对该 MCP 点一次「信任」。")
    print("=" * 62)


def maybe_launch(launch: bool) -> None:
    if not launch:
        return
    exes = [p for p in sorted(DOWNLOADER.glob("*.exe"))
            if "unins" not in p.name.lower()]
    if not exes:
        return
    say(STEP, f"静默拉起下载器：{exes[0].name}")
    kwargs = {}
    if hasattr(subprocess, "DETACHED_PROCESS"):
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(exes[0])], cwd=str(DOWNLOADER),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)


def main() -> int:
    ap = argparse.ArgumentParser(description="视频解析工作流一键安装")
    ap.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", ""),
                    help="大模型 API Key（硅基流动）。**可选**：默认 Agent 模式不需要")
    ap.add_argument("--base-url", default="https://api.siliconflow.cn/v1")
    ap.add_argument("--model", default="Qwen/Qwen3-VL-30B-A3B-Instruct",
                    help="视觉理解模型")
    ap.add_argument("--fast-model", default="Qwen/Qwen2.5-7B-Instruct",
                    help="分类/速览用的快模型")
    ap.add_argument("--pip-mirror", choices=["official", "tsinghua"],
                    default="tsinghua")
    ap.add_argument("--skip-venv", action="store_true")
    ap.add_argument("--fetch-ffmpeg", action="store_true")
    ap.add_argument("--launch", action="store_true", help="安装后拉起下载器")
    ap.add_argument("--login-xhs", action="store_true",
                    help="安装后立刻做小红书扫码登录（推荐，省得回头单独跑）")
    ap.add_argument("--mcp-config", default="",
                    help="指定要写入的 Agent MCP 配置文件（默认自动探测 "
                         "~/.workbuddy/mcp.json 等）")
    ap.add_argument("--no-mcp", action="store_true",
                    help="不自动注册 MCP，只打印配置片段")
    args = ap.parse_args()

    say(STEP, f"安装根目录：{REPO_ROOT}")
    check_python()
    unzip_downloader()
    write_downloader_config()
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    (TOOL_ROOT / "output").mkdir(parents=True, exist_ok=True)
    write_pipeline_config(args.api_key, args.base_url, args.model, args.fast_model)
    ensure_ffmpeg(args.fetch_ffmpeg)
    setup_venv(args.pip_mirror, args.skip_venv)
    register_mcp(args.mcp_config, args.no_mcp)
    login_xhs_hint(args.login_xhs)
    maybe_launch(args.launch)
    print_next(args.launch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
