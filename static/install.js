// "Install the app" page: draws the QR code for this site's address and offers the install button where the browser allows it.
(() => {
  "use strict";
  const url = location.origin + "/app";
  const shown = location.host;
  const draw = id => {
    const el = document.getElementById(id);
    if (!el || typeof qrcode !== "function") return;
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    el.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  };
  draw("install-qr");
  draw("poster-qr");
  ["install-url", "poster-url"].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = shown; });

  const printBtn = document.getElementById("install-print");
  if (printBtn) printBtn.addEventListener("click", () => window.print());

  // Android / desktop Chrome: show our own "Install now" button.
  const btn = document.getElementById("install-btn");
  const done = document.getElementById("install-done");
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone && done) done.hidden = false;
  let deferred = null;
  window.addEventListener("beforeinstallprompt", e => {
    e.preventDefault();
    deferred = e;
    if (btn) btn.hidden = false;
  });
  if (btn) btn.addEventListener("click", async () => {
    if (!deferred) return;
    deferred.prompt();
    try { await deferred.userChoice; } catch (e) { /* dismissed */ }
    deferred = null;
    btn.hidden = true;
  });
  window.addEventListener("appinstalled", () => { if (btn) btn.hidden = true; if (done) done.hidden = false; });
})();
