#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-auto-compact：让 ZCode 自带的自动压缩按设定的百分比触发。

原理：ZCode 引擎（resources/glm/zcode.cjs）里的 getAutoCompactThreshold 决定「用量达到
多少时自动压缩上下文」。本工具只改这一个函数：阈值取
    min(原生阈值, 模型上下文窗口 × autoCompactPercent / 100)
并在每次调用时读取用户设置（读不到就用打补丁时写入的数值）。引擎其它字节不动，
可按原文精确还原。

用法：
    autocompact.py patch                  SessionStart：确保引擎补丁在位（ZCode 升级后自动重打）
    autocompact.py self-update [--force]  SessionStart：经宿主官方 CLI 检查并安装插件更新
    autocompact.py config [选项]          查看/修改设置（--percent 35 …），修改后同步补丁
    autocompact.py settings               打开设置弹窗
    autocompact.py restore-engine         把引擎函数还原为原文，并停止自动打补丁

原则：
- 除 getAutoCompactThreshold 这一个函数外不改引擎任何内容；补丁内的文件读写全部
  包在 try/catch 里，最坏情况是回到打补丁时写入的数值。
- hook 模式任何异常都静默吞掉并以 0 退出，诊断一律走 stderr。
- 唯一会改动宿主状态的另一处是 self-update，只走宿主自带的
  `zcode plugins marketplace update` / `zcode plugins update` 官方命令。

用户设置存放在 ~/.zcode/zcode-auto-compact/config.json（引擎补丁按这个固定路径读取）。

