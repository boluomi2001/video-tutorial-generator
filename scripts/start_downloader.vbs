' 视频下载器静默启动 / 开机自启辅助脚本
'
' 用法：
'   1) 直接双击：进程没在跑就启动（窗口一闪而过属正常）
'   2) 开机自启：把快捷方式放进 shell:startup
'      Win+R → shell:startup → 把本文件（或它的快捷方式）拖进去
'
' 逻辑：进程名不存在才启动；已在运行则直接退出。

Option Explicit

Dim exePath, exeName, shell, fso
exeName = "wx_video_download.exe"

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 本脚本位于 <项目根>\scripts\，下载器位于 <项目根>\tools\wx_channels_download\
exePath = fso.GetParentFolderName(WScript.ScriptFullName) & "\..\tools\wx_channels_download\" & exeName
exePath = fso.GetAbsolutePathName(exePath)

If Not fso.FileExists(exePath) Then
    WScript.Echo "找不到下载器：" & exePath & vbCrLf & "请先运行 scripts\setup.cmd 安装。"
    WScript.Quit 1
End If

If Not IsProcessRunning(exeName) Then
    shell.CurrentDirectory = fso.GetParentFolderName(exePath)
    shell.Run """" & exePath & """", 0, False   ' 0=隐藏窗口，False=不等待
End If

Function IsProcessRunning(name)
    Dim out, lines, i
    IsProcessRunning = False
    Set shell = CreateObject("WScript.Shell")
    out = shell.Exec("tasklist /FI ""IMAGENAME eq " & name & """").StdOut.ReadAll
    lines = Split(LCase(out), vbCrLf)
    For i = 0 To UBound(lines)
        If InStr(lines(i), LCase(name)) > 0 Then
            IsProcessRunning = True
            Exit Function
        End If
    Next
End Function
