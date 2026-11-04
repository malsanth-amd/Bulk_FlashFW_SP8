// FlashTools Web - job dispatch + live log/status streaming.
// This is the web equivalent of flash_gui.py's QProcess wiring: instead of
// Qt signals driving widgets directly, we open an EventSource (SSE) onto
// /api/jobs/{id}/stream and update the DOM as lines arrive.

let targetsFile = null;
let targetsRowCount = 0;
let activeJobId = null;
let activeEventSource = null;

const el = (id) => document.getElementById(id);

// Minimal CSV parser - the targets CSV is always a simple, unquoted format
// (see systems_template.csv), so a plain split is enough; no need for a
// full RFC-4180 parser here.
function parseCsv(text) {
  const lines = text.trim().split("\n").filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { headers: [], rows: [] };
  const headers = lines[0].split(",").map((h) => h.trim());
  const rows = lines.slice(1).map((line) => line.split(",").map((c) => c.trim()));
  return { headers, rows };
}

function renderTargetsPreview(headers, rows) {
  const table = el("targets-table");
  const head = el("targets-table-head");
  const body = el("targets-table-body");
  head.innerHTML = headers.map((h) => `<th>${h}</th>`).join("");
  body.innerHTML = rows.map((row) =>
    `<tr>${headers.map((_, i) => `<td>${row[i] ?? ""}</td>`).join("")}</tr>`
  ).join("");
  table.hidden = rows.length === 0;
}

el("targets-csv").addEventListener("change", (e) => {
  targetsFile = e.target.files[0] || null;
  const preview = el("targets-preview");
  if (!targetsFile) {
    preview.textContent = "";
    renderTargetsPreview([], []);
    targetsRowCount = 0;
    updateRunGating();
    return;
  }
  targetsFile.text().then((text) => {
    const { headers, rows } = parseCsv(text);
    targetsRowCount = rows.length;
    preview.textContent = `${rows.length} target system(s) loaded from ${targetsFile.name}`;
    renderTargetsPreview(headers, rows);
    updateRunGating();
  });
});

el("confirm-flash").addEventListener("change", updateRunGating);

// Manual firmware override: show the real (untruncated) filename next to
// each file input, a clear (x) button once one's picked, a per-board badge
// on the collapsed <summary>, and a non-blocking heuristic warning if the
// chosen filename LOOKS like it belongs to a different component.
//
// This is a filename-pattern heuristic only, not real content/extension
// sniffing - it can't catch a file that's been renamed to look legitimate,
// and it never blocks the upload (false positives on an unfamiliar naming
// scheme shouldn't stop a real flash). It's a safety-net nudge, matched
// against the actual naming conventions seen on the real BKC page/share
// this session: BMC images contain "obmc"/"phosphor" and end in
// ".mtd.tar"; FPGA images contain "_FPGA_"/"_HPMFPGA_"; BIOS images are a
// short letter prefix followed by a digit-leading alphanumeric body (e.g.
// "REAV0080I", "WEAV6A28N" - see BIOS_FILENAME_RE in refresh_recipe_bkc_page.py).
const FW_SHAPE_PATTERNS = {
  bmc: /obmc|phosphor|\.mtd\.tar$/i,
  hpm: /_fpga_|_hpmfpga_/i,
  bios: /^[a-z]{3,6}[0-9][0-9a-z]{2,6}\b/i,
};
const FW_COMPONENT_LABELS = { bios: "BIOS", bmc: "BMC", hpm: "FPGA (HPM)" };

function detectFirmwareMismatch(expectedComponent, filename) {
  for (const [component, pattern] of Object.entries(FW_SHAPE_PATTERNS)) {
    if (component !== expectedComponent && pattern.test(filename)) {
      return `"${filename}" looks like a ${FW_COMPONENT_LABELS[component]} file, not ${FW_COMPONENT_LABELS[expectedComponent]} - double check before flashing.`;
    }
  }
  return null;
}

document.querySelectorAll(".fw-file").forEach((input) => {
  const wrap = input.closest(".fw-override-row-wrap");
  const nameSpan = wrap.querySelector(".fw-override-filename");
  const clearBtn = wrap.querySelector(".fw-clear-btn");
  const warningP = wrap.querySelector(".fw-override-warning");

  input.addEventListener("change", () => {
    const file = input.files[0];
    nameSpan.textContent = file ? file.name : "";
    nameSpan.title = file ? file.name : "";
    clearBtn.hidden = !file;
    const mismatch = file ? detectFirmwareMismatch(input.dataset.component, file.name) : null;
    warningP.textContent = mismatch || "";
    warningP.hidden = !mismatch;
    updateOverrideBadge(input.dataset.board);
  });

  clearBtn.addEventListener("click", () => {
    input.value = "";
    nameSpan.textContent = "";
    nameSpan.title = "";
    clearBtn.hidden = true;
    warningP.hidden = true;
    updateOverrideBadge(input.dataset.board);
  });
});

