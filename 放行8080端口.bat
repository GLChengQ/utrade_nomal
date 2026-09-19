@echo off
chcp 65001 >nul
echo ============================================
echo   放行 8080 端口（让手机能访问交易面板）
echo   请右键本文件 -> 以管理员身份运行
echo ============================================
echo.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 当前不是管理员权限！
    echo 请关闭，右键本文件，选择「以管理员身份运行」。
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="Binance Dashboard 8080" >nul 2>&1
netsh advfirewall firewall add rule name="Binance Dashboard 8080" dir=in action=allow protocol=TCP localport=8080

if %errorlevel% equ 0 (
    echo.
    echo [成功] 8080 端口已放行。
    echo 手机通过 Tailscale 访问： http://100.70.117.115:8080
) else (
    echo [失败] 请截图给我看错误信息。
)
echo.
pause
