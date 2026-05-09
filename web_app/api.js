(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  function applyTheme() {
    if (!tg || !tg.themeParams) return;
    const t = tg.themeParams;
    const root = document.documentElement.style;
    if (t.bg_color) root.setProperty("--bg", t.bg_color);
    if (t.secondary_bg_color) root.setProperty("--card", t.secondary_bg_color);
    if (t.text_color) root.setProperty("--text", t.text_color);
    if (t.hint_color) root.setProperty("--muted", t.hint_color);
    if (t.link_color || t.button_color)
      root.setProperty("--accent", t.link_color || t.button_color);
    try {
      if (tg.setHeaderColor && t.bg_color) tg.setHeaderColor(t.bg_color);
      if (tg.setBackgroundColor && t.bg_color) tg.setBackgroundColor(t.bg_color);
    } catch (e) {}
    try {
      if (tg.MainButton && tg.MainButton.hide) tg.MainButton.hide();
    } catch (e) {}
  }

  applyTheme();
  try {
    if (tg && typeof tg.onEvent === "function") {
      tg.onEvent("themeChanged", applyTheme);
    }
  } catch (e) {}

  function initDataHeader() {
    const initData = tg && tg.initData ? tg.initData : "";
    return initData;
  }

  function hdr() {
    const initData = initDataHeader();
    return {
      "X-Telegram-Init-Data": initData,
      Accept: "application/json",
    };
  }

  function checkInitData() {
    if (!initDataHeader()) {
      throw new Error(
        "Откройте мини-приложение из Telegram (кнопка «Мини-приложение» или команда /webapp), затем нажмите «Открыть приложение»."
      );
    }
  }

  async function readError(r) {
    const t = await r.text();
    if (r.status === 401) {
      if (t.includes("not registered")) {
        return "Сначала нажмите /start в чате с ботом.";
      }
      if (t.includes("no init")) {
        return "Нет подписи Telegram. Закройте окно и откройте приложение снова из бота.";
      }
      if (t.includes("invalid hash")) {
        return "Подпись устарела. Закройте мини-приложение и откройте снова из бота.";
      }
      if (t.includes("expired")) {
        return "Сессия устарела. Закройте окно и откройте приложение снова.";
      }
    }
    return t || r.statusText || "Ошибка запроса";
  }

  async function apiGet(path) {
    checkInitData();
    const r = await fetch(path, { headers: hdr() });
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
  }

  async function apiPost(path, body) {
    checkInitData();
    const r = await fetch(path, {
      method: "POST",
      headers: { ...hdr(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
  }

  async function apiDelete(path) {
    checkInitData();
    const r = await fetch(path, { method: "DELETE", headers: hdr() });
    if (!r.ok) throw new Error(await readError(r));
    const ct = (r.headers.get("Content-Type") || "").toLowerCase();
    if (ct.indexOf("application/json") >= 0) {
      return r.json();
    }
    return { ok: true };
  }

  async function downloadWithAuth(path) {
    checkInitData();
    const r = await fetch(path, { headers: hdr() });
    if (!r.ok) throw new Error(await readError(r));
    return r.blob();
  }

  async function apiUploadFile(path, file) {
    checkInitData();
    const initData = initDataHeader();
    const name = file.name || "file";
    const url =
      path +
      (path.indexOf("?") >= 0 ? "&" : "?") +
      "filename=" +
      encodeURIComponent(name);
    const buf = await file.arrayBuffer();
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "X-Telegram-Init-Data": initData,
        "Content-Type": "application/octet-stream",
      },
      body: buf,
    });
    if (!r.ok) throw new Error(await readError(r));
    return r.json();
  }

  window.SA = {
    tg,
    applyTheme,
    apiGet,
    apiPost,
    apiDelete,
    apiUploadFile,
    downloadWithAuth,
    needGroupBanner:
      '<p class="need-group"><strong>Группа не выбрана.</strong> В боте нажмите «Ввести код группы» или отправьте /join КОД от старосты.</p>',
  };
})();
