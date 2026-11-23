---
description: 打开「自动上下文压缩」设置窗口，或直接设阈值（例：/compact-settings 60）
---
管理 zcode-auto-compact。插件根目录 = 本命令文件所在目录的上一级。Windows 用 `python`。

- 没给数字：`python "<插件根目录>/hooks/autocompact.py" settings`（分离启动设置窗口，立即返回），
  然后一句话告知用户：设置窗口已打开。上下文弹窗里也有滑块可直接调（若已注入）。
- 给了数字（10–95）：`python "<插件根目录>/hooks/autocompact.py" config --percent <数字>`，
  转述返回 JSON 的 `engine`（state/percent/error）与 `asar`（state/pending）字段即可；有 error 原样转述。
- 看状态：同上 `config`；还原引擎 `restore-engine`；卸掉弹窗滑块 `restore-asar`；强制更新 `self-update --force`。

用户附加要求：$ARGUMENTS
