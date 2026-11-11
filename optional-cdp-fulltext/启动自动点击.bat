@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在启动 Cloudflare 自动点击器...
echo 窗口可以最小化，但不要关闭；Ctrl+C 停止。
".venv\Scripts\python.exe" "optional-cdp-fulltext\cf_click_watcher.py"
pause
