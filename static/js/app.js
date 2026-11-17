// Small UI helpers shared by every page. Event delegation only, so everything keeps
// working after hx-boost swaps the <body> (no re-binding needed). No frameworks.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ---------- global search: "/" focuses, Esc clears, click outside closes (FR-18.1) ----------
document.addEventListener("keydown", (e) => {
  const box = document.getElementById("gsearch");
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (box && e.key === "/" && !typing) { e.preventDefault(); box.focus(); }
  if (box && e.key === "Escape" && document.activeElement === box) {
    box.value = "";
    document.getElementById("search-results").innerHTML = "";
    box.blur();
  }
  if (e.key === "Escape" && $("[data-modal]")) closeModal();
  // Enter in the search box opens the first result (FR-18.3)
  if (box && e.key === "Enter" && document.activeElement === box) {
    const first = $("#search-results a");
    if (first) { e.preventDefault(); first.click(); }
  }
});

function closeModal() { const m = document.getElementById("modal"); if (m) m.innerHTML = ""; }

document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-search]")) {
    const results = document.getElementById("search-results");
    if (results) results.innerHTML = "";
  }
  // Filter panel: close when clicking outside it
  $$("details[data-filters][open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });

  // Pop-up: × button or the dark backdrop
  if (e.target.closest("[data-close-modal]") || e.target.matches("[data-modal]")) closeModal();

  // Filter chip ×: clear that field and re-submit the filter form
  const chip = e.target.closest("[data-clear-filter]");
  if (chip) setFilter(chip.dataset.clearFilter, "");
  const tab = e.target.closest("[data-set-filter]");
  if (tab) setFilter(tab.dataset.setFilter, tab.dataset.value);
  if (e.target.closest("[data-clear-all]")) {
    const form = document.getElementById("filters");
    if (form) {
      $$("select, input:not([type=search])", form).forEach((f) => { if (f.name) f.value = ""; });
      form.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  if (e.target.closest("[data-print]")) window.print();

  // Opening WhatsApp: remember it so we can ask "Did you send it?" when the user comes back (FR-11.3)
  const wa = e.target.closest("[data-wa-open]");
  if (wa) { try { sessionStorage.setItem("wa-open", wa.dataset.waOpen); } catch (_) {} }
});

function setFilter(name, value) {
  const form = document.getElementById("filters");
  const field = form && form.elements[name];
  if (field) { field.value = value; field.dispatchEvent(new Event("change", { bubbles: true })); }
}

// Confirm before destructive actions: <button data-confirm="Remove GAMCA Medical details?">
document.addEventListener("htmx:confirm", (e) => {
  const msg = e.detail.elt.dataset && e.detail.elt.dataset.confirm;
  if (!msg) return;
  e.preventDefault();
  if (window.confirm(msg)) e.detail.issueRequest(true);
});
// plain (boosted) forms with a data-confirm submit button
document.addEventListener("submit", (e) => {
  const btn = e.submitter;
  if (btn && btn.dataset.confirm && !btn.hasAttribute("hx-post") && !window.confirm(btn.dataset.confirm)) e.preventDefault();
}, true);

// ---------- "Did you send it?" after coming back from WhatsApp (FR-11.3) ----------
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  let id = null;
  try { id = sessionStorage.getItem("wa-open"); sessionStorage.removeItem("wa-open"); } catch (_) {}
  if (!id || !window.htmx) return;
  setTimeout(() => {
    if (window.confirm("Did you send the WhatsApp message?")) {
      htmx.ajax("POST", `/app/messages/${id}/sent`, { target: `#msg-${id}`, swap: "outerHTML" }).catch(() => {});
      const row = document.getElementById(`msg-${id}`);
      if (!row) setTimeout(() => location.reload(), 300);
    }
  }, 300);
});

// ---------- registration / edit form (FR-2.2 – 2.8, FR-14.1) ----------
const ALL_PROCESS = ["medical", "skill_test", "mofa", "vfs", "stamping", "contract", "emigration", "ticket"];
const LABELS = { medical: "GAMCA Medical", skill_test: "Skill Test", mofa: "MOFA", vfs: "VFS Appointment", stamping: "Visa stamping",
  contract: "Employment contract", emigration: "Emigration", ticket: "Ticket", umrah: "Umrah package", family: "Family visit details" };
let undoState = null, undoTimer = null;

