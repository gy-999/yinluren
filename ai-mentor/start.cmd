@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   引路人 . AI 导师
echo ============================================================
echo.
echo   启动后请在浏览器打开： http://127.0.0.1:8848
echo   停止服务：在本窗口按 Ctrl + C
echo.

where python >nul 2>nul
if %errorlevel%==0 (
  python server.py %*
  goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py %*
  goto :end
)

echo [错误] 没有找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH。
pause

:end
pause
