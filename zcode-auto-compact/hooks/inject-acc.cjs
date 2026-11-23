/* zcode-auto-compact 主进程 loader：由 app.asar 入口尾部的 dynamic import 加载。
 * 只做三件事：读写阈值配置、给渲染层提供改值通道、把渲染层脚本注入每个窗口。
 * 全部包在 try/catch 里：任何异常都不影响 ZCode 本身。 */
(function () {
  "use strict";
  var ROOT, CFG, RENDERER, electron;
  try {
    var path = require("path"), fs = require("fs"), os = require("os");
    ROOT = path.join(os.homedir(), ".zcode", "zcode-auto-compact");
    CFG = path.join(ROOT, "config.json");
    RENDERER = path.join(ROOT, "inject-renderer.js");
  } catch (e) { return; }
  function logEvent(ev, extra) {
    try {
      var line = JSON.stringify(Object.assign({ time: Date.now(), pid: process.pid, event: ev }, extra || {})) + "\n";
      fs.appendFileSync(path.join(ROOT, "inject-log.jsonl"), line);
    } catch (e) { }
  }
  function readPercent() {
    try {
      var c = JSON.parse(fs.readFileSync(CFG, "utf8")).autoCompactPercent;
      return (typeof c === "number" && c >= 10 && c <= 95) ? Math.floor(c) : 70;
    } catch (e) { return 70; }
  }
  function writePercent(p) {
    p = Math.max(10, Math.min(95, Math.floor(Number(p) || 0)));
    try {
      var raw = {};
      try { raw = JSON.parse(fs.readFileSync(CFG, "utf8")) || {}; } catch (e) { }
      raw.autoCompactPercent = p;
      var tmp = CFG + ".tmp";
      fs.writeFileSync(tmp, JSON.stringify(raw, null, 2) + "\n");
      fs.renameSync(tmp, CFG);
    } catch (e) { logEvent("write-failed", { percent: p }); }
    return p;
  }
  try { electron = require("electron"); } catch (e) { return; }
  var app = electron.app, BrowserWindow = electron.BrowserWindow, session = electron.session;

  function broadcast(p) {
    try {
      BrowserWindow.getAllWindows().forEach(function (w) {
        try {
          if (!w.isDestroyed()) w.webContents.executeJavaScript(
            "window.__accUpdate&&window.__accUpdate(" + p + ")").catch(function () { });
        } catch (e) { }
      });
    } catch (e) { }
  }
  function injectInto(wc) {
    try {
      if (!wc || wc.isDestroyed()) return;
      var stat = fs.statSync(RENDERER);   // kill-switch：删掉渲染脚本即不再注入
      if (!stat.isFile()) return;
      var js = fs.readFileSync(RENDERER, "utf8");
      wc.executeJavaScript(js + "\n;window.__accInit&&window.__accInit(" + readPercent() + ");")
        .catch(function () { });
    } catch (e) { }
  }
  function wireWindow(w) {
    try {
      injectInto(w.webContents);
      w.webContents.on("did-finish-load", function () { injectInto(w.webContents); });
    } catch (e) { }
  }
  function wireSession() {
    try {
      session.defaultSession.webRequest.onBeforeRequest(
        { urls: ["https://acc-inject.local/*"] },
        function (details, cb) {
          var u = null;
          try { u = new URL(details.url); } catch (e) { return cb({ cancel: true }); }
          var p = u.searchParams.get("p");
          if (u.pathname === "/set" && p !== null) {
            var v = writePercent(p);
            logEvent("set", { percent: v });
            broadcast(v);
          } else if (u.pathname === "/attach") {
            logEvent("attach", { percent: readPercent() });
          } else if (u.pathname === "/diag") {
            var d = {};
            u.searchParams.forEach(function (val, key) { d[key] = String(val).slice(0, 140); });
            logEvent("diag", d);
          }
          cb({ cancel: true });
        });
    } catch (e) { }
  }
  try {
    app.whenReady().then(function () {
      wireSession();
      try { BrowserWindow.getAllWindows().forEach(wireWindow); } catch (e) { }
      try { app.on("browser-window-created", function (_e, w) { wireWindow(w); }); } catch (e) { }
      try {
        setInterval(function () {
          try { BrowserWindow.getAllWindows().forEach(wireWindow); } catch (e) { }
        }, 15000);
      } catch (e) { }
      logEvent("boot", { percent: readPercent() });
    }).catch(function () { });
  } catch (e) { }
})();
