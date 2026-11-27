async function compressImageFile(file, maxSide = 1000, quality = 0.78) {
  if (!file || file.size > 15000000) throw Error();
  const image = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.width * scale));
  canvas.height = Math.max(1, Math.round(image.height * scale));
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff"; // transparent PNGs become white instead of black
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  return canvas.toDataURL("image/jpeg", quality).split(",")[1];
}

// =========================================================
// 1. EXCHANGE SHELF UPLOAD FORM
// =========================================================
const shelfForm = document.getElementById("shelf-upload-form");
if (shelfForm) {
  const picker = document.getElementById("photo");
  const titleInput = shelfForm.querySelector('input[name="title"]');
  const descInput = shelfForm.querySelector('textarea[name="description"]');
  const submitBtn = document.getElementById("post-submit");
  const tagLabel = document.getElementById("tag-menu-label");

  const getTagBoxes = () => Array.from(shelfForm.querySelectorAll('input[name="tags"]'));

  const updateTagLabel = () => {
    if (!tagLabel) return;
    const chosen = getTagBoxes()
      .filter(b => b.checked)
      .map(b => b.parentElement.textContent.trim());
    tagLabel.textContent = chosen.length ? chosen.join(", ") : tagLabel.dataset.placeholder;
  };

  const updateShelfState = () => {
    const hasPhoto = Boolean(picker && picker.files && picker.files.length > 0);
    const hasTitle = Boolean(titleInput && titleInput.value.trim().length > 0);
    const hasTag = getTagBoxes().some(b => b.checked);
    if (submitBtn) submitBtn.disabled = !(hasPhoto && hasTitle && hasTag);
  };

  const syncShelfForm = () => {
    updateTagLabel();
    updateShelfState();
  };

  shelfForm.addEventListener("change", syncShelfForm);
  shelfForm.addEventListener("input", syncShelfForm);
  syncShelfForm();

  shelfForm.addEventListener("submit", async event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    if (descInput && descInput.value.trim().split(/\s+/).filter(Boolean).length > 100) {
      alert(isDe ? "Die Beschreibung darf maximal 100 Wörter lang sein." : "The description must be 100 words or fewer.");
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
      document.getElementById("image").value = await compressImageFile(picker.files[0]);
      const thumbField = document.getElementById("thumb");
      if (thumbField) {
        // small copy for the shelf grid and home page, so phones do not download full photos for thumbnails
        try { thumbField.value = await compressImageFile(picker.files[0], 420, 0.72); } catch { thumbField.value = ""; }
      }
      shelfForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      updateShelfState();
    }
  });
}

// =========================================================
// 2. ADMIN ADD & EDIT GUEST ROOM FORMS
// =========================================================
const roomForm = document.getElementById("add-room-form");
if (roomForm) {
  const roomPhoto = document.getElementById("room-photo");
  const roomFloor = document.getElementById("room-floor");
  const roomMax = document.getElementById("room-max");
  const roomPrice = document.getElementById("room-price");
  const roomPreview = document.getElementById("room-preview-text");
  const roomSubmit = document.getElementById("room-submit");

  const updateRoomState = () => {
    const fl = roomFloor.value.trim();
    const mx = parseInt(roomMax.value, 10);
    const pr = parseFloat(roomPrice.value);
    const hasPhoto = roomPhoto.files && roomPhoto.files.length > 0;
    const valid = hasPhoto && fl.length > 0 && mx >= 1 && mx <= 20 && !Number.isNaN(pr) && pr >= 0;
    roomSubmit.disabled = !valid;

    const isDe = document.documentElement.lang === "de";
    if (fl && mx >= 1 && !Number.isNaN(pr) && pr >= 0) {
      const pStr = Number.isInteger(pr) ? `${pr} €` : `${pr.toFixed(2)} €`;
      roomPreview.textContent = fl;
      document.getElementById("room-preview-sub").textContent =
        `Max. ${mx} ${isDe ? "Gäste" : "guests"} · ${pStr}/${isDe ? "Nacht" : "night"}`;
    } else {
      roomPreview.textContent = fl || "—";
      document.getElementById("room-preview-sub").textContent = "";
    }
  };

  [roomPhoto, roomFloor, roomMax, roomPrice].forEach(el => {
    if (!el) return;
    el.addEventListener("input", updateRoomState);
    el.addEventListener("change", updateRoomState);
  });
  updateRoomState();

  roomForm.addEventListener("submit", async event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    roomSubmit.disabled = true;
    try {
      document.getElementById("room-image").value = await compressImageFile(roomPhoto.files[0]);
      roomForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      updateRoomState();
    }
  });
}

