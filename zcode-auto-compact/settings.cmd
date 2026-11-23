@echo off
rem 双击直接打开「自动上下文压缩」设置弹窗（不依赖 ZCode 会话）
start "" pythonw "%~dp0hooks\settings_dialog.py"
