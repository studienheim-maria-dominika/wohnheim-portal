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

// Installable app: register the service worker (only works over https or on this PC).
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "127.0.0.1" || location.hostname === "localhost")) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}

// Tab bar: mark the tapped tab immediately so the app feels instant while the next page loads.
document.querySelectorAll(".tabbar a.tab").forEach(tab => tab.addEventListener("click", e => {
  if (e.metaKey || e.ctrlKey || e.shiftKey || tab.getAttribute("aria-current") === "page") return;
  const bar = tab.closest(".tabbar");
  bar.querySelectorAll(".going").forEach(t => t.classList.remove("going"));
  bar.classList.add("navigating");
  tab.classList.add("going");
}));
// Coming back with the browser's back button: undo that marking.
window.addEventListener("pageshow", e => {
  if (!e.persisted) return;
  document.querySelectorAll(".tabbar").forEach(bar => bar.classList.remove("navigating"));
  document.querySelectorAll(".tab.going").forEach(t => t.classList.remove("going"));
});

// ---- Photos in notes: shrink on the phone before upload, show previews, open large on tap ----
(() => {
  const MAX = 4;
  const de = document.documentElement.lang === "de";
  const shrink = async (file, maxSide, quality) => {
    if (!file || file.size > 25000000) throw Error();
    const image = await createImageBitmap(file);
    const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    if (image.close) image.close();
    return canvas.toDataURL("image/jpeg", quality).split(",")[1];
  };

  document.querySelectorAll("form .note-photo-input").forEach(input => {
    const form = input.form;
    const preview = form.querySelector(".note-photo-preview");
    const body = form.querySelector('textarea[name="body"]');
    const submit = form.querySelector("button:not([type=button])");
    const sync = () => {
      if (input.files.length > MAX) {
        alert(de ? `Bitte höchstens ${MAX} Fotos auswählen.` : `Please choose at most ${MAX} photos.`);
        input.value = "";
      }
      if (body) body.required = !input.files.length;   // a note can be just photos
      if (preview) {
        preview.querySelectorAll("img").forEach(img => URL.revokeObjectURL(img.src));
        preview.innerHTML = "";
        Array.from(input.files).forEach(file => {
          const img = document.createElement("img");
          img.alt = "";
          img.src = URL.createObjectURL(file);
          preview.appendChild(img);
        });
      }
    };
    input.addEventListener("change", sync);

    form.addEventListener("submit", async event => {
      if (!input.files.length || form.dataset.photosReady) return;
      event.preventDefault();
      if (submit) submit.disabled = true;
      form.querySelectorAll('input[name="photos"], input[name="thumbs"]').forEach(el => el.remove());
      try {
        for (const file of Array.from(input.files).slice(0, MAX)) {
          for (const [name, side, q] of [["photos", 1400, 0.8], ["thumbs", 480, 0.72]]) {
            const field = document.createElement("input");
            field.type = "hidden";
            field.name = name;
            field.value = await shrink(file, side, q);
            form.appendChild(field);
          }
        }
        form.dataset.photosReady = "1";
        form.submit();
      } catch (e) {
        alert(de ? "Bitte wähle JPEG-, PNG- oder WebP-Fotos." : "Please choose JPEG, PNG or WebP photos.");
        if (submit) submit.disabled = false;
      }
    });
  });

  // Tap a photo in a note: show it large; tap anywhere or press Escape to close.
  const links = document.querySelectorAll("a.note-photo");
  if (links.length && window.HTMLDialogElement) {
    const viewer = document.createElement("dialog");
    viewer.className = "photo-viewer";
    viewer.innerHTML = '<img alt="">';
    document.body.appendChild(viewer);
    viewer.addEventListener("click", () => viewer.close());
    links.forEach(link => link.addEventListener("click", e => {
      e.preventDefault();
      viewer.querySelector("img").src = link.href;
      viewer.showModal();
    }));
  }
})();
