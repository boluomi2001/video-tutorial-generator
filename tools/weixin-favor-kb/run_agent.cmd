@echo off
rem ============================================================
rem  Agent 写笔记模式（默认「纯本地」：零 API 调用、零成本）
rem
rem  流水线只产出干净素材：转录 + 关键帧 + 本地 OCR + 缩略图拼版
rem  不生成/不发布 Qwen 版笔记；也不调任何 LLM API
rem  视觉理解与写笔记全部交给 Agent：
rem    · 先看 frames\contact_sheet.jpg 判断本条属于哪类
rem    · 文稿/PPT/代码类 → 画面文字已在 ocr 里，无需看单帧
rem    · 实物/场景/3D/审美/操作类 → 按需读 frames\ 里的原帧
rem    · 纯口播类 → 只写转录
rem
rem  单条: run_agent.cmd "<链接或本地文件>"
rem  批量: run_agent.cmd --from-file links.txt
rem  续跑: run_agent.cmd --from-file links.txt --resume output\batch_<时间戳>
rem
rem  需要无人值守的超大批量时，加 --with-vision 保留付费视觉模型
rem  （硅基流动，约 ¥0.04/条）
rem
rem  跑完后 Agent 下一步：读 agent_input.json → 写笔记
rem                       → 上传 ima「大李老师」→ 回读校验 → 删本地
rem ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u auto_run.py --agent-write %*
endlocal
