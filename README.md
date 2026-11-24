# zcode-auto-compact · ZCode 自动上下文压缩

让 ZCode 自带的上下文自动压缩**按你设定的百分比提前触发**，并直接在**上下文容量弹窗里调滑块**——拖完即时生效，全部会话生效，不经过模型。

<!-- TODO: 补一张最终版弹窗截图到 docs/ 并取消注释：<p><img src="docs/popup.png" width="360"/></p> -->

```
自动压缩阈值 = 模型上下文窗口 × 百分比（默认 70%），且不会晚于 ZCode 原生阈值
```

例如 1M 窗口设 35%：用量到约 350K tokens 时 ZCode 自动压缩；原生默认要到 ~90% 以上才压。

## 功能一览

| 能力 | 说明 |
| --- | --- |
| 弹窗内滑块 | 悬停输入框旁的上下文用量指示器（圆环/百分比）→ 弹窗顶部第一行「自动压缩阈值」；拖动停顿 250ms 即写入 |
| 即时生效 | 运行中的引擎**每次判定压缩时实时重读配置**——改完当前会话与所有会话立即按新阈值，无需重启 |
| 设置窗口 | `/compact-settings` 打开；`/compact-settings 60` 直接设；双击插件目录 `settings.cmd` 秒开（不经过模型） |
| 自动更新 | 经 ZCode 官方 CLI（`plugins marketplace update` + `plugins update`）：新会话检查、每小时限频、安装前语法预检、坏版本拒装 |
| 自修复 | ZCode 升级覆盖引擎/asar 后，下个会话自动重打补丁；目标代码形状不符则拒绝改动并在设置里显示状态 |
| 零打扰 | 不往模型上下文注入任何文字；无状态条、无监控常驻（仅换文件时短暂存在后台 waiter） |

## 实现与安全

| 改动 | 范围 | 还原 |
| --- | --- | --- |
| 引擎 `resources/glm/zcode.cjs` | 仅 `getAutoCompactThreshold` 一个函数（约 82 → 740 字节）；阈值取 `min(原生阈值, 窗口×百分比)`，窗口用 ZCode 自己解析的模型设置 | `restore-engine`：按原文形状精确回退 + 目标函数及紧邻上下文的 SHA256 校验（1.0.2 起，不受同文件里其它补丁影响） |
| `app.asar` | 主入口 `out/main/index.js` **尾部一行** dynamic import（141 字节），其余 31963 个文件逐字节不变，入口经语法检查后才写入 | `restore-asar`：剥离注入行后逐字节还原 |
| 滑块 UI | 主进程 loader 只做三件事：挂滑块、经主进程读写配置、记日志；全部 try/catch，任何异常不影响 ZCode | 删除 `~/.zcode/zcode-auto-compact/inject-renderer.js`，重启后消失 |

设置存于 `~/.zcode/zcode-auto-compact/config.json`（升级插件不丢）；带防抖与跨进程锁。

## 安装

**前提**：ZCode 桌面版 + Python 3.8+（Windows 官方安装包即含 tkinter；`python3` / `python` / `py -3` 任一可用）。

**方式 A · Git 仓库（推荐，支持自动更新）**

1. ZCode → 插件市场 → 添加 → **添加插件市场**，填以下任一地址 → 添加
   - `1473166814/zcode-auto-compact`（走 HTTPS）
   - `git@github.com:1473166814/zcode-auto-compact.git`（**HTTPS 被网络重置时用这个**，需本机有可用的 GitHub SSH key）
2. 个人 → 对应市场 → 「自动上下文压缩」→ 安装
3. **完全退出并重开 ZCode**（托盘退出，关窗不算；首次必须，之后改阈值免重启）
4. 悬停输入框旁的上下文指示器 → 弹窗顶部即滑块

**方式 B · zip（内网/离线）**：从 [dist/](dist/) 下载 zip 解压到**固定目录**，把该目录作为本地插件市场添加，其余同上。

**发给别人**：把 `1473166814/zcode-auto-compact` 发给对方，对方在 插件市场 → 添加 → 添加插件市场 里粘贴即可（公开仓库，不需要给任何 key）；对方网络到不了 GitHub 时，把 `dist/zcode-auto-compact-v1.0.4.zip` 发过去，解压到固定目录，把该目录作为本地插件市场添加。对方需要 Python 3.8+。

## 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `autoCompactPercent` | `70` | 达到模型上下文窗口的该百分比时自动压缩（10–95） |
| `patchEnabled` | `true` | 引擎补丁开关（`restore-engine` 会置 `false`） |
| `asarInject` | `true` | 弹窗滑块注入开关（`restore-asar` 会置 `false`） |
| `autoUpdate` | `true` | 自动更新开关 |
| `autoUpdateIntervalMinutes` | `60` | 两次自动检查的最小间隔 |

上下文窗口大小自动取自 ZCode 的模型设置（个人规则 > 内置预设 > 宿主默认 200K），与宿主同规则解析。

