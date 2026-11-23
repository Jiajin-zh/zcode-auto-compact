#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zcode-auto-compact 设置弹窗（Tkinter，无第三方依赖）。

由 autocompact.py settings 分离启动；也可直接运行：python settings_dialog.py
"""

import os
import sys
import time
import tkinter as tk
from tkinter import ttk

sys.dont_write_bytecode = True   # 不在插件目录里生成 __pycache__（会被打包进安装副本）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autocompact as cc

BG, CARD, FG, MUTED, ACCENT, OK, WARN = ("#f5f6f8", "#ffffff", "#1f2329", "#646a73",
                                        "#3370ff", "#2a9d1f", "#d9660f")
FONT = "Microsoft YaHei UI"
EDITABLE = ("autoCompactPercent", "patchEnabled", "autoUpdate")


def fmt_time(ts):
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "—"


def engine_text(info):
    """返回 (引擎补丁状态文字, 颜色)。"""
    if not info.get("patchEnabled", True):
        return "引擎补丁：已关闭，ZCode 按默认阈值压缩", MUTED
    state = info.get("state")
    if state == "patched":
        if info.get("heartbeatAt"):
            how = "，改阈值即时生效" if info.get("runtimeRead") else "，改阈值需重启 ZCode 才生效"
            return ("引擎补丁：已加载（ZCode {}，{}）{}".format(
                info.get("zcodeBuild") or "?", fmt_time(info["heartbeatAt"]), how), OK)
        return "引擎补丁：已写入（阈值 {}%），完全重启 ZCode 后生效".format(info.get("percent")), WARN
    if state == "clean":
        return "引擎补丁：未打上（点「保存」即可打上）", WARN
    if state == "foreign":
        return "引擎补丁：引擎里的目标函数不是原文形状（ZCode 升级后代码有变，或被其它补丁改过），本插件未改动它", WARN
    return "引擎补丁：找不到 ZCode 引擎文件", WARN


def build(root, cfg, usage, runinfo):
    for w in root.pack_slaves():
        w.destroy()
    root.configure(bg=BG)
    body = tk.Frame(root, bg=BG, padx=22, pady=18)
    body.pack(fill="both", expand=True)

    tk.Label(body, bg=BG, fg=FG, font=(FONT, 15, "bold"), text="自动上下文压缩 · 设置").pack(anchor="w")
    tk.Label(body, bg=BG, fg=MUTED, font=(FONT, 8),
             text="插件 v{}   ·   设置文件 {}".format(cc.plugin_version(), cc.user_config_path())
             ).pack(anchor="w", pady=(2, 12))

    # ---- 用量卡片（只读展示；触发压缩用的是 ZCode 自己的估算） ----
    card = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground="#dee0e3")
    card.pack(fill="x", pady=(0, 14))
    inner = tk.Frame(card, bg=CARD, padx=14, pady=11)
    inner.pack(fill="x")
    if usage:
        ctx, model, _provider = usage
        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, bg=CARD, fg=MUTED, font=(FONT, 9), text="当前会话上下文用量（约）").pack(side="left")
        tk.Label(head, bg=CARD, fg=FG, font=(FONT, 12, "bold"),
                 text="{:,} tokens   {}".format(ctx, model)).pack(side="right")
    else:
        tk.Label(inner, bg=CARD, fg=MUTED, font=(FONT, 9), text="当前会话上下文用量：暂无数据").pack(anchor="w")
    tk.Label(inner, bg=CARD, fg=MUTED, font=(FONT, 8),
             text="百分比以 ZCode 上下文窗口弹窗里的「已用 / 总量」为准"
             ).pack(anchor="w", pady=(2, 0))

    # ---- 表单 ----
    form = tk.Frame(body, bg=BG)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)

    tk.Label(form, bg=BG, fg=FG, font=(FONT, 10), text="自动压缩阈值"
             ).grid(row=0, column=0, sticky="w", padx=(0, 18), pady=(4, 2))
    f0 = tk.Frame(form, bg=BG)
    f0.grid(row=0, column=1, sticky="we", pady=(4, 2))
    pct_var = tk.IntVar(value=cfg["autoCompactPercent"])
    pct_lab = tk.Label(f0, bg=BG, fg=ACCENT, font=(FONT, 11, "bold"), width=6, anchor="w")
    sc = tk.Scale(f0, from_=cc.PCT_MIN, to=cc.PCT_MAX, orient="horizontal", showvalue=False, length=220,
                  variable=pct_var, bg=BG, fg=MUTED, troughcolor="#dfe3ea", activebackground=ACCENT,
                  highlightthickness=0, sliderrelief="flat", font=(FONT, 8))
    sc.pack(side="left")
    pct_lab.pack(side="left", padx=(10, 0))
    hint = tk.Label(form, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", anchor="w", wraplength=430)
    hint.grid(row=1, column=1, sticky="we", pady=(0, 10))

    tk.Label(form, bg=BG, fg=FG, font=(FONT, 10), text="自动更新"
             ).grid(row=2, column=0, sticky="w", padx=(0, 18), pady=6)
    f2 = tk.Frame(form, bg=BG)
    f2.grid(row=2, column=1, sticky="we", pady=6)
    au_var = tk.BooleanVar(value=bool(cfg["autoUpdate"]))
    tk.Checkbutton(f2, variable=au_var, bg=BG, fg=FG, activebackground=BG, selectcolor=BG,
                   font=(FONT, 9), text="发现新版本时自动安装（每个新会话检查一次，最多每小时一次）"
                   ).pack(side="left")

    eng = tk.Label(form, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", anchor="w", wraplength=430)
    eng.grid(row=3, column=0, columnspan=2, sticky="we", pady=(14, 0))
    asr = tk.Label(form, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", anchor="w", wraplength=430)
    asr.grid(row=4, column=0, columnspan=2, sticky="we", pady=(6, 0))

    status = tk.Label(body, bg=BG, fg=MUTED, font=(FONT, 9), text=runinfo, anchor="w", wraplength=430,
                      justify="left")
    status.pack(fill="x", pady=(16, 2))

    foot = tk.Frame(body, bg=BG)
    foot.pack(fill="x")
    ttk.Button(foot, text="保存", style="Accent.TButton", command=lambda: save()).pack(side="right")
    ttk.Button(foot, text="恢复默认", command=lambda: reset()).pack(side="right", padx=(0, 8))
    ttk.Button(foot, text="还原引擎", command=lambda: unpatch()).pack(side="right", padx=(0, 8))

    # ---- 行为 ----
    def on_scale(_v=None):
        pct_lab.config(text="{} %".format(int(pct_var.get())))
        hint.config(text="达到模型上下文窗口的 {}% 时，ZCode 自动压缩上下文；不打补丁时默认要到约 90% 以上才压缩。"
                          .format(int(pct_var.get())))

    def show_engine():
        text, color = engine_text(cc.engine_info())
        eng.config(text=text, fg=color)
        ai = cc.injection_info()
        if not ai.get("enabled", True):
            atext, acolor = "弹窗滑块：已关闭（上下文弹窗里不显示）", MUTED
        elif ai.get("pending"):
            atext, acolor = "弹窗滑块：待收尾——完全退出 ZCode 后自动换入，重开后出现", WARN
        elif ai.get("state") == "injected":
            if ai.get("sliderAttachAt"):
                atext, acolor = ("弹窗滑块：已在上下文弹窗里（{}）· 拖动即改，全部会话即时生效"
                                 .format(fmt_time(ai["sliderAttachAt"])), OK)
            elif ai.get("loaderBootAt"):
                atext, acolor = "弹窗滑块：已加载，打开上下文弹窗即可见", OK
            else:
                atext, acolor = "弹窗滑块：已注入，完全重启 ZCode 后出现", WARN
        elif ai.get("state") == "clean":
            atext, acolor = "弹窗滑块：未注入（点「保存」即可注入）", WARN
        else:
            atext, acolor = "弹窗滑块：{}".format(ai.get("error") or ai.get("state", "未知状态")), WARN
        asr.config(text=atext, fg=acolor)

    def save():
        p = int(pct_var.get())
        cc.save_user_config({"autoCompactPercent": p, "patchEnabled": True,
                             "autoUpdate": bool(au_var.get())})
        res = cc.apply_patch(p)
        info = cc.engine_info()
        show_engine()
        if res.get("error"):
            status.configure(text="阈值已保存，但引擎补丁未更新：{}".format(res["error"]), fg=WARN)
            return
        if res.get("changed") is False:
            text = "已保存（引擎补丁已是这个阈值）"
        elif info.get("runtimeRead") and info.get("heartbeatAt"):
            text = "已保存，运行中的 ZCode 从下次请求起按新阈值压缩"
        else:
            text = "已保存并写入引擎补丁，完全重启 ZCode 后生效"
        status.configure(text=text, fg=OK)
        root.after(2800, root.destroy)

    def unpatch():
        cc.save_user_config({"patchEnabled": False})   # 同时停止以后自动打补丁
        res = cc.restore_engine()
        show_engine()
        if res.get("error"):
            status.configure(text="未能还原：{}".format(res["error"]), fg=WARN)
            return
        status.configure(text="已把引擎函数还原为原文，ZCode 重启后恢复默认压缩阈值" if res.get("changed")
                             else "引擎本来就是原文，已停止自动打补丁", fg=MUTED)

    def reset():
        cc.save_user_config({k: None for k in EDITABLE})
        new = cc.load_config()
        pct_var.set(new["autoCompactPercent"])
        au_var.set(bool(new["autoUpdate"]))
        on_scale()
        status.configure(text="已恢复默认值（点击「保存」后生效）", fg=MUTED)

    sc.config(command=on_scale)
    on_scale()
    show_engine()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.title("自动上下文压缩 · 设置")
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", font=(FONT, 10), padding=(14, 5))
    style.configure("Accent.TButton", font=(FONT, 10, "bold"), padding=(16, 5),
                    background=ACCENT, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#2860e1")])
    root.resizable(False, False)
    build(root, cc.load_config(), cc.current_context(os.environ.get("ZCODE_SESSION_ID")),
          "上次 hook 运行：{}".format(fmt_time((cc._read_json(cc.DATA_DIR / "last-run.json") or {}).get("time")))
          if (cc._read_json(cc.DATA_DIR / "last-run.json") or {}).get("time")
          else "尚未检测到 hook 运行记录；新会话发一条消息后此处会显示")
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("+{}+{}".format((sw - root.winfo_reqwidth()) // 2, int(sh * 0.36)))
    root.mainloop()


if __name__ == "__main__":
    main()