function regForm(el) { return el && el.closest("[data-reg-form]"); }
function sectionOf(form, key) { return $(`[data-section="${key}"]`, form); }
function boxOf(form, key) { return $(`[data-svc="${key}"]`, form); }
function sectionHasData(sec) {
  return $$("input, select", sec).some((i) => !i.hasAttribute("data-cost") && i.value && i.value !== "Pending");
}

// which sections should be visible, from the checkboxes + visa type + ECR
function refreshSections(form) {
  const all = $("[data-all-process]", form).checked;
  const type = ($("[data-visa-type]:checked", form) || {}).value;
  const ecr = ($("[data-ecr]", form) || {}).value;
  const on = new Set();
  $$("[data-svc]", form).forEach((b) => { if (b.checked) on.add(b.dataset.svc); });
  if (all) ["stamping", "contract", "emigration"].forEach((k) => on.add(k));
  if (ecr !== "ECR") on.delete("emigration");
  if (type === "umrah" || type === "family") on.add(type);
  $$("input[name=locked_svc]", form).forEach((i) => on.add(i.value));
  $$("[data-section]", form).forEach((s) => { s.hidden = !on.has(s.dataset.section); });
  updateTotals(form);
}

function syncAllProcess(form) {
  const allBox = $("[data-all-process]", form);
  allBox.checked = ["medical", "skill_test", "mofa", "vfs", "ticket"].every((k) => boxOf(form, k).checked);  // FR-2.8
}

function showUndo(form, text, restore) {
  const bar = $("[data-undo-bar]", form);
  undoState = restore;
  $("[data-undo-text]", bar).textContent = text;
  bar.hidden = false;
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => { bar.hidden = true; undoState = null; }, 10000);   // FR-2.5: 10 seconds
}

function snapshot(sec) { return $$("input, select", sec).map((i) => [i, i.value]); }
function clearSection(sec) { $$("input, select", sec).forEach((i) => { i.value = i.tagName === "SELECT" ? (i.options[0] || {}).value || "" : ""; }); }

document.addEventListener("change", (e) => {
  const form = regForm(e.target);
  if (!form) return;
  const t = e.target;

  if (t.matches("[data-svc]")) {
    const key = t.dataset.svc, sec = sectionOf(form, key);
    if (!t.checked && sec && sectionHasData(sec)) {
      if (!window.confirm(`Remove ${LABELS[key]} details?`)) { t.checked = true; return; }
      const snap = snapshot(sec);
      clearSection(sec);
      showUndo(form, `${LABELS[key]} removed.`, () => { snap.forEach(([i, v]) => { i.value = v; }); t.checked = true; syncAllProcess(form); refreshSections(form); });
    }
    syncAllProcess(form);
    refreshSections(form);
  }

  if (t.matches("[data-all-process]")) {
    const keys = ALL_PROCESS;
    if (!t.checked) {
      const filled = keys.filter((k) => sectionOf(form, k) && !sectionOf(form, k).hidden && sectionHasData(sectionOf(form, k)));
      if (filled.length && !window.confirm("Remove all services? Details already filled in these sections will be deleted.")) { t.checked = true; return; }
      const snaps = keys.map((k) => [k, sectionOf(form, k) ? snapshot(sectionOf(form, k)) : []]);
      keys.forEach((k) => { if (boxOf(form, k)) boxOf(form, k).checked = false; if (sectionOf(form, k) && filled.includes(k)) clearSection(sectionOf(form, k)); });
      if (filled.length) showUndo(form, "All services removed.", () => {
        snaps.forEach(([k, s]) => s.forEach(([i, v]) => { i.value = v; }));
        keys.forEach((k) => { if (boxOf(form, k)) boxOf(form, k).checked = true; });
        t.checked = true; refreshSections(form);
      });
    } else {
      keys.forEach((k) => { const b = boxOf(form, k); if (b && !b.disabled) b.checked = true; });
    }
    refreshSections(form);
  }

  // visa type pre-ticks its usual services (FR-2.3)
  if (t.matches("[data-visa-type]")) {
    const defaults = JSON.parse(form.dataset.defaults || "{}")[t.value] || [];
    $$("[data-svc]", form).forEach((b) => { if (!b.disabled) b.checked = defaults.includes(b.dataset.svc); });
    syncAllProcess(form);
    refreshSections(form);
  }
  if (t.matches("[data-ecr]")) refreshSections(form);

  if (t.matches("[data-ref-select]")) $("[data-ref-new]", form).hidden = t.value !== "new";
});

