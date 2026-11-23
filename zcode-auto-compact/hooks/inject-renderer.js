/* zcode-auto-compact 渲染层：在「上下文容量」弹窗里注入自动压缩阈值分区。
 * 标题行直接克隆原生标题行 DOM（样式与「上下文容量」完全一致）；滑块与原生
 * 进度条同视觉重量。改动即时写入配置（经主进程），运行中的引擎每次判定都会
 * 重读该值——即改即生效，全部会话生效。
 * 带版本守卫：主进程每 15 秒重放本脚本，版本号变化时先清掉旧实例再重装。 */
(function () {
  var VER = 10;
  if (window.__accVer === VER) return;
  if (window.__accCleanup) { try { window.__accCleanup(); } catch (e) { } }
  window.__accVer = VER;
  var P = 70;
  var HC = '[data-slot="hover-card-content"]';
  var ROW = "acc-ac-sec";
  var MIN = 10, MAX = 95;
  var cleanups = [];
  window.__accCleanup = function () {
    cleanups.forEach(function (f) { try { f(); } catch (e) { } });
    try {
      var rows = document.querySelectorAll("." + ROW);
      for (var i = 0; i < rows.length; i++) rows[i].remove();
    } catch (e) { }
  };

  function send(path, p) {
    try {
      fetch("https://acc-inject.local" + path + (p === undefined ? "" : "?p=" + p),
        { mode: "no-cors" }).catch(function () { });
    } catch (e) { }
  }
  var diagSeen = {};
  function diag(reason, extra) {
    var key = reason + "|" + String(extra || "");
    var now = Date.now();
    if (diagSeen[key] && now - diagSeen[key] < 60000) return;
    diagSeen[key] = now;
    send("/diag?r=" + encodeURIComponent(reason) + "&x=" + encodeURIComponent(String(extra || "").slice(0, 400)));
  }
  window.__accSet = function (p) { P = p; paint(); send("/set", p); };
  window.__accUpdate = function (p) { P = p; paint(); };
  window.__accInit = function (p) { P = p; scan(); };

  /* 样式：与原生上下文进度条同一套观感（高度/圆角/轨道与填充色都取自原生元素），
     只保留一个更小的滑钮用于拖动 */
  var STYLE_ID = "acc-ac-style";
  var ACC = "var(--color-primary, var(--primary, #3370ff))";
  var FILL = ACC, TRACK = "rgba(128,128,128,.18)";
  function ensureStyle() {
    var st = document.getElementById(STYLE_ID);
    if (st) return;
    st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = ""
      + ".acc-ac-range{-webkit-appearance:none;appearance:none;width:100%;height:8px;"
      + "border-radius:6px;background:rgba(128,128,128,.18);outline:none;margin:12px 0 0;cursor:pointer;}"
      + ".acc-ac-range::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:12px;height:12px;"
      + "border-radius:50%;background:#fff;border:2px solid var(--acc-fill," + ACC + ");"
      + "box-shadow:0 1px 2px rgba(0,0,0,.2);transition:transform .12s ease;}"
      + ".acc-ac-range:hover::-webkit-slider-thumb{transform:scale(1.08);}"
      + ".acc-ac-range:active::-webkit-slider-thumb{transform:scale(1.15);}";
    document.head.appendChild(st);
    cleanups.push(function () { try { st.remove(); } catch (e) { } });
  }
  /* 取原生进度条的尺寸与颜色：轨道 = progress 的背景，填充 = indicator（或首个分段）的背景 */
  function syncNativeLook(card) {
    if (!card || !slider) return;
    try {
      var prog = card.querySelector('[data-slot="progress"]');
      if (!prog) return;
      var cs = getComputedStyle(prog);
      var h = parseFloat(cs.height);
      if (h > 0) slider.style.height = cs.height;
      if (cs.borderRadius) slider.style.borderRadius = cs.borderRadius;
      if (cs.backgroundColor) TRACK = cs.backgroundColor;
      var ind = prog.querySelector('[data-slot="progress-indicator"]');
      if (ind) {
        var fill = getComputedStyle(ind).backgroundColor;
        if (!fill || /rgba\(0, 0, 0, 0\)|transparent/.test(fill)) {
          var seg = ind.firstElementChild;
          if (seg) fill = getComputedStyle(seg).backgroundColor;
        }
        if (fill && !/rgba\(0, 0, 0, 0\)|transparent/.test(fill)) FILL = fill;
      }
      slider.style.setProperty("--acc-fill", FILL);
      paint();
      diag("look", "h=" + slider.style.height + " r=" + slider.style.borderRadius
        + " fill=" + FILL + " track=" + TRACK);
    } catch (e) { }
  }

  var slider = null, valEl = null, labEl = null, fontOk = false;

  /* 字体对齐：克隆行会丢掉祖先节点提供的字号/字重，这里从原生标题（「上下文容量」）
     和原生标值上取计算样式，逐项内联到本分区，保证与原生完全一致。 */
  function gs(el) {
    try {
      var c = getComputedStyle(el);
      return c.fontSize + "/" + c.fontWeight + "/" + c.lineHeight;
    } catch (e) { return ""; }
  }
  function setFont(dst, src) {
    if (!dst || !src) return;
    try {
      var c = getComputedStyle(src), props = ["fontSize", "fontFamily", "fontWeight", "fontStyle",
        "lineHeight", "letterSpacing", "fontVariantNumeric", "color"], i;
      for (i = 0; i < props.length; i++) {
        var v = c[props[i]];
        if (v) dst.style[props[i]] = v;
      }
      dst.style.opacity = "1";
    } catch (e) { }
  }
  function nativeLabelEl(card) {
    var best = null;
    try {
      var all = card.querySelectorAll("*");
      for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.children.length === 0 && String(el.textContent || "").trim() === "上下文容量") best = el;
      }
    } catch (e) { }
    return best;
  }
  /* 原生标题行：「上下文容量」那个 span 所在的父行。
     4.0.0 起该行不再带 justify-between，按类名找会误选下面的小字行（字号偏小）。 */
  function nativeHeader(card) {
    var lab = nativeLabelEl(card);
    if (!lab || !lab.parentElement) return null;
    var row = lab.parentElement;
    var val = row.lastElementChild;
    return { row: row, lab: lab, val: val && val !== lab ? val : null };
  }
  function matchFonts(card) {
    if (!card || !labEl) return false;
    var nat = nativeHeader(card);   // 原生还没渲染出标题时留待下次 scan 重试
    if (!nat) return false;
    setFont(labEl, nat.lab);
    if (nat.val) setFont(valEl, nat.val);
    fontOk = true;
    diag("font", "lab=" + gs(labEl) + " ref=" + gs(nat.lab)
      + (nat.val ? " val=" + gs(valEl) + " valref=" + gs(nat.val) : ""));
    return true;
  }
  function cloneText(src, text) {
    var el = src.cloneNode(false);   // 只保留标签与类名，文字重写
    el.removeAttribute("id");
    el.removeAttribute("aria-label");
    el.removeAttribute("data-testid");
    el.textContent = text;
    return el;
  }
  function buildHeader(card) {
    var nat = nativeHeader(card);
    if (nat) {
      try {
        var row = document.createElement("div");
        /* 用原生行的布局类，但去掉外边距/上下内边距/上边框（本分区在卡片顶部） */
        var keep = [], raw = String(nat.row.className || "").split(/\s+/);
        for (var i = 0; i < raw.length; i++) {
          var c = raw[i];
          if (!c || /^(m[trblxy]?|p[trblxy]?|border)(-|$)/.test(c)) continue;
          keep.push(c);
        }
        row.className = keep.join(" ");
        row.style.marginTop = "0";
        var lab = cloneText(nat.lab, "自动压缩阈值");   // 类名与「上下文容量」完全一致
        row.appendChild(lab);
        var val = nat.val ? cloneText(nat.val, P + "%") : document.createElement("span");
        if (!nat.val) val.textContent = P + "%";
        row.appendChild(val);
        labEl = lab;
        valEl = val;
        fontOk = false;
        diag("header-built", String(nat.lab.className || "").slice(0, 120));
        return row;
      } catch (e) { diag("header-clone-failed", String(e).slice(0, 100)); }
    }
    /* 兜底：照 4.0.0 原生标题的类名（原生标题还没渲染出来时用） */
    var fb = document.createElement("div");
    fb.className = "flex min-w-0 items-center gap-3";
    var l = document.createElement("span");
    l.className = "shrink-0 text-ui-base font-medium text-foreground";
    l.textContent = "自动压缩阈值";
    valEl = document.createElement("span");
    valEl.className = "ml-auto shrink-0 text-right font-mono text-ui-sm text-foreground-subtle";
    valEl.textContent = P + "%";
    fb.appendChild(l);
    fb.appendChild(valEl);
    labEl = l;
    fontOk = false;
    return fb;
  }
  function paint() {
    if (valEl && valEl.textContent !== P + "%") valEl.textContent = P + "%";
    if (slider) {
      var pct = (P - MIN) * 100 / (MAX - MIN);
      slider.style.background = "linear-gradient(to right," + FILL + " " + pct + "%, " + TRACK + " " + pct + "%)";
    }
  }
  function build(card) {
    ensureStyle();
    var sec = document.createElement("div");
    sec.className = ROW;
    sec.appendChild(buildHeader(card));
    slider = document.createElement("input");
    slider.type = "range";
    slider.min = String(MIN);
    slider.max = String(MAX);
    slider.step = "1";
    slider.value = String(P);
    slider.className = "acc-ac-range";
    slider.setAttribute("aria-label", "自动压缩阈值百分比");
    var timer = null;
    slider.addEventListener("input", function () {
      P = Number(slider.value);
      paint();
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () { window.__accSet(P); }, 250);   // 拖动停顿再落盘
    });
    sec.appendChild(slider);
    paint();
    return sec;
  }
  function clsOf(el) { try { return String(el.className || "").slice(0, 100); } catch (e) { return ""; } }
  function scan() {
    var cards;
    try { cards = document.querySelectorAll(HC); } catch (e) { return; }
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      var cls = c.classList;
      if (!cls || !cls.contains("!w-80")) { diag("hc-no-w80", clsOf(c)); continue; }
      if (!c.querySelector('[data-slot="progress"]')) { diag("hc-no-progress", clsOf(c)); continue; }
      var body = c.querySelector("div.space-y-3");
      if (!body) { diag("hc-no-body", clsOf(c)); continue; }
      if (body.querySelector("." + ROW)) { if (!fontOk) matchFonts(c); syncNativeLook(c); paint(); continue; }
      body.insertBefore(build(c), body.firstChild);
      if (!fontOk) matchFonts(c);
      syncNativeLook(c);
      send("/attach");
    }
  }
  scan();
  try {
    var obs = new MutationObserver(function () { setTimeout(scan, 50); });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    cleanups.push(function () { try { obs.disconnect(); } catch (e) { } });
  } catch (e) { }
  var iv = setInterval(scan, 2000);
  cleanups.push(function () { clearInterval(iv); });
})();
