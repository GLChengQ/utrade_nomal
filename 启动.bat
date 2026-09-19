@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   启动 Binance 合约量化交易系统
echo   (会打开两个窗口：交易机器人 + 可视化面板)
echo ============================================
echo.

start "交易机器人(bot)" cmd /k ".\.venv\Scripts\python.exe bot.py"
start "可视化面板(dashboard)" cmd /k ".\.venv\Scripts\python.exe dashboard\server.py"

echo 已启动。两个窗口不要关闭，关闭即停止。
echo 面板手机访问： http://你的Tailscale IP:8080
echo.
pause
