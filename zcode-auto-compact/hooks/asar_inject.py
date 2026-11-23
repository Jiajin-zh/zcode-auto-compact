#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-auto-compact 的 app.asar 注入器：在主入口尾部追加一行 dynamic import。

实现参照 zusage 在 3.19.1 上验证过的方式：入口 out/main/index.js 是 ESM
（package.json type:module），追加 `\n;import("<file-url>").then(...)` 即可在应用
启动时加载外部 loader；重打包时重建 asar header（偏移 + SHA256 完整性）。

只增删本插件自己的注入行（ACC_LINE_RE 识别，不限目录）；其它工具的注入行不动。
"""

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

ENTRY = "out/main/index.js"
ALIGN = 4
BLOCK = 4194304
ACC_LINE_TMPL = ('\n;import("{url}").then(() => null, (e) => console.error("[acc] load failed", e));')
ACC_LINE_RE = re.compile(
    rb'\n;import\("[^"]*inject-acc\.cjs"\)\.then\(\(\) => null, '
    rb'\(e\) => console\.error\("\[acc\] load failed", e\)\);')

DATA_DIR = Path(os.environ.get("ZCODE_AUTO_COMPACT_DATA")
                or Path.home() / ".zcode" / "zcode-auto-compact")
LOADER = DATA_DIR / "inject-acc.cjs"
PENDING = DATA_DIR / "asar-pending.json"
LOG = DATA_DIR / "asar-inject-log.jsonl"


def log_event(ev, extra=None):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": time.time(), "event": ev, **(extra or {})},
                          ensure_ascii=False) + "\n"
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def acc_line(loader=None):
    return ACC_LINE_TMPL.format(url=(loader or LOADER).resolve().as_uri())


# ---------- asar 格式 ----------

def read_header(f):
    f.seek(0)
    a, b, c, d = struct.unpack("<4I", f.read(16))
    if a != 4:
        raise ValueError("unexpected pickle prefix {}".format(a))
    return json.loads(f.read(d)), 8 + b


def iter_files(node, path=""):
    for name, ch in (node.get("files") or {}).items():
        p = "{}/{}".format(path, name)
        if isinstance(ch, dict) and "files" in ch:
            yield from iter_files(ch, p)
        elif isinstance(ch, dict) and "size" in ch:
            yield p, ch


def _integrity(data):
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(),
            "blockSize": BLOCK,
            "blocks": [hashlib.sha256(data[i:i + BLOCK]).hexdigest()
                       for i in range(0, len(data), BLOCK)]}


def repack(src_path, modify, dst_path):
    """重建 asar。modify: {asar内路径: 新内容bytes}；其余文件按原字节流式拷贝。"""
    with open(src_path, "rb") as src:
        header, base = read_header(src)
        header = json.loads(json.dumps(header))   # 深拷贝
        nodes = dict(iter_files(header))
        missing = set(modify) - set(nodes)
        if missing:
            raise ValueError("paths not in asar: {}".format(sorted(missing)))
        data_parts, offset = [], 0
        for p, node in nodes.items():
            if node.get("unpacked"):
                node["offset"] = "0"
                continue
            if p in modify:
                data = modify[p]
                node["size"] = len(data)
                node["integrity"] = _integrity(data)
            else:
                src.seek(base + int(node["offset"]))
                data = src.read(node["size"])
            node["offset"] = str(offset)
            data_parts.append(data)
            offset += len(data)
        payload = b"".join(data_parts)
        header_bytes = json.dumps(header, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
        d = len(header_bytes)
        c = (d + 4 + ALIGN - 1) // ALIGN * ALIGN
        with open(dst_path, "wb") as out:
            out.write(struct.pack("<4I", 4, c + 4, c, d))
            out.write(header_bytes)
            out.write(b"\0" * (c - d - 4))
            if out.tell() != 8 + c + 4:
                raise ValueError("header pad mismatch")
            out.write(payload)
    return dst_path


def entry_bytes(asar_path):
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        node = dict(iter_files(header))["/" + ENTRY]
        f.seek(base + int(node["offset"]))
        return f.read(node["size"])


def inspect(asar_path):
    """返回注入状态：injected=我的行在位；clean=没有；error=结构问题。"""
    try:
        with open(asar_path, "rb") as f:
            header, _base = read_header(f)
            n = sum(1 for _ in iter_files(header))
        entry = entry_bytes(asar_path)
        return {"state": "injected" if ACC_LINE_RE.search(entry) else "clean",
                "files": n, "entrySize": len(entry)}
    except (OSError, ValueError, KeyError, struct.error) as exc:
        return {"state": "error", "error": repr(exc)}


def _syntax_check(entry_data, zcode_exe):
    """入口是 ESM：写 .mjs 用 ZCode 自带的 node 做语法检查。"""
    if not zcode_exe or not Path(zcode_exe).is_file():
        return True, "skip"
    fd, tmp = None, None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix=".acc-entry.", suffix=".mjs",
                                   dir=str(DATA_DIR))
        with os.fdopen(fd, "wb") as f:
            f.write(entry_data)
        env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
        r = subprocess.run([str(zcode_exe), "--check", tmp], env=env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stderr or "")[-300:]
    except OSError as exc:
        return False, repr(exc)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _verify_repacked(src_path, dst_path, modified_paths):
    """重打包校验：header 可解析、文件数一致、除修改文件外逐文件字节相同。"""
    with open(src_path, "rb") as fs, open(dst_path, "rb") as fd:
        hs, bs = read_header(fs)
        hd, bd = read_header(fd)
        ns, nd = dict(iter_files(hs)), dict(iter_files(hd))
        if set(ns) != set(nd):
            raise ValueError("file set changed")
        for p, node in nd.items():
            if node.get("unpacked"):
                continue
            fs.seek(bs + int(ns[p]["offset"]))
            fd.seek(bd + int(node["offset"]))
            if p in modified_paths:
                continue
            if fs.read(ns[p]["size"]) != fd.read(node["size"]):
                raise ValueError("unmodified file differs: {}".format(p))


# ---------- 安装 / 卸载 ----------

def locate_zcode_exe():
    for env in ("ZCODE_WINDOWS_APP_INSTALL_DIR",):
        if os.environ.get(env):
            exe = Path(os.environ[env]) / "ZCode.exe"
            if exe.is_file():
                return exe
    return None


def _swap_in(tmp_path, asar_path):
    """尝试原子替换；ZCode 运行中会失败 → 挂待收尾 waiter，完全退出后自动换入。"""
    try:
        os.replace(tmp_path, asar_path)
        return {"swapped": True}
    except OSError as exc:
        meta = {"time": time.time(), "tmp": str(tmp_path), "asar": str(asar_path),
                "operation": "swap", "error": str(exc)}
        tmp_path.with_name(tmp_path.name + ".json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        PENDING.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _spawn_waiter(tmp_path, asar_path)
        return {"swapped": False, "pending": True, "error": str(exc)}


def _waiter_script():
    return (
        "import json, os, sys, time\n"
        "tmp, dst = sys.argv[1], sys.argv[2]\n"
        "log = sys.argv[3]\n"
        "pending = sys.argv[4]\n"
        "for _ in range(7200):\n"
        "    try:\n"
        "        os.replace(tmp, dst)\n"
        "        with open(log, 'a', encoding='utf-8') as f:\n"
        "            f.write(json.dumps({'time': time.time(), 'event': 'swapped'}) + chr(10))\n"
        "        break\n"
        "    except OSError:\n"
        "        if not os.path.exists(tmp):\n"
        "            break\n"
        "        time.sleep(0.5)\n"
        "try:\n"
        "    os.unlink(pending)\n"
        "except OSError:\n"
        "    pass\n"
    )




def spawn_survivor(args_list):
    """经 WMI 创建进程：完全脱离当前进程树（回合结束/应用退出都杀不死）。"""
    line = " ".join('"' + str(a).replace('"', '\\"') + '"' for a in args_list)
    ps = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine='" + line.replace("'", "''") + "'} | Out-Null")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
        return True
    except Exception:
        return False

def _spawn_waiter(tmp_path, asar_path):
    waiter = DATA_DIR / "asar-waiter.py"
    waiter.write_text(_waiter_script(), encoding="utf-8")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.is_file() else Path(sys.executable))
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    kwargs = {"creationflags": flags} if flags else {"start_new_session": True}
    args = [exe, "-I", str(waiter), str(tmp_path), str(asar_path), str(LOG), str(PENDING)]
    if not spawn_survivor(args):   # WMI 失败才退回直接派生（回合结束会被回收）
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **kwargs)


def apply(asar_path, zcode_exe=None):
    """追加/重定向注入行。"""
    asar_path = Path(asar_path)
    if not asar_path.is_file():
        return {"state": "missing", "error": "找不到 app.asar"}
    entry = entry_bytes(asar_path)
    line = acc_line().encode()
    if entry.rstrip().endswith(line.rstrip()):   # 当前注入行已在尾部：无需改动
        return {"state": "injected", "changed": False}
    new_entry = ACC_LINE_RE.sub(b"", entry) + line   # 剥离任何旧 acc 行（含指向旧目录的）后追加
    ok, err = _syntax_check(new_entry, zcode_exe or locate_zcode_exe())
    if not ok:
        return {"state": "error", "error": "入口语法检查失败：{}".format(err)}
    tmp = asar_path.with_name(asar_path.name + ".acc.tmp")
    repack(asar_path, {"/" + ENTRY: new_entry}, tmp)
    _verify_repacked(asar_path, tmp, {"/" + ENTRY})
    res = inspect(tmp)
    if res["state"] != "injected":
        tmp.unlink(missing_ok=True)
        return {"state": "error", "error": "重打包自检失败"}
    swap = _swap_in(tmp, asar_path)
    log_event("apply", {"swapped": swap.get("swapped")})
    return {"state": "injected", "changed": True, **swap}


def remove(asar_path, zcode_exe=None):
    """剥离注入行。"""
    asar_path = Path(asar_path)
    if not asar_path.is_file():
        return {"state": "missing", "error": "找不到 app.asar"}
    entry = entry_bytes(asar_path)
    if not ACC_LINE_RE.search(entry):
        return {"state": "clean", "changed": False}
    new_entry = ACC_LINE_RE.sub(b"", entry)
    ok, err = _syntax_check(new_entry, zcode_exe or locate_zcode_exe())
    if not ok:
        return {"state": "error", "error": "入口语法检查失败：{}".format(err)}
    tmp = asar_path.with_name(asar_path.name + ".acc.tmp")
    repack(asar_path, {"/" + ENTRY: new_entry}, tmp)
    _verify_repacked(asar_path, tmp, {"/" + ENTRY})
    res = inspect(tmp)
    if res["state"] != "clean":
        tmp.unlink(missing_ok=True)
        return {"state": "error", "error": "重打包自检失败"}
    swap = _swap_in(tmp, asar_path)
    log_event("remove", {"swapped": swap.get("swapped")})
    return {"state": "clean", "changed": True, **swap}


def pending_status():
    if PENDING.is_file():
        try:
            meta = json.loads(PENDING.read_text(encoding="utf-8"))
            if Path(meta.get("tmp", "")).is_file():
                return {"pending": True, **meta}
        except (OSError, ValueError):
            pass
        try:
            PENDING.unlink()
        except OSError:
            pass
    return {"pending": False}


if __name__ == "__main__":
    print(json.dumps(inspect(sys.argv[1] if len(sys.argv) > 1 else
                             "E:/ZCode/resources/app.asar"), ensure_ascii=False))
