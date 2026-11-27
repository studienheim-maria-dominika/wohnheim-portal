// Small behaviours shared by every page:
//  - keep what you typed when a form is rejected and you press "Back to the form"
//  - close the "More" menu on the phone tab bar
//  - refresh the "Right now" cards on the home page once a minute
(() => {
  "use strict";

  document.querySelectorAll(".js-back").forEach(btn => btn.addEventListener("click", () => history.back()));

  // ---- Form drafts (session only, never passwords, files or hidden fields) ----
  let store = null;
  try { store = window.sessionStorage; } catch (e) { store = null; }
  if (store) {
    const PREFIX = "portal-draft:";
    const navEntry = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
    const restoring = navEntry.type === "back_forward";
    if (!restoring) {
      Object.keys(store).filter(k => k.indexOf(PREFIX) === 0).forEach(k => store.removeItem(k));
    }
    const skipTypes = ["hidden", "file", "password", "submit", "button", "reset", "image"];
    const fieldsOf = form => Array.from(form.elements).filter(el => el.name && !el.disabled && skipTypes.indexOf(el.type) === -1);
    const forms = Array.from(document.querySelectorAll("main form")).filter(f =>
      (f.getAttribute("method") || "").toLowerCase() === "post" && !f.querySelector("input[type=password]"));
    forms.forEach((form, i) => {
      const key = PREFIX + location.pathname + location.search + "#" + i + ":" + (form.getAttribute("action") || "");
      if (restoring) {
        try {
          const saved = JSON.parse(store.getItem(key) || "null");
          const fields = fieldsOf(form);
          if (saved && saved.names.length === fields.length && saved.names.every((n, k) => n === fields[k].name)) {
            fields.forEach((el, k) => {
              if (el.type === "checkbox" || el.type === "radio") el.checked = Boolean(saved.values[k]);
              else el.value = saved.values[k];
              el.dispatchEvent(new Event("input", { bubbles: true }));
              el.dispatchEvent(new Event("change", { bubbles: true }));
            });
          }
        } catch (e) { /* ignore broken drafts */ }
      }
      form.addEventListener("submit", () => {
        try {
          const fields = fieldsOf(form);
          store.setItem(key, JSON.stringify({
            names: fields.map(el => el.name),
            values: fields.map(el => (el.type === "checkbox" || el.type === "radio") ? el.checked : el.value),
          }));
        } catch (e) { /* storage full or blocked */ }
      }, true);
    });
  }

  // ---- "More" menu on the phone tab bar ----
  const more = document.querySelector(".more-menu");
  if (more) {
    document.addEventListener("click", e => { if (more.open && !more.contains(e.target)) more.open = false; });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && more.open) { more.open = false; const s = more.querySelector("summary"); if (s) s.focus(); }
    });
  }

  // ---- "..." menus on notes: one open at a time, close on outside tap or Escape ----
  const noteMenus = Array.from(document.querySelectorAll(".note-menu"));
  if (noteMenus.length) {
    noteMenus.forEach(menu => menu.addEventListener("toggle", () => {
      if (menu.open) noteMenus.forEach(other => { if (other !== menu) other.open = false; });
    }));
    document.addEventListener("click", e => {
      noteMenus.forEach(menu => {
        if (!menu.open) return;
        const opensEditor = e.target.closest && e.target.closest("[popovertarget]");
        if (!menu.contains(e.target) || (opensEditor && menu.contains(opensEditor))) menu.open = false;
      });
    });
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      noteMenus.forEach(menu => { if (menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
    });
  }

  // ---- Home page: keep "Right now" fresh ----
  const live = document.querySelector(".live-grid");
  if (live) {
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const res = await fetch(location.href, { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) return;
        const doc = new DOMParser().parseFromString(await res.text(), "text/html");
        const fresh = doc.querySelector(".live-grid");
        if (fresh && fresh.innerHTML !== live.innerHTML) live.innerHTML = fresh.innerHTML;
      } catch (e) { /* offline: keep what is shown */ }
    };
    setInterval(refresh, 60000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }
})();