function updateOverrideBadge(board) {
  const details = document.querySelector(`.fw-override[data-board="${board}"]`);
  const count = details.querySelectorAll(".fw-file").length
    ? Array.from(details.querySelectorAll(".fw-file")).filter((i) => i.files.length > 0).length
    : 0;
  const badge = details.querySelector(".fw-override-badge");
  if (count > 0) {
    badge.textContent = `${count} file${count > 1 ? "s" : ""} set`;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

el("toggle-pass-btn").addEventListener("click", () => {
  const input = el("bmc-pass");
  const btn = el("toggle-pass-btn");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  btn.textContent = show ? "Hide" : "Show";
});

el("refresh-btn").addEventListener("click", async () => {
  el("refresh-btn").disabled = true;
  const originalLabel = el("refresh-btn").textContent;
  el("refresh-btn").textContent = "Refreshing...";
  appendLog("=== Starting firmware refresh from the BKC page ===");
  try {
    const resp = await fetch("/api/jobs/refresh", { method: "POST" });
    const data = await resp.json();
    if (data.error) {
      appendLog(`ERROR: ${data.error}`);
      el("refresh-btn").disabled = false;
      el("refresh-btn").textContent = originalLabel;
      return;
    }
    appendLog(`$ ${data.cmd_display}`);
    streamJob(data.job_id, () => {
      el("refresh-btn").disabled = false;
      el("refresh-btn").textContent = originalLabel;
    });
  } catch (e) {
    appendLog(`ERROR: ${e}`);
    el("refresh-btn").disabled = false;
    el("refresh-btn").textContent = originalLabel;
  }
});

el("dry-run-btn").addEventListener("click", () => startFlashJob(true));
el("flash-now-btn").addEventListener("click", () => startFlashJob(false));

el("stop-btn").addEventListener("click", async () => {
  if (!activeJobId) return;
  await fetch(`/api/jobs/${activeJobId}/stop`, { method: "POST" });
  appendLog("=== Stop requested ===");
});

async function startFlashJob(dryRun) {
  if (!targetsFile) {
    appendLog("ERROR: upload a targets CSV first.");
    return;
  }
  const triggeringBtn = el(dryRun ? "dry-run-btn" : "flash-now-btn");
  const originalLabel = triggeringBtn.textContent;
  setRunButtonsEnabled(false);
  triggeringBtn.textContent = "Running...";
  clearLog();
  clearStatusTable();
  appendLog(dryRun ? "=== Starting dry run ===" : "=== Starting FLASH NOW ===");

  const form = new FormData();
  form.append("targets_csv", targetsFile);
  form.append("dry_run", dryRun ? "true" : "false");
  if (el("bmc-user").value) form.append("bmc_user", el("bmc-user").value);
  if (el("bmc-pass").value) form.append("bmc_pass", el("bmc-pass").value);

  // Manual firmware override: any file the user picked under "1b." gets
  // sent as fw_<board>_<component>, matched server-side to only the rows
  // whose platform equals <board>.
  document.querySelectorAll(".fw-file").forEach((input) => {
    if (input.files.length > 0) {
      form.append(`fw_${input.dataset.board}_${input.dataset.component}`, input.files[0]);
    }
  });

  const restore = () => {
    updateRunGating();
    triggeringBtn.textContent = originalLabel;
  };

  try {
    const resp = await fetch(`/api/jobs/flash?dry_run=${dryRun}`, { method: "POST", body: form });
    const data = await resp.json();
    if (data.error) {
      appendLog(`ERROR: ${data.error}`);
      restore();
      return;
    }
    appendLog(`$ ${data.cmd_display}`);
    el("stop-btn").disabled = false;
    streamJob(data.job_id, restore);
  } catch (e) {
    appendLog(`ERROR: ${e}`);
    restore();
  }
}

function streamJob(jobId, onDone) {
  activeJobId = jobId;
  if (activeEventSource) activeEventSource.close();
  const source = new EventSource(`/api/jobs/${jobId}/stream`);
  activeEventSource = source;

  source.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.line !== undefined) appendLog(data.line);
    if (data.statuses) renderStatuses(data.statuses);
  };
  source.addEventListener("done", (ev) => {
    const data = JSON.parse(ev.data);
    appendLog(`=== Job finished (exit code ${data.exit_code}) ===`);
    if (data.statuses) renderStatuses(data.statuses);
    source.close();
    activeEventSource = null;
    activeJobId = null;
    el("stop-btn").disabled = true;
    onDone();
  });
  source.onerror = () => {
    appendLog("ERROR: lost connection to the job stream.");
    source.close();
  };
}

function setRunButtonsEnabled(enabled) {
  el("dry-run-btn").disabled = !enabled || !targetsFile;
  el("flash-now-btn").disabled = !enabled || !el("confirm-flash").checked || !targetsFile;
}

function updateRunGating() {
  setRunButtonsEnabled(true);
  const hint = el("run-hint");
  if (!targetsFile) {
    hint.textContent = "Upload a target CSV above to enable Dry Run / Flash Now.";
  } else if (!el("confirm-flash").checked) {
    hint.textContent = `${targetsRowCount} target system(s) loaded. Check the box above to enable Flash Now (Dry Run is always safe).`;
  } else {
    hint.textContent = `${targetsRowCount} target system(s) loaded - ready to flash.`;
  }
}

function appendLog(line) {
  const log = el("output-log");
  log.textContent += line + "\n";
  log.scrollTop = log.scrollHeight;
}

function clearLog() {
  el("output-log").textContent = "";
}

function clearStatusTable() {
  el("status-rows").innerHTML = "";
}

function renderStatuses(statuses) {
  const tbody = el("status-rows");
  tbody.innerHTML = "";
  for (const [name, info] of Object.entries(statuses)) {
    const tr = document.createElement("tr");
    const statusClass = info.status === "ok" ? "status-ok" : "status-failed";
    tr.innerHTML = `
      <td>${name}</td>
      <td class="${statusClass}">${info.detail}</td>
      <td>${info.elapsed}s</td>
      <td>${info.log}</td>
    `;
    tbody.appendChild(tr);
  }
}

updateRunGating();