document.addEventListener("input", (e) => {
  const form = regForm(e.target);
  if (form && (e.target.matches("[data-cost]") || e.target.matches("[data-service-charge]"))) updateTotals(form);
});

document.addEventListener("click", (e) => {
  const reset = e.target.closest("[data-reset-section]");
  if (reset) {
    const form = regForm(reset), sec = sectionOf(form, reset.dataset.resetSection);
    if (sectionHasData(sec) && window.confirm(`Reset ${LABELS[reset.dataset.resetSection]}? The details in this section will be deleted.`)) {  // FR-2.7
      const snap = snapshot(sec);
      clearSection(sec);
      showUndo(form, `${LABELS[reset.dataset.resetSection]} reset.`, () => snap.forEach(([i, v]) => { i.value = v; }));
      updateTotals(form);
    }
  }
  if (e.target.closest("[data-undo]") && undoState) {
    undoState(); undoState = null;
    regForm(e.target).querySelector("[data-undo-bar]").hidden = true;
  }
});

const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const numOf = (v) => Number(String(v || "").replace(/[^\d.]/g, "")) || 0;

function updateTotals(form) {
  let costs = 0;
  $$("[data-section]:not([hidden])", form).forEach((sec) => {
    const sell = $("input[name$='.sell']", sec), cost = $("input[name$='.cost']", sec);
    costs += numOf(sell && sell.value ? sell.value : cost ? cost.value : 0);
  });
  const sc = numOf(($("[data-service-charge]", form) || {}).value);
  const gst = Number(form.dataset.gst || 0), cap = Number(form.dataset.cap || 0);
  const c = $("[data-costs]", form), t = $("[data-total]", form), w = $("[data-cap-warn]", form);
  if (c) c.textContent = inr(costs);
  if (t) t.textContent = inr(sc + (sc * gst) / 100 + costs);
  if (w) w.hidden = !(cap && sc > cap);
}

// first paint of a form (full load and after hx-boost swaps)
function initPage(root) {
  $$("[data-reg-form]", root).forEach((f) => refreshSections(f));
  $$("[data-ticket-form]", root).forEach((f) => ticketTotals(f));
}
document.addEventListener("DOMContentLoaded", () => initPage(document));
document.addEventListener("htmx:load", (e) => initPage(e.detail.elt));

// ---------- ticket desk: margin preview + passenger "use this customer" (FR-7.1, 7.3) ----------
function ticketTotals(form) {
  const cost = numOf(($("[name=cost_price]", form) || {}).value), sell = numOf(($("[name=selling_price]", form) || {}).value);
  const m = $("[data-margin]", form);
  if (m) m.textContent = inr(sell - cost);
}
document.addEventListener("input", (e) => { const f = e.target.closest("[data-ticket-form]"); if (f) ticketTotals(f); });
document.addEventListener("click", (e) => {
  const use = e.target.closest("[data-use-customer]");
  if (!use) return;
  const row = use.closest("[data-passenger]");
  if (!row) return;
  $("input[name=pax_customer_id]", row).value = use.dataset.useCustomer;
  $("[data-pax-linked]", row).textContent = `Linked to ${use.dataset.name} (existing customer)`;
  const nameBox = $("input[name=pax_name]", row);
  if (nameBox && !nameBox.value) nameBox.value = use.dataset.name;
  use.closest("[id^=pax-lookup]").innerHTML = "";
});

// remove a passenger / flight row added with "+ Add"
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove-row]");
  if (btn) btn.closest(btn.dataset.removeRow).remove();
});

// recent search chip → put it back in the box and search again (FR-18.4)
document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-recent-search]");
  if (!chip) return;
  const box = document.getElementById("gsearch");
  box.value = chip.dataset.recentSearch;
  box.focus();
  box.dispatchEvent(new Event("input", { bubbles: true }));
});

// tabs that load a panel with htmx (dashboard Today / Tomorrow): move the highlight
document.addEventListener("click", (e) => {
  const tab = e.target.closest("[role=tab][hx-get]");
  if (!tab) return;
  tab.closest("[role=tablist]").querySelectorAll("[role=tab]").forEach((t) => t.setAttribute("aria-selected", t === tab ? "true" : "false"));
});

