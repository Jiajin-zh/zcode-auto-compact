# zcode-auto-compact · 自动上下文压缩

让 ZCode **自带的自动压缩**按你设定的百分比触发，并且**在上下文弹窗里直接调**——拖完滑块即时生效，全部会话生效，不经过模型。

```
自动压缩阈值 = 模型上下文窗口 × 百分比（默认 70%），且不会晚于 ZCode 原生阈值
```

## 设置入口（三选一）

| 入口 | 说明 |
| --- | --- |
| **上下文弹窗里的滑块** | 打开输入框旁的上下文容量弹窗，顶部即「自动压缩阈值」滑块；拖动停顿即写入，**即时生效**（运行中的引擎每次判定都会重读配置） |
| `/compact-settings` | 新会话里输入；带数字如 `/compact-settings 60` 直接设 |
| 双击 `settings.cmd` | 不开会话也能调；Tk 设置窗口立即打开 |

## 组成（两处改动，都可一键还原）

1. **引擎补丁**：`resources/glm/zcode.cjs` 里 `getAutoCompactThreshold` 一个函数（82 → 约 740 字节，其余字节不动）。阈值取 `min(原生阈值, 窗口 × 百分比 / 100)`，每次调用重读 `~/.zcode/zcode-auto-compact/config.json`——这就是热生效与全局生效的来源。
2. **弹窗滑块注入**：`app.asar` 主入口**尾部追加一行** dynamic import（141 字节，其余 31963 个文件逐字节不变），加载 `~/.zcode/zcode-auto-compact/inject-acc.cjs`。它只做三件事：把滑块脚本注入窗口、经主进程读写配置、记日志。滑块出现在上下文容量弹窗（`!w-80` 卡片）顶部。

两处改动都有：原样形状校验（不符即拒绝改动，不乱碰）、完整备份、哈希校验的精确还原、ZCode 升级后 SessionStart 自动重打。app.asar 在运行中被锁定时走「待收尾」：后台 waiter 等你完全退出 ZCode 后自动换入（重开生效）。

## 命令

```
python hooks/autocompact.py config [--percent N] [--patch on|off] [--auto-update on|off] [--reset]
python hooks/autocompact.py inject          # 确保弹窗滑块注入在位（幂等）
python hooks/autocompact.py restore-engine  # 引擎函数还原为原文
python hooks/autocompact.py restore-asar    # 剥离 app.asar 注入行
python hooks/autocompact.py self-update --force
```

## 配置（`~/.zcode/zcode-auto-compact/config.json`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `autoCompactPercent` | `70` | 用量达到模型上下文窗口的该百分比时自动压缩（10–95） |
| `patchEnabled` | `true` | 引擎补丁开关（`restore-engine` 置 `false`） |
| `asarInject` | `true` | 弹窗滑块注入开关（`restore-asar` 置 `false`） |
| `autoUpdate` | `true` | 新会话启动时自动检查并安装插件更新（经官方 CLI） |

## 生效时机

- **第一次**打补丁/注入：需要完全退出并重开 ZCode（只关窗口不算，从托盘退出）。
- **之后改阈值**：弹窗滑块或命令改完即热生效（运行中引擎按调用重读），全部会话通用。
- **ZCode 升级后**：引擎与 asar 会被换新。下个会话启动时插件自动重打两处；若新版代码形状变了则拒绝乱改，设置窗口里会显示状态。

## 数据目录

`~/.zcode/zcode-auto-compact/`：`config.json`（设置）、`engine-patch.json` / `engine-heartbeat.json`（引擎补丁记录与心跳）、`engine-backup/`、`inject-acc.cjs` / `inject-renderer.js`（注入脚本，删除 `inject-renderer.js` = 滑块消失的应急开关）、`inject-log.jsonl`（注入侧日志：boot / attach / set）、`asar-pending.json`（待收尾替换）、`update.json`、`last-run.json`。

卸载：先 `restore-engine` + `restore-asar`（或弹窗「还原引擎」），再在 ZCode 设置 → 插件里移除本插件，最后删除数据目录 `~/.zcode/zcode-auto-compact/`。

## 分发给他人

**前提**（接收方）：ZCode 桌面版 + Python 3.8+（Windows 用官方安装包即含 tkinter；`python3` / `python` / `py -3` 任一可用）。已在 Windows 11 + ZCode 3.19.1 验证；4.0.0 需 1.0.1 及以上（4.0.0 的引擎里出现了 `$` 开头的压缩名字，1.0.0 会把它判成形状不符而拒绝改动），4.0.0 上的引擎补丁已验证。其它 ZCode 版本若目标代码形状不符，插件会拒绝改动并在设置里显示状态，不会乱改。

**方式 A：zip 包（本地市场）**

1. 把 `zcode-auto-compact-vX.Y.Z.zip`（内含 `marketplace.json` + `zcode-auto-compact/`）发给对方
2. 对方解压到任意固定目录（解压后目录里直接就是这两个条目）
3. ZCode → 插件市场 → 添加 → 添加插件市场 → 选择该目录 → 添加
4. 个人 → 对应市场 → 自动上下文压缩 → 安装
5. **完全退出并重开 ZCode**（首次需重启加载引擎补丁与弹窗滑块；托盘退出，关窗不算）
6. 悬停输入框旁的上下文用量指示器 → 弹窗顶部即阈值滑块

**方式 B：GitHub（支持自动更新）**

把 `marketplace.json` + `zcode-auto-compact/` 放进仓库根目录，对方在插件市场用 **Git URL** 添加该仓库后按上面 4–6 步安装。之后你更新仓库，对方的新会话会自动检查并安装新版（每个会话最多查一次、每小时限频，可关）。

**务必告知接收方**：本插件会给 ZCode 的引擎文件（一个函数）和 `app.asar`（入口尾部一行）打**可精确还原**的补丁；不满意可随时用 `restore-engine` / `restore-asar` 恢复原样，或直接卸载。