document.querySelectorAll(".edit-room-form").forEach(editForm => {
  editForm.addEventListener("submit", async event => {
    const fileInput = editForm.querySelector(".edit-room-photo");
    const hiddenInput = editForm.querySelector('.edit-room-image, input[name="image"]');
    if (!fileInput || !hiddenInput || !fileInput.files || fileInput.files.length === 0) return;

    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    const btn = editForm.querySelector('button[type="submit"], button:not([type])');
    if (btn) btn.disabled = true;
    try {
      hiddenInput.value = await compressImageFile(fileInput.files[0]);
      editForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      if (btn) btn.disabled = false;
    }
  });
});

// =========================================================
// 3. GUEST ROOM CALENDAR & LIVE PRICE CALCULATOR
// =========================================================
const calEl = document.getElementById("room-calendar");
if (calEl) {
  const statusMap = JSON.parse(calEl.dataset.status || "{}");
  const todayStr = calEl.dataset.today;
  const isDe = calEl.dataset.lang === "de";
  const pricePerNight = parseFloat(calEl.dataset.price || "0");
  const beddingFee = parseFloat(calEl.dataset.beddingFee || "10");

  const arrivalInput = document.getElementById("book-arrival");
  const departureInput = document.getElementById("book-departure");
  const guestsInput = document.getElementById("book-guests");
  const beddingInput = document.getElementById("book-bedding");
  const nameInput = document.getElementById("book-name");
  const notesInput = document.getElementById("book-notes");
  const totalDisplay = document.getElementById("total-price-display");
  const staySummary = document.getElementById("stay-summary");
  const submitBtn = document.getElementById("booking-submit");

  const monthLabel = document.getElementById("cal-month-label");
  const weekdaysEl = document.getElementById("cal-weekdays");
  const daysEl = document.getElementById("cal-days");

  const parseIso = s => {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
  };
  const toIso = dt => {
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const d = String(dt.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };
  const fmtDate = iso => {
    if (!iso) return "";
    const p = String(iso).slice(0, 10).split("-");
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : iso;
  };
  const addDays = (iso, n) => {
    const dt = parseIso(iso);
    dt.setDate(dt.getDate() + n);
    return toIso(dt);
  };

  // Check if all nights in [arr, dep) are free and >= today
  const rangeIsFree = (arr, dep) => {
    if (!arr || !dep || arr < todayStr || dep <= arr) return false;
    let cur = arr;
    while (cur < dep) {
      if (statusMap[cur]) return false;
      cur = addDays(cur, 1);
    }
    return true;
  };

  const countNights = (arr, dep) => {
    if (!arr || !dep || dep <= arr) return 0;
    return Math.round((parseIso(dep) - parseIso(arr)) / 86400000);
  };

  const initDate = parseIso(todayStr);
  let viewYear = initDate.getFullYear();
  let viewMonth = initDate.getMonth();

  const weekdayNames = isDe
    ? ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  weekdaysEl.innerHTML = weekdayNames.map(w => `<span>${w}</span>`).join("");

  const renderCalendar = () => {
    const monthFmt = new Intl.DateTimeFormat(isDe ? "de-DE" : "en-US", { month: "long", year: "numeric" });
    monthLabel.textContent = monthFmt.format(new Date(viewYear, viewMonth, 1));

    const firstDay = new Date(viewYear, viewMonth, 1);
    const startOffset = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();

    const arr = arrivalInput.value;
    const dep = departureInput.value;
    const selectingCheckout = Boolean(arr && !dep);

    let html = "";
    for (let i = 0; i < startOffset; i++) {
      html += '<span class="cal-empty"></span>';
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const iso = toIso(new Date(viewYear, viewMonth, day));
      const st = statusMap[iso];
      const isPast = iso < todayStr;
      const isArr = iso === arr;
      const isDep = iso === dep;
      const isInRange = arr && dep && iso >= arr && iso < dep;

      const canBeCheckout = selectingCheckout && iso > arr && rangeIsFree(arr, iso);
      const canBeCheckin = !isPast && !st;

      let cls = "cal-day";
      if (isPast) cls += " past";
      else if (st === "approved") cls += " occupied";
      else if (st === "pending") cls += " pending";
      else cls += " free";

      if (isArr) cls += " selected-start";
      if (isDep) cls += " selected-end";
      if (isInRange) cls += " in-range";
      if (canBeCheckout && st) cls += " checkout-allowed";

      const clickable = canBeCheckout || canBeCheckin;
      html += `<button type="button" class="${cls}" data-date="${iso}"${clickable ? "" : " disabled"}>${day}</button>`;
    }
    daysEl.innerHTML = html;
  };

  const updateBookingSummary = () => {
    const arr = arrivalInput.value;
    const dep = departureInput.value;
    const validRange = rangeIsFree(arr, dep);
    const nights = validRange ? countNights(arr, dep) : 0;
    const beddingAdd = beddingInput && beddingInput.checked ? beddingFee : 0;
    const total = nights > 0 ? nights * pricePerNight + beddingAdd : 0;

    totalDisplay.textContent = Number.isInteger(total) ? `${total} €` : `${total.toFixed(2)} €`;

    if (nights > 0) {
      const unit = nights === 1 ? staySummary.dataset.night : staySummary.dataset.nights;
      staySummary.textContent = `${staySummary.dataset.prefix}: ${fmtDate(arr)} → ${fmtDate(dep)} (${nights} ${unit})`;
      staySummary.classList.remove("invalid-text");
    } else if (arr && dep) {
      staySummary.textContent = isDe
        ? "Ungültiger Zeitraum oder belegte Nacht enthalten."
        : "Invalid range or includes an unavailable night.";
      staySummary.classList.add("invalid-text");
    } else if (arr) {
      staySummary.textContent = isDe
        ? `Anreise: ${fmtDate(arr)} — bitte Abreisedatum wählen.`
        : `Check-in: ${fmtDate(arr)} — please select a check-out date.`;
      staySummary.classList.remove("invalid-text");
    } else {
      staySummary.textContent = "";
    }

    const guestsVal = parseInt(guestsInput.value, 10);
    const maxGuests = parseInt(guestsInput.max, 10);
    const hasName = nameInput.value.trim().length > 0;
    const wordsOk = !notesInput || notesInput.value.trim().split(/\s+/).filter(Boolean).length <= 100;

    submitBtn.disabled = !(validRange && guestsVal >= 1 && guestsVal <= maxGuests && hasName && wordsOk);
    renderCalendar();
  };

  daysEl.addEventListener("click", event => {
    const btn = event.target.closest(".cal-day");
    if (!btn || btn.disabled) return;
    const clicked = btn.dataset.date;
    const arr = arrivalInput.value;
    const dep = departureInput.value;

    if (!arr || dep || clicked <= arr) {
      if (!statusMap[clicked] && clicked >= todayStr) {
        arrivalInput.value = clicked;
        departureInput.value = "";
      }
    } else if (rangeIsFree(arr, clicked)) {
      departureInput.value = clicked;
    } else if (!statusMap[clicked]) {
      arrivalInput.value = clicked;
      departureInput.value = "";
    }
    updateBookingSummary();
  });

  document.getElementById("cal-prev").addEventListener("click", () => {
    viewMonth -= 1;
    if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
    renderCalendar();
  });

  document.getElementById("cal-next").addEventListener("click", () => {
    viewMonth += 1;
    if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
    renderCalendar();
  });

  [arrivalInput, departureInput, guestsInput, beddingInput, nameInput, notesInput].forEach(el => {
    if (!el) return;
    el.addEventListener("input", updateBookingSummary);
    el.addEventListener("change", updateBookingSummary);
  });

  updateBookingSummary();
}
// =========================================================
// 4. WASHING MACHINES: DEFAULT TIME, END PREVIEW & COUNTDOWN
// =========================================================
const nowTimeStr = () => {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

document.querySelectorAll(".machine-start-form").forEach(mForm => {
  const startIn = mForm.querySelector(".m-start-input");
  const hrsIn = mForm.querySelector(".m-hours-input");
  const minsIn = mForm.querySelector(".m-mins-input");
  const preview = mForm.querySelector(".m-end-preview");
  const btn = mForm.querySelector(".m-start-submit");

  if (startIn && !startIn.value) {
    startIn.value = nowTimeStr();
  }

  const updateMachinePreview = () => {
    const h = parseInt(hrsIn.value || "0", 10);
    const m = parseInt(minsIn.value || "0", 10);
    const totalMins = h * 60 + m;
    const valid = Boolean(startIn.value) && h >= 0 && h <= 12 && m >= 0 && m <= 59 && totalMins >= 1 && totalMins <= 720;
    if (btn) btn.disabled = !valid;
    if (!preview) return;
    if (!valid) {
      preview.textContent = "";
      return;
    }
    const [sh, sm] = startIn.value.split(":").map(Number);
    const endTotal = (sh * 60 + sm + totalMins) % 1440;
    const eh = String(Math.floor(endTotal / 60)).padStart(2, "0");
    const em = String(endTotal % 60).padStart(2, "0");
    preview.textContent = `${preview.dataset.prefix}: ${eh}:${em}`;
  };

  [startIn, hrsIn, minsIn].forEach(el => {
    if (!el) return;
    el.addEventListener("input", updateMachinePreview);
    el.addEventListener("change", updateMachinePreview);
  });
  updateMachinePreview();
});

const countdownEls = Array.from(document.querySelectorAll(".m-countdown[data-until]"));
if (countdownEls.length > 0) {
  let reloaded = false;
  const tickCountdowns = () => {
    const nowSec = Math.floor(Date.now() / 1000);
    countdownEls.forEach(el => {
      const target = parseInt(el.dataset.until || "0", 10);
      const diff = target - nowSec;
      if (diff <= 0) {
        el.textContent = `${el.dataset.prefix}: 00:00`;
        if (!reloaded) {
          reloaded = true;
          setTimeout(() => window.location.reload(), 900);
        }
        return;
      }
      const hrs = Math.floor(diff / 3600);
      const mins = Math.floor((diff % 3600) / 60);
      const secs = diff % 60;
      const fmt = hrs > 0
        ? `${hrs}h ${String(mins).padStart(2, "0")}m ${String(secs).padStart(2, "0")}s`
        : `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
      el.textContent = `${el.dataset.prefix}: ${fmt}`;
    });
  };
  tickCountdowns();
  setInterval(tickCountdowns, 1000);
}

// =========================================================
// 5. HOUSE RULES PDF UPLOAD
// =========================================================
const activePortalRole = (document.body && document.body.dataset && document.body.dataset.role) || "";

const pdfForm = document.getElementById("rules-pdf-form");
if (pdfForm) {
  const pdfInput = document.getElementById("rules-pdf-input") || document.getElementById("rules-pdf-file");
  const pdfB64 = document.getElementById("rules-pdf-b64");
  const pdfName = document.getElementById("rules-pdf-filename") || document.getElementById("rules-pdf-name");
  const pdfSubmit = document.getElementById("rules-pdf-submit");

  if (pdfInput && pdfSubmit) {
    pdfInput.addEventListener("change", () => {
      const file = pdfInput.files && pdfInput.files[0];
      pdfSubmit.disabled = !(file && file.size > 0 && file.size <= 8 * 1024 * 1024);
    });
  }

  pdfForm.addEventListener("submit", event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    const file = pdfInput && pdfInput.files && pdfInput.files[0];
    if (!file || file.size > 8 * 1024 * 1024) {
      alert(isDe ? "Bitte wähle eine gültige PDF-Datei unter 8 MB." : "Please select a valid PDF file under 8 MB.");
      return;
    }
    if (pdfSubmit) pdfSubmit.disabled = true;
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      pdfB64.value = result.includes(",") ? result.split(",")[1] : result;
      if (pdfName) pdfName.value = file.name || "Hausordnung.pdf";
      pdfForm.submit();
    };
    reader.onerror = () => {
      alert(isDe ? "PDF konnte nicht gelesen werden." : "Could not read PDF file.");
      if (pdfSubmit) pdfSubmit.disabled = false;
    };
    reader.readAsDataURL(file);
  });
}

// =========================================================
// 6. ACTIVITIES, MINI-ACTIVITIES & BUDGET TRACKER
// =========================================================
const actForm = document.getElementById("add-activity-form");
if (actForm) {
  const actPhoto = actForm.querySelector('input[type="file"]');
  const actTitle = actForm.querySelector('input[name="title"], input[name="name"]');
  const actImage = actForm.querySelector('input[name="image"]');
  const actSubmit = actForm.querySelector('button[type="submit"], button:not([type])');

  const updateActState = () => {
    const ok = Boolean(actPhoto && actPhoto.files && actPhoto.files.length > 0 && actTitle && actTitle.value.trim().length > 0);
    if (actSubmit) actSubmit.disabled = !ok;
  };

  actForm.addEventListener("input", updateActState);
  actForm.addEventListener("change", updateActState);
  updateActState();

  actForm.addEventListener("submit", async event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    if (actSubmit) actSubmit.disabled = true;
    try {
      actImage.value = await compressImageFile(actPhoto.files[0]);
      actForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      updateActState();
    }
  });
}

const miniForm = document.getElementById("add-mini-form");
if (miniForm) {
  const miniPhoto = miniForm.querySelector('input[type="file"]');
  const miniTitle = miniForm.querySelector('input[name="title"], input[name="name"]');
  const miniCap = miniForm.querySelector('input[name="max_participants"]');
  const miniImage = miniForm.querySelector('input[name="image"]');
  const miniSubmit = miniForm.querySelector('button[type="submit"], button:not([type])');

  const updateMiniState = () => {
    const capVal = miniCap ? parseInt(miniCap.value, 10) : 1;
    const ok = Boolean(
      miniPhoto && miniPhoto.files && miniPhoto.files.length > 0 &&
      miniTitle && miniTitle.value.trim().length > 0 &&
      !Number.isNaN(capVal) && capVal >= 1 && capVal <= 500
    );
    if (miniSubmit) miniSubmit.disabled = !ok;
  };

  miniForm.addEventListener("input", updateMiniState);
  miniForm.addEventListener("change", updateMiniState);
  updateMiniState();

  miniForm.addEventListener("submit", async event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    if (miniSubmit) miniSubmit.disabled = true;
    try {
      miniImage.value = await compressImageFile(miniPhoto.files[0]);
      miniForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      updateMiniState();
    }
  });
}

// Big-Activity Photo Gallery Upload
const galForm = document.getElementById("act-gallery-form");
if (galForm) {
  const galPhoto = document.getElementById("gal-photo");
  const galImage = document.getElementById("gal-image");
  const galSubmit = document.getElementById("gal-submit");

  const updateGalState = () => {
    const ok = Boolean(galPhoto && galPhoto.files && galPhoto.files.length > 0);
    if (galSubmit) galSubmit.disabled = !ok;
  };

  galForm.addEventListener("change", updateGalState);
  updateGalState();

  galForm.addEventListener("submit", async event => {
    event.preventDefault();
    const isDe = document.documentElement.lang === "de";
    if (galSubmit) galSubmit.disabled = true;
    try {
      galImage.value = await compressImageFile(galPhoto.files[0]);
      galForm.submit();
    } catch {
      alert(isDe ? "Bitte wähle ein JPEG-, PNG- oder WebP-Foto unter 15 MB." : "Please choose a JPEG, PNG, or WebP photo under 15 MB.");
      updateGalState();
    }
  });
}

// Expense Logger: Dynamic Itemized Rows, Live Calculation & Receipt Compression
const trackerForm = document.getElementById("tracker-log-form");
if (trackerForm) {
  const itemsRowsEl = document.getElementById("tracker-item-rows");
  const addItemBtn = document.getElementById("tracker-add-item-btn");
  const tookOutInput = document.getElementById("tracker-took-out");
  const putBackInput = document.getElementById("tracker-put-back");
  const formulaBar = document.getElementById("tracker-formula-preview");
  const itemsJsonInput = document.getElementById("tracker-items-json");
  const receiptsJsonInput = document.getElementById("tracker-receipts-json");
  const receiptInput = document.getElementById("tracker-receipt-input");
  const receiptPreviews = document.getElementById("tracker-receipt-previews");
  const logSubmitBtn = document.getElementById("tracker-log-submit");

  let compressedReceipts = [];

  const parseNum = raw => {
    const cleaned = String(raw || "").replace("€", "").trim().replace(",", ".");
    if (!cleaned) return 0;
    const val = parseFloat(cleaned);
    return Number.isFinite(val) && val >= 0 ? val : 0;
  };

  const fmtEuroComma = num => {
    const val = Number.isFinite(num) ? num : 0;
    return `${val.toFixed(2).replace(".", ",")} €`;
  };

  const syncTrackerExpense = () => {
    const took = parseNum(tookOutInput && tookOutInput.value);
    const back = parseNum(putBackInput && putBackInput.value);
    const fundDelta = back - took;

    const itemsList = [];
    let itemsSum = 0;
    if (itemsRowsEl) {
      itemsRowsEl.querySelectorAll(".tracker-item-row").forEach(row => {
        const nameInp = row.querySelector(".tracker-item-name");
        const amtInp = row.querySelector(".tracker-item-amt");
        const nm = nameInp ? nameInp.value.trim() : "";
        const amt = parseNum(amtInp && amtInp.value);
        if (nm || amt > 0) {
          itemsList.push({ name: nm, amount: Math.round(amt * 100) / 100 });
        }
        itemsSum += amt;
      });
    }

    if (itemsJsonInput) {
      itemsJsonInput.value = JSON.stringify(itemsList);
    }

    if (formulaBar) {
      const chgLbl = formulaBar.dataset.changeLbl || "Fund change";
      const itmLbl = formulaBar.dataset.itemsLbl || "Itemized total";
      const sign = fundDelta > 0 ? "+" : (fundDelta < 0 ? "-" : "");
      const deltaStr = `${sign}${fmtEuroComma(Math.abs(fundDelta))}`;
      formulaBar.innerHTML =
        `<span>${chgLbl}: <strong>${deltaStr}</strong></span>` +
        `<span> | </span>` +
        `<span>${itmLbl}: <strong>${fmtEuroComma(itemsSum)}</strong></span>`;
    }
  };

  if (addItemBtn && itemsRowsEl) {
    addItemBtn.addEventListener("click", () => {
      const ph = itemsRowsEl.dataset.placeholder || "Item / description";
      const row = document.createElement("div");
      row.className = "tracker-item-row";
      row.innerHTML =
        `<input type="text" class="tracker-item-name" maxlength="80" placeholder="${ph}">` +
        `<input type="text" inputmode="decimal" class="tracker-item-amt" placeholder="0,00">` +
        `<button type="button" class="btn-secondary tracker-remove-item" aria-label="Remove">×</button>`;
      itemsRowsEl.appendChild(row);
      syncTrackerExpense();
    });

    itemsRowsEl.addEventListener("click", event => {
      const rmBtn = event.target.closest(".tracker-remove-item");
      if (!rmBtn) return;
      const row = rmBtn.closest(".tracker-item-row");
      if (row) row.remove();
      syncTrackerExpense();
    });
  }

  if (receiptInput) {
    receiptInput.addEventListener("change", async () => {
      const isDe = document.documentElement.lang === "de";
      compressedReceipts = [];
      if (receiptPreviews) receiptPreviews.innerHTML = "";
      if (receiptsJsonInput) receiptsJsonInput.value = "[]";

      const files = Array.from(receiptInput.files || []);
      if (!files.length) return;
      if (files.length > 4) {
        alert(isDe ? "Maximal 4 Belegfotos pro Eintrag erlaubt." : "Maximum 4 receipt photos allowed per entry.");
        receiptInput.value = "";
        return;
      }

      if (logSubmitBtn) logSubmitBtn.disabled = true;
      try {
        for (const file of files) {
          const b64 = await compressImageFile(file);
          compressedReceipts.push(b64);
          if (receiptPreviews) {
            const box = document.createElement("div");
            box.className = "tracker-thumb-btn";
            const img = document.createElement("img");
            img.src = `data:image/jpeg;base64,${b64}`;
            img.alt = "Receipt";
            box.appendChild(img);
            receiptPreviews.appendChild(box);
          }
        }
        if (receiptsJsonInput) {
          receiptsJsonInput.value = JSON.stringify(compressedReceipts);
        }
      } catch {
        compressedReceipts = [];
        if (receiptPreviews) receiptPreviews.innerHTML = "";
        if (receiptsJsonInput) receiptsJsonInput.value = "[]";
        receiptInput.value = "";
        alert(isDe ? "Bitte wähle gültige JPEG-, PNG- oder WebP-Fotos unter 15 MB." : "Please choose valid JPEG, PNG, or WebP photos under 15 MB.");
      } finally {
        if (logSubmitBtn) logSubmitBtn.disabled = false;
      }
    });
  }

  trackerForm.addEventListener("input", syncTrackerExpense);
  trackerForm.addEventListener("change", syncTrackerExpense);
  syncTrackerExpense();

  trackerForm.addEventListener("submit", () => {
    syncTrackerExpense();
    if (receiptsJsonInput) {
      receiptsJsonInput.value = JSON.stringify(compressedReceipts);
    }
  });
}

// View-Only Receipt & Gallery Lightbox Modal
const receiptModal = document.getElementById("receipt-modal");
const receiptModalImg = document.getElementById("receipt-modal-img");
const receiptModalCaption = document.getElementById("receipt-modal-caption");
const receiptModalClose = document.getElementById("receipt-modal-close");

if (receiptModal && receiptModalImg) {
  const closeModal = () => {
    receiptModal.classList.add("hidden");
    receiptModalImg.src = "";
    if (receiptModalCaption) receiptModalCaption.textContent = "";
  };

  document.addEventListener("click", event => {
    const trigger = event.target.closest(".js-lightbox-trigger");
    if (trigger) {
      const photoId = document.getElementById("gallery-delete-id");
      if (photoId) photoId.value = trigger.dataset.photoId;
      receiptModalImg.src = trigger.dataset.src || "";
      if (receiptModalCaption) {
        receiptModalCaption.textContent = trigger.dataset.caption || "";
      }
      receiptModal.classList.remove("hidden");
      return;
    }
    if (event.target === receiptModal || event.target === receiptModalClose) {
      closeModal();
    }
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !receiptModal.classList.contains("hidden")) {
      closeModal();
    }
  });
}