相关环境变量（均可选）：
- ZCODE_WINDOWS_APP_INSTALL_DIR   ZCode 安装目录（定位引擎与官方 CLI）
- ZCODE_AUTO_COMPACT_DATA        数据目录（缺省 ~/.zcode/zcode-auto-compact）
- ZCODE_AUTO_COMPACT_CONFIG      用户设置文件路径（覆盖默认位置）
- ZCODE_AUTO_COMPACT_ENGINE      引擎文件路径（覆盖默认位置，仅用于测试）
"""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

PLUGIN_NAME = "zcode-auto-compact"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
DATA_DIR = Path(os.environ.get("ZCODE_AUTO_COMPACT_DATA") or HOME / ".zcode" / PLUGIN_NAME)
PLUGINS_DIR = HOME / ".zcode" / "cli" / "plugins"
ZCODE_CJS = Path("resources") / "glm" / "zcode.cjs"

DEFAULTS = {
    "autoCompactPercent": 35,         # 用量达到模型上下文窗口的该百分比时自动压缩
    "patchEnabled": True,             # False：不打补丁（「还原引擎」会置为 False）
    "asarInject": True,               # False：不往 app.asar 注入弹窗滑块（「还原 asar」会置为 False）
    "autoUpdate": True,               # 新会话启动时自动检查并安装插件更新
    "autoUpdateIntervalMinutes": 60,  # 两次自动检查之间的最小间隔
    "zcodeExe": None,                 # 可选：手动指定 ZCode 可执行文件
    "zcodeCliEntry": None,            # 可选：手动指定 zcode.cjs 路径
}
PCT_MIN, PCT_MAX = 10, 95
CFG_REL = ".zcode/zcode-auto-compact/config.json"   # 引擎补丁按此固定路径读取设置
HB_REL = ".zcode/zcode-auto-compact/engine-heartbeat.json"   # 补丁首次运行时写出

# config 模式可修改的键：(参数名, 配置键, 类型)
CONFIG_OPTS = {
    "--percent": ("autoCompactPercent", int),
    "--patch": ("patchEnabled", bool),
    "--interval": ("autoUpdateIntervalMinutes", int),
    "--auto-update": ("autoUpdate", bool),
}

# 引擎原文（压缩后的名字随版本变化，所以用形状匹配并把名字捕获出来）：
#   3.19.1  function S7e(e={}){let t=K3n(e),n=Uue(e.bufferTokens)??KKo;return Math.max(0,t-n)}
#   4.0.0   function x7e(e={}){let t=t8n(e),n=$ue(e.bufferTokens)??fZo;return Math.max(0,t-n)}
# 4.0.0 起名字可能以 $ 开头（如 $ue），标识符要认 [\w$]+；只用 \w+ 会把 4.0.0 判成"形状不符"
FN_CLEAN = re.compile(
    rb"function (?P<f>[\w$]+)\((?P<e>[\w$]+)=\{\}\)\{let (?P<t>[\w$]+)=(?P<k>[\w$]+)\((?P=e)\),"
    rb"(?P<n>[\w$]+)=(?P<u>[\w$]+)\((?P=e)\.bufferTokens\)\?\?(?P<d>[\w$]+);return Math\.max\(0,(?P=t)-(?P=n)\)\}")
# 本插件打过补丁后的形状
FN_PATCHED = re.compile(
    rb"function (?P<f>[\w$]+)\((?P<e>[\w$]+)=\{\}\)\{let (?P<t>[\w$]+)=(?P<k>[\w$]+)\((?P=e)\),"
    rb"(?P<n>[\w$]+)=(?P<u>[\w$]+)\((?P=e)\.bufferTokens\)\?\?(?P<d>[\w$]+);\{let p=(?P<p>\d+),w="
    rb".*?Math\.floor\(w\*p/100\)\)\}\}", re.S)
# 宿主内置默认窗口常量（4.0.0：que=2e5,pZo=32e3,Uic=21e3,zic=.08,fZo=13e3；值 13e3 的须等于上面捕获的 d）
WIN_DEFAULT = re.compile(rb"(?:(?P<w>[\w$]+)=2e5,[\w$]+=32e3,[\w$]+=21e3,[\w$]+=\.08,(?P<b>[\w$]+)=13e3,)")
VERIFY_CONTEXT = 1024   # 还原校验时，目标函数两侧各带多少字节的上下文一起比对


def log_err(msg):
    print(msg, file=sys.stderr)


# ---------- 配置 ----------

def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def user_config_path():
    return Path(os.environ.get("ZCODE_AUTO_COMPACT_CONFIG") or DATA_DIR / "config.json")


def _num(v, lo, hi, dflt):
    try:
        return int(min(hi, max(lo, float(v))))
    except (TypeError, ValueError):
        return dflt


def _merge(cfg, raw):
    if isinstance(raw, dict):
        for k in DEFAULTS:
            if raw.get(k) is not None:
                cfg[k] = raw[k]
        if raw.get("autoCompactPercent") is None and raw.get("thresholdPercent") is not None:
            cfg["autoCompactPercent"] = raw["thresholdPercent"]   # 兼容旧版键名


def sanitize(cfg):
    out = dict(cfg)
    out["autoCompactPercent"] = _num(cfg.get("autoCompactPercent"), PCT_MIN, PCT_MAX,
                                     DEFAULTS["autoCompactPercent"])
    out["patchEnabled"] = cfg.get("patchEnabled", True) is not False
    out["asarInject"] = cfg.get("asarInject", True) is not False
    out["autoUpdate"] = cfg.get("autoUpdate", True) is not False
    out["autoUpdateIntervalMinutes"] = _num(cfg.get("autoUpdateIntervalMinutes"), 5, 1440,
                                            DEFAULTS["autoUpdateIntervalMinutes"])
    return out


def load_config():
    cfg = dict(DEFAULTS)
    _merge(cfg, _read_json(PLUGIN_ROOT / "config.json"))
    _merge(cfg, _read_json(user_config_path()))
    return sanitize(cfg)


def save_user_config(changes):
    """合并写入用户设置；值为 None 表示删除该键（恢复默认）。"""
    path = user_config_path()
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raw = {}
    for k, v in changes.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    raw.pop("thresholdPercent", None)   # 旧版键名，已并入 autoCompactPercent
    _write_json(path, raw)


def plugin_version():
    try:
        return str((_read_json(PLUGIN_ROOT / ".zcode-plugin" / "plugin.json") or {}).get("version") or "?")
    except Exception:
        return "?"


def record_run(mode):
    """记录最近一次运行，供设置弹窗显示「插件是否在正常工作」。"""
    try:
        _write_json(DATA_DIR / "last-run.json", {
            "time": time.time(), "mode": mode, "version": plugin_version(), "root": str(PLUGIN_ROOT)})
    except Exception:
        pass


# ---------- 定位 ZCode ----------

def locate_zcode_cli(cfg):
    """定位宿主（ZCode.exe 与引擎 zcode.cjs；引擎以 Node 模式运行时就是官方 CLI）。"""
    if cfg.get("zcodeExe"):
        exe, entry = Path(cfg["zcodeExe"]), Path(cfg.get("zcodeCliEntry") or "")
        return (exe, entry) if exe.is_file() and entry.is_file() else None
    cands = []
    if os.environ.get("ZCODE_WINDOWS_APP_INSTALL_DIR"):
        cands.append(Path(os.environ["ZCODE_WINDOWS_APP_INSTALL_DIR"]))
    for mf in PLUGINS_DIR.glob("cache/*/*/*/.zcode-plugin/plugin.json"):   # 官方插件 manifest 里记有宿主绝对路径
        raw = _read_json(mf)
        exe = str(((raw or {}).get("mcpServers") or {}).get("documents", {}).get("command") or "")
        if exe.endswith("ZCode.exe"):
            cands.append(Path(exe).parent)
            break
    for base in cands:
        exe, entry = base / "ZCode.exe", base / ZCODE_CJS
        if exe.is_file() and entry.is_file():
            return exe, entry
    return None


def engine_path(cfg):
    if os.environ.get("ZCODE_AUTO_COMPACT_ENGINE"):
        return Path(os.environ["ZCODE_AUTO_COMPACT_ENGINE"])
    cli = locate_zcode_cli(cfg)
    return cli[1] if cli else None


def run_cli(args, timeout=40):
    exe, entry = locate_zcode_cli(load_config())
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    return subprocess.run([str(exe), str(entry)] + args, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


# ---------- 引擎补丁 ----------

def _head(n):
    return (b"function " + n["f"] + b"(" + n["e"] + b"={}){let " + n["t"] + b"=" + n["k"] + b"(" + n["e"]
            + b")," + n["n"] + b"=" + n["u"] + b"(" + n["e"] + b".bufferTokens)??" + n["d"])


def _clean_fn(n):
    return _head(n) + b";return Math.max(0," + n["t"] + b"-" + n["n"] + b")}"


def _patched_fn(n, percent, wname):
    native = b"Math.max(0," + n["t"] + b"-" + n["n"] + b")"
    return (_head(n) + b";{let p=" + str(percent).encode() + b",w=" + n["u"] + b"(" + n["e"]
            + b".contextWindow)??" + wname
            + b',R=typeof require=="function"?require:process.mainModule&&process.mainModule.require.bind(process.mainModule),ok=!1;'
            + b"if(R)try{let c=JSON.parse(R(\"fs\").readFileSync(R(\"os\").homedir()+\"/" + CFG_REL.encode()
            + b"\",\"utf8\")).autoCompactPercent;if(!(c>=" + str(PCT_MIN).encode() + b"&&c<=" + str(PCT_MAX).encode()
            + b"))throw 0;p=c;ok=!0}catch(x){}"
            + b"if(R&&!globalThis.__accHB)try{globalThis.__accHB=1;"
            + b"R(\"fs\").writeFileSync(R(\"os\").homedir()+\"/" + HB_REL.encode() + b"\","
            + b"JSON.stringify({time:Date.now(),pid:process.pid,runtimeRead:ok,percent:p,window:w,"
            + b"threshold:Math.min(" + native + b",Math.floor(w*p/100))}))}catch(x){}"
            + b"return Math.min(" + native + b",Math.floor(w*p/100))}}")


def inspect(data):
    """返回 (状态, 匹配对象)：patched=本插件补丁在位；clean=原文；foreign=被别的补丁改过。"""
    mine = list(FN_PATCHED.finditer(data))
    if len(mine) == 1:
        return "patched", mine[0]
    clean = list(FN_CLEAN.finditer(data))
    if len(clean) == 1:
        return "clean", clean[0]
    return ("foreign" if data else "missing"), None


def _atomic_write(path, data):
    tmp = Path(path).with_name(Path(path).name + ".zcode-auto-compact.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def apply_patch(percent, cfg=None):
    """确保引擎函数带着指定百分比；返回状态字典。"""
    percent = int(_num(percent, PCT_MIN, PCT_MAX, DEFAULTS["autoCompactPercent"]))
    path = engine_path(cfg or load_config())
    if not path or not path.is_file():
        return {"state": "missing", "error": "找不到 ZCode 引擎文件"}
    data = path.read_bytes()
    state, m = inspect(data)
    if state == "patched" and int(m.group("p")) == percent:
        return {"state": "patched", "percent": percent, "changed": False, "path": str(path)}
    if state == "patched":   # 百分比变了：先还原原文，再按新值重打
        data = data[:m.start()] + _clean_fn(m.groupdict()) + data[m.end():]
        state, m = inspect(data)
    if state != "clean":
        return {"state": state, "error": "引擎里的目标函数不是原文形状（可能 ZCode 升级后代码有变，"
                                         "或被其它补丁修改），未改动", "path": str(path)}
    names = m.groupdict()
    wd = WIN_DEFAULT.search(data)
    wname = wd.group("w") if wd and wd.group("b") == names["d"] else b"2e5"
    new = data[:m.start()] + _patched_fn(names, percent, wname) + data[m.end():]
    if inspect(new)[0] != "patched":
        return {"state": "error", "error": "补丁自检失败，未改动", "path": str(path)}
    clean_sha = hashlib.sha256(data).hexdigest()
    try:   # 原版备份：还原不依赖它（按原文形状精确回退），仅作保险
        bak = DATA_DIR / "engine-backup" / ("zcode.cjs." + clean_sha[:16])
        if not bak.exists():
            bak.parent.mkdir(parents=True, exist_ok=True)
            bak.write_bytes(data)
    except OSError:
        pass
    try:
        _atomic_write(path, new)
    except OSError as exc:
        return {"state": "error", "error": "写入引擎文件失败：{}".format(exc), "path": str(path)}
    _write_json(DATA_DIR / "engine-patch.json", {
        "time": time.time(), "percent": percent, "path": str(path),
        "zcodeBuild": os.environ.get("ZCODE_APP_VERSION"),
        "cleanSha256": clean_sha, "patchedSha256": hashlib.sha256(new).hexdigest(),
        "windowSha256": _verify_window(data, m),
        "names": {k: v.decode() for k, v in names.items()}, "windowDefault": wname.decode()})
    return {"state": "patched", "percent": percent, "changed": True, "path": str(path),
            "cleanSha256": clean_sha}


def _verify_window(data, m):
    """目标函数及其两侧紧邻上下文的哈希，用于还原校验。只比这一段，是为了不受同一文件里别处的改动
    影响（如 zcode-adb-patch 的补丁）；这一段里都是带版本特征的压缩代码，仍足以确认还原的是
    同一个版本的同一段代码。"""
    k = VERIFY_CONTEXT
    return hashlib.sha256(data[max(0, m.start() - k):m.end() + k]).hexdigest()


def _verify_restore(clean, rec):
    """还原校验：目标函数（及紧邻上下文）须与打补丁时一致。函数本身是由捕获到的名字按原文形状
    重建的。旧记录没有 windowSha256 时退回整文件比对（那种记录会被别处的改动误伤）。"""
    if not isinstance(rec, dict) or not rec.get("cleanSha256"):
        return True
    if not rec.get("windowSha256"):
        return rec["cleanSha256"] == hashlib.sha256(clean).hexdigest()
    found = list(FN_CLEAN.finditer(clean))
    return len(found) == 1 and _verify_window(clean, found[0]) == rec["windowSha256"]


def restore_engine(cfg=None):
    """把引擎函数还原为原文。"""
    path = engine_path(cfg or load_config())
    if not path or not path.is_file():
        return {"state": "missing", "error": "找不到 ZCode 引擎文件"}
    data = path.read_bytes()
    state, m = inspect(data)
    if state == "clean":
        _forget_engine()
        return {"state": "clean", "changed": False, "path": str(path)}
    if state != "patched":
        return {"state": state, "error": "引擎不是本插件打的补丁，未改动", "path": str(path)}
    clean = data[:m.start()] + _clean_fn(m.groupdict()) + data[m.end():]
    if inspect(clean)[0] != "clean":
        return {"state": "error", "error": "还原结果自检失败，未改动", "path": str(path)}
    rec = _read_json(DATA_DIR / "engine-patch.json") or {}
    sha = hashlib.sha256(clean).hexdigest()
    if not _verify_restore(clean, rec):
        return {"state": "error", "error": "还原后的引擎与记录的原版哈希不一致，未写入", "path": str(path)}
    try:
        _atomic_write(path, clean)
    except OSError as exc:
        return {"state": "error", "error": "写入引擎文件失败：{}".format(exc), "path": str(path)}
    _forget_engine()
    return {"state": "clean", "changed": True, "path": str(path), "cleanSha256": sha}


def _forget_engine():
    for name in ("engine-patch.json", "engine-heartbeat.json"):
        try:
            (DATA_DIR / name).unlink()
        except OSError:
            pass


def engine_info(cfg=None):
    """引擎补丁状态，供弹窗与 config 输出使用。"""
    cfg = cfg or load_config()
    path = engine_path(cfg)
    info = {"path": str(path) if path else None, "patchEnabled": bool(cfg.get("patchEnabled"))}
    if not path or not path.is_file():
        return dict(info, state="missing")
    state, m = inspect(path.read_bytes())
    info["state"] = state
    if state == "patched":
        info["percent"] = int(m.group("p"))
    rec = _read_json(DATA_DIR / "engine-patch.json")
    if isinstance(rec, dict) and rec.get("time"):
        info.update({"appliedAt": rec["time"], "zcodeBuild": rec.get("zcodeBuild")})
    hb = _read_json(DATA_DIR / "engine-heartbeat.json")
    if isinstance(hb, dict) and hb.get("time"):
        info.update({"heartbeatAt": hb["time"] / 1000.0, "runtimeRead": bool(hb.get("runtimeRead")),
                     "enginePercent": hb.get("percent")})
    return info


def do_patch():
    cfg = load_config()
    if cfg.get("patchEnabled", True):
        res = apply_patch(cfg["autoCompactPercent"], cfg)
        if res.get("error"):
            log_err("zcode-auto-compact: 引擎补丁未应用：{}".format(res["error"]))
    ensure_injection(cfg)


# ---------- app.asar 弹窗滑块注入 ----------

def asar_path_of(cfg=None):
    cli = locate_zcode_cli(cfg or load_config())
    return cli[1].parent.parent / "app.asar" if cli else None   # <安装目录>/resources/app.asar


def sync_runtime_files():
    """把注入用的两个脚本同步到数据目录（asar 里的注入行指向那里的固定路径）。"""
    import shutil
    for name in ("inject-acc.cjs", "inject-renderer.js"):
        src = PLUGIN_ROOT / "hooks" / name
        dst = DATA_DIR / name
        if src.is_file():
            same = dst.is_file() and src.read_bytes() == dst.read_bytes()
            if not same:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)


def ensure_injection(cfg=None):
    """确保 app.asar 尾部有指向数据目录 loader 的注入行（幂等，SessionStart 自修复）。"""
    try:
        import asar_inject
        cfg = cfg or load_config()
        if not cfg.get("asarInject", True):
            return
        pend = asar_inject.pending_status()
        if pend.get("pending"):
            # 已有待收尾替换：补拉一个 waiter（幂等；前一个可能已死），完全退出后自动换入
            try:
                asar_inject._spawn_waiter(Path(pend["tmp"]), Path(pend["asar"]))
            except Exception:
                pass
            return
        sync_runtime_files()
        if not (DATA_DIR / "inject-acc.cjs").is_file():
            return
        asar = asar_path_of(cfg)
        if not asar or not asar.is_file():
            return
        st = asar_inject.inspect(asar)
        if st.get("state") == "injected":
            return
        if st.get("state") == "error":
            log_err("zcode-auto-compact: app.asar 结构异常，跳过注入：{}".format(st.get("error")))
            return
        res = asar_inject.apply(asar, cfg.get("zcodeExe") or None)
        if res.get("error"):
            log_err("zcode-auto-compact: 弹窗滑块注入未完成：{}".format(res["error"]))
    except Exception as exc:
        log_err("zcode-auto-compact: 注入检查失败：{!r}".format(exc))


def injection_info(cfg=None):
    """asar 注入状态，供弹窗与 config 输出使用。"""
    try:
        import asar_inject
        info = {"enabled": bool((cfg or load_config()).get("asarInject", True))}
        info.update(asar_inject.pending_status())
        if not info.get("pending"):
            asar = asar_path_of(cfg or load_config())
            if asar and asar.is_file():
                info.update(asar_inject.inspect(asar))
            else:
                info["state"] = "missing"
        logq = DATA_DIR / "inject-log.jsonl"
        if logq.is_file():
            try:
                lines = logq.read_text(encoding="utf-8").strip().splitlines()[-40:]
                events = [json.loads(x) for x in lines if x.strip()]
                boots = [e for e in events if e.get("event") == "boot"]
                attaches = [e for e in events if e.get("event") == "attach"]
                sets = [e for e in events if e.get("event") == "set"]
                if boots:
                    info["loaderBootAt"] = boots[-1]["time"] / 1000.0
                if attaches:
                    info["sliderAttachAt"] = attaches[-1]["time"] / 1000.0
                if sets:
                    info["lastSliderSet"] = sets[-1]
            except (OSError, ValueError):
                pass
        return info
    except Exception as exc:
        return {"state": "error", "error": repr(exc)}


# ---------- 自动更新（只走宿主官方 CLI） ----------

def _installed_entry():
    data = _read_json(PLUGINS_DIR / "installed_plugins.json") or {}
    for p in data.get("plugins") or []:
        if isinstance(p, dict) and p.get("name") == PLUGIN_NAME:
            return p
    return None


def _preflight(market, source):
    """安装前对市场副本里的新版本做语法预检：坏版本不装。"""
    root = PLUGINS_DIR / "marketplaces" / str(market or "") / (source if isinstance(source, str) else PLUGIN_NAME)
    if not root.is_dir():
        return ""
    for name in ("hooks/autocompact.py", "hooks/settings_dialog.py", "hooks/asar_inject.py"):
        f = root / name
        if f.is_file():
            try:
                compile(f.read_text(encoding="utf-8"), str(f), "exec")
            except (SyntaxError, UnicodeDecodeError) as exc:
                return "{}: {}".format(name, exc)
    return ""


def _run_update(cfg, inst):
    out = {"time": time.time()}
    if not locate_zcode_cli(cfg):
        log_err("zcode-auto-compact: 找不到 ZCode 宿主 CLI，跳过自动更新")
        return dict(out, lastResult="cli-not-found")
    market = inst.get("marketplace") or ""
    plugin_id = inst.get("id") or "{}@{}".format(PLUGIN_NAME, market)
    if market:
        r = run_cli(["plugins", "marketplace", "update", market, "--json"])
        if r.returncode != 0:
            return dict(out, lastResult="marketplace-refresh-failed", detail=(r.stderr or r.stdout)[-400:])
    bad = _preflight(market, inst.get("source"))
    if bad:
        log_err("zcode-auto-compact: 新版本语法预检失败，不安装：{}".format(bad))
        return dict(out, lastResult="preflight-failed", detail=bad)
    r = run_cli(["plugins", "update", plugin_id, "--json"])
    text = (r.stdout or "").strip()
    try:
        data = json.loads(text.splitlines()[-1])   # --json 输出；拿不到 JSON 时按文本处理
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if r.returncode != 0 or data.get("ok") is False:
        return dict(out, lastResult="failed", detail=(r.stderr or text)[-400:])
    before = inst.get("version")
    after = (_installed_entry() or {}).get("version") or data.get("version") or before   # 以宿主安装记录为准
    return dict(out, lastResult="updated" if after != before else "up-to-date",
                fromVersion=before, version=after, detail=text[-200:])


def _acquire_lock(path):
    for _ in range(2):
        try:
            os.close(os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:   # 超过 5 分钟视为残留锁
                if time.time() - path.stat().st_mtime > 300:
                    path.unlink()
                    continue
            except OSError:
                pass
            return False
        except OSError:
            return True   # 无法加锁时不因此跳过更新
    return False


def do_self_update(force=False):
    cfg = load_config()
    if not force and not cfg.get("autoUpdate", True):
        return
    inst = _installed_entry()   # 尚未安装（直接从源码目录运行）时无需更新
    if not inst:
        return
    now = time.time()
    ufile, lock = DATA_DIR / "update.json", DATA_DIR / "update.lock"
    st = _read_json(ufile) or {}
    if not isinstance(st, dict):
        st = {}
    try:
        elapsed = now - float(st.get("lastCheck") or 0)
    except (TypeError, ValueError):
        elapsed = None
    if not force and elapsed is not None and 0 <= elapsed < int(cfg["autoUpdateIntervalMinutes"]) * 60:
        return
    st["lastCheck"] = now
    if not _acquire_lock(lock):
        _write_json(ufile, st)
        return
    try:
        st.update(_run_update(cfg, inst))
    except Exception as exc:
        st["lastResult"], st["detail"] = "failed", repr(exc)[:400]
    finally:
        try:
            _write_json(ufile, st)
        except Exception:
            pass
        try:
            lock.unlink()
        except OSError:
            pass


# ---------- 设置：config（命令行）/ settings（弹窗） ----------

def do_config(args):
    changes, i = {}, 0
    try:
        while i < len(args):
            a = args[i]
            if a == "--reset":
                changes.update({key: None for key, _typ in CONFIG_OPTS.values()})
            elif a in CONFIG_OPTS:
                key, typ = CONFIG_OPTS[a]
                val = args[i + 1]
                if typ is bool:
                    changes[key] = val.lower() in ("on", "true", "1", "yes")
                else:
                    changes[key] = int(val)
                i += 1
            else:
                raise ValueError("未知参数 {}".format(a))
            i += 1
    except (IndexError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "usage": "config [--percent N] [--patch on|off] [--interval N] "
                            "[--auto-update on|off] [--reset]"}, ensure_ascii=False))
        return 2
    if changes:
        save_user_config(changes)
    cfg = load_config()
    action = None
    if ("autoCompactPercent" in changes or "patchEnabled" in changes) and cfg["patchEnabled"]:
        action = apply_patch(cfg["autoCompactPercent"], cfg)   # 阈值变了：同步补丁
    elif changes.get("patchEnabled") is False:
        action = restore_engine(cfg)
    engine = engine_info(cfg)
    if action:
        engine["action"] = {k: v for k, v in action.items() if k not in ("state", "path")}
    print(json.dumps({"changed": bool(changes), "configPath": str(user_config_path()), "config": cfg,
                      "engine": engine, "asar": injection_info(cfg)}, ensure_ascii=False))
    return 0


def do_settings():
    script = PLUGIN_ROOT / "hooks" / "settings_dialog.py"
    if not script.is_file():
        log_err("zcode-auto-compact: 找不到 {}".format(script))
        return 1
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([str(pythonw if pythonw.is_file() else Path(sys.executable)), str(script)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, **kwargs)
    except Exception as exc:
        log_err("zcode-auto-compact: 设置弹窗启动失败：{!r}".format(exc))
        return 1
    return 0


# ---------- 用量（仅设置弹窗展示用，只读） ----------

def resolve_db_path():
    cands = []
    if os.environ.get("ZCODE_USAGE_DB"):
        cands.append(Path(os.environ["ZCODE_USAGE_DB"]))
    cands.append(HOME / ".zcode" / "cli" / "db" / "db.sqlite")
    if os.environ.get("ZCODE_DATA_BASE_DIR"):
        cands.append(Path(os.environ["ZCODE_DATA_BASE_DIR"]) / ".zcode" / "cli" / "db" / "db.sqlite")
    for p in cands:
        if p.is_file():
            return p
    return None


def current_context(sid=None):
    """返回最近一次模型请求的 (上下文 tokens, 模型 id, 服务商 id)；无数据返回 None。"""
    db = resolve_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(db.as_posix()), uri=True, timeout=3)
        try:
            if not sid:
                row = con.execute(
                    "select session_id from model_usage where status='completed' and input_tokens>0 "
                    "and (query_source is null or query_source='main_turn') "
                    "order by completed_at desc limit 1").fetchone()
                sid = row[0] if row else None
            if not sid:
                return None
            for row in con.execute(
                    "select input_tokens, output_tokens, provider_id, model_id from model_usage where session_id=? "
                    "and status='completed' and input_tokens>0 and (query_source is null or query_source='main_turn') "
                    "order by completed_at desc, id desc limit 1", (sid,)):
                return (row[0] or 0) + (row[1] or 0), str(row[3] or ""), str(row[2] or "")
            return None
        finally:
            con.close()
    except sqlite3.Error:
        return None


# ---------- 入口 ----------

def drain_stdin(timeout_s=2.0):
    """消费 stdin（hook 协议要求），设超时兜底：客户端不给 EOF 也不挂起。内容不使用。
    返回读取线程；到超时仍阻塞在 stdin 上时，退出前要走 finish_hook。"""
    def pump():
        try:
            while sys.stdin.buffer.read(65536):
                pass
        except Exception:
            pass
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    t.join(timeout_s)
    return t


def finish_hook(reader):
    """hook 模式的收尾。读取线程若还阻塞在 stdin 上，不能走正常的解释器退出：Windows 上会以
    Fatal Python error（_enter_buffered_busy）崩溃、退出码 0xC0000005。此时该做的都已做完，
    刷完诊断信息后直接退出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if reader is not None and reader.is_alive():
        os._exit(0)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = sys.argv[1:]
    mode = argv[0] if argv and not argv[0].startswith("-") else "patch"
    args = [] if mode == "patch" else argv[1:]
    reader = None
    try:
        if mode == "config":
            return do_config(args)
        if mode == "settings":
            return do_settings()
        if mode == "restore-engine":
            save_user_config({"patchEnabled": False})
            print(json.dumps(restore_engine(), ensure_ascii=False))
            return 0
        if mode == "restore-asar":
            save_user_config({"asarInject": False})
            import asar_inject
            asar = asar_path_of()
            res = (asar_inject.remove(asar) if asar and asar.is_file()
                   else {"state": "missing", "error": "找不到 app.asar"})
            print(json.dumps(res, ensure_ascii=False))
            return 0
        if mode == "inject":
            reader = drain_stdin()
            record_run(mode)
            ensure_injection()
        elif mode == "self-update":
            reader = drain_stdin()
            record_run(mode)
            do_self_update("--force" in args)
        elif mode == "patch":
            reader = drain_stdin()
            record_run(mode)
            do_patch()
    except Exception as exc:   # 最坏情况：不改动，不干扰会话
        try:
            log_err("zcode-auto-compact error: {!r}".format(exc))
        except Exception:
            pass
    if mode in ("inject", "self-update", "patch"):
        finish_hook(reader)
    return 0


if __name__ == "__main__":
    sys.exit(main())
