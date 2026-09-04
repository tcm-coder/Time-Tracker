function pad(n) {
  return n.toString().padStart(2, "0");
}

function formatHMS(totalSeconds) {
  totalSeconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${h}:${pad(m)}:${pad(s)}`;
}

function tickRunningTimers() {
  document.querySelectorAll(".timer.running").forEach(function (el) {
    const base = parseFloat(el.dataset.baseSeconds || "0");
    const startTs = el.dataset.startTs;
    if (!startTs) return;
    const start = new Date(startTs);
    const elapsed = base + (Date.now() - start.getTime()) / 1000;
    el.textContent = formatHMS(elapsed);
  });
}

document.addEventListener("DOMContentLoaded", function () {
  tickRunningTimers();
  setInterval(tickRunningTimers, 1000);
});

function formatHM(totalSeconds) {
  totalSeconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  return `${h}h ${pad(m)}m`;
}

// Separate from tickRunningTimers: the header clock widget shows "Xh YYm"
// (matches format_duration server-side), not the H:MM:SS job timers use.
function tickClockWidget() {
  const el = document.querySelector(".clock-widget-time.running");
  if (!el) return;
  const base = parseFloat(el.dataset.baseSeconds || "0");
  const startTs = el.dataset.startTs;
  if (!startTs) return;
  const start = new Date(startTs);
  const elapsed = base + (Date.now() - start.getTime()) / 1000;
  el.textContent = formatHM(elapsed);
}

document.addEventListener("DOMContentLoaded", function () {
  tickClockWidget();
  setInterval(tickClockWidget, 1000);
});

document.addEventListener("click", function (e) {
  if (e.target.closest("a, button, input, form, select, textarea")) return;
  const row = e.target.closest("tr[data-href]");
  if (row) window.location.href = row.dataset.href;
});

// Registered once at script load, not inside initDescriptionEditor, so it
// isn't re-registered on every "New Job" toggle click.
const TABLE_MODULE_NAME = TableUp.default.moduleName; // "table-up"
Quill.register({ [`modules/${TABLE_MODULE_NAME}`]: TableUp.default }, true);

// Format names Quill will keep on the page. Anything not listed here is
// stripped by the editor itself, so the table-up-* blot names must be
// present or a pasted table throws (Parchment can't create the blot) instead
// of degrading gracefully.
const DESCRIPTION_FORMATS = [
  "bold", "italic", "underline", "strike", "list", "blockquote", "background",
  "table-up", "table-up-container", "table-up-caption", "table-up-main",
  "table-up-colgroup", "table-up-col", "table-up-head", "table-up-body",
  "table-up-foot", "table-up-row", "table-up-cell", "table-up-cell-inner",
];

// Initializing Quill while its container is inside a display:none ancestor
// (e.g. the collapsed "New Job" form) breaks its layout, so callers must
// only invoke this once the editor is actually visible. Guarded so it's
// safe to call more than once (e.g. every time a toggle button is clicked).
function initDescriptionEditor(editorId, hiddenId) {
  const container = document.getElementById(editorId);
  if (!container || container.dataset.quillInit) return;
  container.dataset.quillInit = "1";

  const quill = new Quill("#" + editorId, {
    theme: "snow",
    placeholder: "Description / requirements (optional)",
    formats: DESCRIPTION_FORMATS,
    modules: {
      toolbar: [
        ["bold", "italic", "underline", "strike"],
        [{ list: "ordered" }, { list: "bullet" }],
        ["blockquote"],
        [{ background: [] }],
        [{ [TableUp.default.toolName]: [] }],
      ],
      [TABLE_MODULE_NAME]: {
        // Excel/Word paste HTML drives cell appearance from a <style> block
        // rather than inline styles; without this the table structure comes
        // through but every cell renders unstyled.
        pasteStyleSheet: true,
        pasteDefaultTagStyle: true,
        customSelect: TableUp.defaultCustomSelect,
      },
    },
  });

  const hidden = document.getElementById(hiddenId);
  const sync = function () { hidden.value = quill.root.innerHTML; };
  sync();
  quill.on("text-change", sync);
  const form = hidden.closest("form");
  if (form) form.addEventListener("submit", sync);
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
  return Promise.resolve();
}

document.addEventListener("click", function (e) {
  const btn = e.target.closest("#copy-job-btn");
  if (!btn) return;
  const original = btn.textContent;
  copyText(btn.dataset.copyText || "")
    .then(function () {
      btn.textContent = "Copied!";
    })
    .catch(function () {
      btn.textContent = "Copy failed";
    })
    .finally(function () {
      setTimeout(function () { btn.textContent = original; }, 1500);
    });
});