// ---------- app shell: "More" drawer, "New" sheet, header menus (phone + laptop) ----------
function openLayer(el) {
  if (!el) return;
  el.hidden = false;
  requestAnimationFrame(() => el.setAttribute("data-open", ""));
  document.documentElement.style.overflow = "hidden";
}
function closeLayers() {
  $$("[data-drawer], [data-sheet]").forEach((el) => {
    if (el.hidden) return;
    el.removeAttribute("data-open");
    setTimeout(() => { el.hidden = true; }, 200);
  });
  document.documentElement.style.overflow = "";
  $$("details[data-menu][open]").forEach((d) => d.removeAttribute("open"));
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-open-drawer]")) { openLayer($("[data-drawer]")); return; }
  const sheet = e.target.closest("[data-open-sheet]");
  if (sheet) { openLayer($(`[data-sheet="${sheet.dataset.openSheet}"]`)); return; }
  if (e.target.closest("[data-close-overlay]")) closeLayers();
  $$("details[data-menu][open]").forEach((d) => { if (!d.contains(e.target)) d.removeAttribute("open"); });
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLayers(); });
// navigating away (hx-boost swaps the body) must never leave the page scroll-locked
document.addEventListener("htmx:beforeRequest", (e) => { if (e.detail.boosted) closeLayers(); });

// show / hide password on login
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-toggle-password]");
  if (!btn) return;
  const input = btn.parentElement.querySelector("[data-password]");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.textContent = show ? "Hide" : "Show";
});

// a form button shows it is working while the request runs (slow networks: no double submits)
document.addEventListener("htmx:beforeRequest", (e) => {
  const form = e.detail.elt.closest && e.detail.elt.closest("form");
  if (form && e.detail.requestConfig && e.detail.requestConfig.verb !== "get") {
    $$("button[type=submit], button:not([type])", form).forEach((b) => { b.dataset.busy = "1"; b.disabled = true; });
  }
});
document.addEventListener("htmx:afterRequest", () => {
  $$("button[data-busy]").forEach((b) => { b.disabled = false; delete b.dataset.busy; });
});

// ---------- collapsible sidebar (laptop): button or "[" key; remembered per browser ----------
const sbMini = () => document.documentElement.classList.contains("sb-mini");
function syncSidebar() {
  const btn = $("[data-sb-toggle]");
  if (!btn) return;
  btn.setAttribute("aria-expanded", String(!sbMini()));
  const label = sbMini() ? "Expand menu" : "Collapse menu";
  btn.setAttribute("aria-label", label);
  btn.title = label + " ([)";
}
function toggleSidebar() {
  const mini = document.documentElement.classList.toggle("sb-mini");
  try { localStorage.setItem("sb-mini", mini ? "1" : "0"); } catch (_) {}
  hideTip();
  syncSidebar();
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-sb-toggle]")) toggleSidebar(); });
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
  if (e.key === "[" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && $("[data-sidebar]")) toggleSidebar();
});
document.addEventListener("DOMContentLoaded", syncSidebar);
document.addEventListener("htmx:load", syncSidebar);

// Tooltip with the label next to each icon while the sidebar is collapsed (fixed, so the scrolling menu cannot clip it)
function hideTip() { const t = document.getElementById("sb-tip"); if (t) t.hidden = true; }
function showTip(el) {
  if (!sbMini() || !window.matchMedia("(min-width: 1024px)").matches) return;
  let t = document.getElementById("sb-tip");
  if (!t) { t = document.createElement("div"); t.id = "sb-tip"; t.className = "sb-tip"; t.setAttribute("role", "tooltip"); document.body.appendChild(t); }
  const r = el.getBoundingClientRect();
  t.textContent = el.dataset.tip;
  t.style.left = r.right + 10 + "px";
  t.style.top = r.top + r.height / 2 + "px";
  t.hidden = false;
}
for (const [on, off] of [["mouseover", "mouseout"], ["focusin", "focusout"]]) {
  document.addEventListener(on, (e) => { const el = e.target.closest("[data-sidebar] [data-tip]"); if (el) showTip(el); });
  document.addEventListener(off, (e) => { if (e.target.closest("[data-sidebar] [data-tip]")) hideTip(); });
}
document.addEventListener("htmx:beforeRequest", hideTip);