## 命令速查

```bash
python hooks/autocompact.py config [--percent N] [--patch on|off] [--auto-update on|off] [--reset]
python hooks/autocompact.py inject          # 确保弹窗滑块注入在位（幂等）
python hooks/autocompact.py restore-engine  # 引擎函数还原为原文
python hooks/autocompact.py restore-asar    # 剥离 app.asar 注入行
python hooks/autocompact.py self-update --force   # 立即检查并安装更新
```

## 常见问题

- **为什么首次要完全重启？** 引擎与 app.asar 只在进程启动时加载；首次打补丁/注入后需重启一次。之后**改阈值全程热生效**（引擎每次判定重读配置）。
- **滑动后多久生效？** 停顿 250ms 落盘，下一次模型请求的压缩判定即按新值；所有窗口的滑块即时同步。
- **支持哪些模型？** 窗口按 ZCode 模型设置解析（如 GLM 1M、DeepSeek 1M）；百分比相对当前会话模型的窗口计算。
- **ZCode 升级后会失效吗？** 引擎/asar 会被换新，下个会话自动重打；若新版代码形状改变则拒绝改动（fail-safe），设置窗口会显示状态。
- **ZCode 4.0.0 上引擎补丁没打上？** 需要 1.0.1 及以上。4.0.0 的引擎里压缩后的名字会出现 `$` 开头（如 `$ue`），1.0.0 的形状匹配不认 `$`，把它判成「形状不符」而拒绝改动（状态显示 `foreign`）。1.0.1 起标识符按 `[\w$]+` 匹配，在 3.19.1 上的行为不变。
- **补丁显示已打上却不生效？** 看数据目录里的 `engine-heartbeat.json` 在新会话里有没有更新（补丁后的函数首次运行时写出）。另外 4.0.0 的桌面端代码里有 `ZCODE_DESKTOP_AGENT_BYTECODE=1` 开关，开启后会话改跑字节码版引擎，源码补丁不生效；当前版本默认不开。
- **和 zcode-adb-patch 一起用要注意什么？** 两者是独立插件，改同一个引擎文件的不同位置，可共存，先后顺序无关（本插件 1.0.2 起）：zcode-adb-patch 替换引擎前后会确认文件有没有被别的进程改过，两个钩子同时运行也不会互相覆盖；本插件的 `restore-engine` 只校验目标函数及紧邻上下文，不被别处的改动误伤。若 adb 补丁是用 `re-patch-adb.sh` 脚本打的，先跑脚本再开新会话（脚本要存下该版本的原版）。本插件低于 1.0.2 时，先让它打、后打 adb 补丁会让 `restore-engine` 因整文件哈希不一致而拒绝写入。
- **钩子以崩溃码退出（3221225477，日志里有 `Fatal Python error: _enter_buffered_busy`）？** 旧版在宿主没有关闭钩子标准输入的情况下，会在工作做完后的退出阶段崩溃（不影响补丁结果）。ZCode 正常会关闭，所以一般遇不到；1.0.2 起改为直接退出。
- **会影响远程 SSH 会话吗？** 不会——本插件只改本机 ZCode 安装目录内的文件；远端主机不受影响。

## 卸载

`restore-engine` + `restore-asar`（或设置窗口「还原引擎」）→ ZCode 设置 → 插件 → 移除本插件 → 删除 `~/.zcode/zcode-auto-compact/`。

## 兼容性

已在 **Windows 11 + ZCode 3.19.1** 完整验证。**ZCode 4.0.0**：引擎补丁已验证（需 1.0.1）；弹窗滑块已实测显示（1.0.2 起）。1.0.3 修好标题字号——4.0.0 的原生标题行不再带 `justify-between`，旧逻辑会误克隆下面的小字行，现在按「上下文容量」那个 span 的类名重建（实测 `14px/500` 与原生一致）；1.0.4 让滑块与原生进度条统一观感：高度、圆角、轨道色、填充色都取自原生 `progress` 元素（实测 `8px/6px`、填充 `rgb(11,127,255)`），只保留一个 12px 的滑钮。macOS/Linux 上引擎补丁与 asar 注入逻辑通用（文件替换无需等待，POSIX 语义），未做系统级验证。补丁按代码形状匹配，版本不符时拒绝改动而非乱改。

## 目录结构

```
marketplace.json            插件市场清单（ZCode 添加市场用）
zcode-auto-compact/       插件本体
  hooks/autocompact.py        主逻辑：引擎补丁/配置/自更新/CLI
  hooks/asar_inject.py        app.asar 注入器（重打包/校验/待收尾）
  hooks/inject-acc.cjs        主进程 loader（配置读写 + 注入渲染层）
  hooks/inject-renderer.js    弹窗滑块（版本守卫热更新 + 诊断探针）
  hooks/settings_dialog.py    Tk 设置窗口
  commands/compact-settings.md
dist/                       分发 zip
```

## License

MIT（见 [LICENSE](LICENSE)）
