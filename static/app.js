const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const chooseFileBtn = document.getElementById("chooseFileBtn");
const fileName = document.getElementById("fileName");
const outputDir = document.getElementById("outputDir");
const browseOutputBtn = document.getElementById("browseOutputBtn");
const folderStatus = document.getElementById("folderStatus");
const dpiSelect = document.getElementById("dpiSelect");
const modeSelect = document.getElementById("modeSelect");
const formatSelect = document.getElementById("formatSelect");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const darkModeToggle = document.getElementById("darkModeToggle");
const logList = document.getElementById("logList");
const thumbGrid = document.getElementById("thumbGrid");
const thumbPlaceholder = document.getElementById("thumbPlaceholder");
const timerDisplay = document.getElementById("timerDisplay");
const ringFill = document.getElementById("ringFill");
const statPages = document.getElementById("statPages");
const statPagesSub = document.getElementById("statPagesSub");
const statImages = document.getElementById("statImages");
const statPercent = document.getElementById("statPercent");
const statCurrentPage = document.getElementById("statCurrentPage");
const statPageStatus = document.getElementById("statPageStatus");
const statQueue = document.getElementById("statQueue");
const queueDot = document.getElementById("queueDot");

let pollTimer = null;
let lastLogLen = 0;
let localTimer = null;
let localSeconds = 0;
let totalPagesHint = 0;

function formatTime(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")} : ${String(s).padStart(2, "0")}`;
}

function setRing(percent) {
  const p = Math.min(100, Math.max(0, percent));
  ringFill.setAttribute("stroke-dasharray", `${p}, 100`);
  statPercent.textContent = `${p}%`;
}

function setRunning(running) {
  startBtn.disabled = running;
  stopBtn.disabled = !running;
  queueDot.className = "queue-dot" + (running ? " running" : "");
}

const OUTPUT_DIR_KEY = "cropToolOutputDir";

function saveOutputDirPreference() {
  const path = outputDir.value.trim();
  if (path) localStorage.setItem(OUTPUT_DIR_KEY, path);
}

function updateFolderStatus() {
  const path = outputDir.value.trim();
  if (!path) {
    folderStatus.textContent = "";
    folderStatus.className = "folder-status";
    return;
  }
  folderStatus.textContent = "Folder path set (must exist on this PC where server runs)";
  folderStatus.className = "folder-status ready";
}

function startLocalTimer() {
  localSeconds = 0;
  timerDisplay.textContent = formatTime(0);
  if (localTimer) clearInterval(localTimer);
  localTimer = setInterval(() => {
    localSeconds += 1;
    timerDisplay.textContent = formatTime(localSeconds);
  }, 1000);
}

function stopLocalTimer() {
  if (localTimer) {
    clearInterval(localTimer);
    localTimer = null;
  }
}

function renderLogs(messages) {
  if (messages.length <= lastLogLen) return;
  const newMsgs = messages.slice(lastLogLen);
  newMsgs.forEach((msg) => {
    const li = document.createElement("li");
    const isError = /error|failed|cannot/i.test(msg);
    if (isError) li.classList.add("error");
    li.textContent = msg;
    logList.appendChild(li);
  });
  logList.scrollTop = logList.scrollHeight;
  lastLogLen = messages.length;
}

function renderThumbs(images) {
  if (!images || !images.length) return;
  thumbPlaceholder.style.display = "none";

  const existing = new Set(
    [...thumbGrid.querySelectorAll(".thumb-item")].map((el) => el.dataset.name)
  );

  images.forEach((img) => {
    if (existing.has(img.name)) return;
    const div = document.createElement("div");
    div.className = "thumb-item";
    div.dataset.name = img.name;
    const el = document.createElement("img");
    el.src = `${img.url}${img.url.includes("?") ? "&" : "?"}t=${Date.now()}`;
    el.alt = img.name;
    el.loading = "lazy";
    const cap = document.createElement("span");
    cap.textContent = img.name;
    div.appendChild(el);
    div.appendChild(cap);
    thumbGrid.appendChild(div);
  });
}

async function resetUiState() {
  logList.innerHTML = "";
  lastLogLen = 0;
  thumbGrid.querySelectorAll(".thumb-item").forEach((el) => el.remove());
  thumbPlaceholder.style.display = "block";
  statImages.textContent = "0";
  setRing(0);
  statCurrentPage.textContent = "—";
  statPageStatus.textContent = "Idle";
  statQueue.textContent = "Ready";
  queueDot.className = "queue-dot";
  timerDisplay.textContent = formatTime(0);
  stopLocalTimer();
}

async function resetSession() {
  await fetch("/api/reset", { method: "POST" });
  await resetUiState();
  fileName.textContent = "No file selected";
  totalPagesHint = 0;
  statPages.textContent = "—";
}

async function uploadFiles(fileListObj) {
  const files = Array.from(fileListObj);
  if (!files.length) return;

  await resetUiState();

  const form = new FormData();
  files.forEach((f) => form.append("files", f));

  const res = await fetch("/api/upload", { method: "POST", body: form });
  fileInput.value = "";

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || "Upload failed");
    return;
  }

  const data = await res.json();
  fileName.textContent = data.primary_filename || data.files.map((f) => f.name).join(", ");
  totalPagesHint = data.total_pages || 0;
  statPages.textContent = totalPagesHint ? String(totalPagesHint) : "—";
  statPagesSub.textContent = totalPagesHint === 1 ? "Page" : "Pages";

  const li = document.createElement("li");
  li.textContent = `Uploaded: ${fileName.textContent}`;
  logList.appendChild(li);
  lastLogLen = 1;
}

chooseFileBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => uploadFiles(e.target.files));

["dragenter", "dragover"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
});

["dragleave", "drop"].forEach((ev) => {
  dropZone.addEventListener(ev, (e) => {
    e.preventDefault();
    if (ev === "drop") uploadFiles(e.dataTransfer.files);
    dropZone.classList.remove("drag-over");
  });
});

document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  if (!dropZone.contains(e.target)) uploadFiles(e.dataTransfer.files);
});

browseOutputBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  const res = await fetch("/api/select-folder");
  const data = await res.json();
  if (data.path) {
    outputDir.value = data.path;
    saveOutputDirPreference();
    updateFolderStatus();
  }
});

outputDir.addEventListener("input", () => {
  saveOutputDirPreference();
  updateFolderStatus();
});

function updateCmykHint() {
  const hint = document.getElementById("cmykHint");
  if (!hint) return;
  hint.classList.toggle("visible", modeSelect.value === "cmyk");
}

modeSelect.addEventListener("change", updateCmykHint);

startBtn.addEventListener("click", async () => {
  const body = {
    output_dir: outputDir.value.trim(),
    dpi: parseInt(dpiSelect.value, 10),
    color_mode: modeSelect.value,
    output_format: formatSelect.value,
  };

  if (!body.output_dir) {
    alert("Please choose an output folder (BROWSE).");
    return;
  }

  logList.innerHTML = "";
  lastLogLen = 0;
  thumbGrid.querySelectorAll(".thumb-item").forEach((el) => el.remove());
  thumbPlaceholder.style.display = "block";
  statImages.textContent = "0";
  setRing(0);

  const res = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg =
      typeof err.detail === "string"
        ? err.detail
        : Array.isArray(err.detail)
          ? err.detail.map((d) => d.msg || d).join("\n")
          : "Could not start — check output folder path";
    alert(msg);
    return;
  }

  saveOutputDirPreference();

  setRunning(true);
  startLocalTimer();
  startPolling();
});

stopBtn.addEventListener("click", async () => {
  await fetch("/api/stop", { method: "POST" });
  statQueue.textContent = "Stopping…";
});

darkModeToggle.addEventListener("change", () => {
  document.body.classList.toggle("dark", darkModeToggle.checked);
  localStorage.setItem("darkMode", darkModeToggle.checked ? "1" : "0");
});

async function refreshStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();

  setRunning(data.running);

  renderLogs(data.messages);

  const pages = data.total_pages || totalPagesHint;
  statPages.textContent = pages ? String(pages) : "—";
  statImages.textContent = String(data.saved_count || 0);

  setRing(data.progress_percent || 0);

  if (data.current_page && data.total_pages) {
    statCurrentPage.textContent = `${data.current_page} / ${data.total_pages}`;
    statPageStatus.textContent = data.running ? "Processing Page" : "Done";
  } else if (data.running) {
    statCurrentPage.textContent = "…";
    statPageStatus.textContent = "Processing";
  } else {
    statCurrentPage.textContent = "—";
    statPageStatus.textContent = "Idle";
  }

  const q = data.queue_status || "idle";
  statQueue.textContent =
    q === "running"
      ? "Processing"
      : q === "done"
        ? "Complete"
        : q === "error"
          ? "Error"
          : q === "stopping"
            ? "Stopping"
            : "Ready";

  queueDot.className = "queue-dot";
  if (data.running) queueDot.classList.add("running");
  else if (q === "error") queueDot.classList.add("error");
  else if (q === "done") queueDot.classList.add("done");

  if (data.elapsed_seconds != null && data.running) {
    timerDisplay.textContent = formatTime(data.elapsed_seconds);
  }

  renderThumbs(data.output_images);

  if (!data.running && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    stopLocalTimer();
    if (data.elapsed_seconds != null) {
      timerDisplay.textContent = formatTime(data.elapsed_seconds);
    }
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refreshStatus, 500);
  refreshStatus();
}

(function init() {
  if (localStorage.getItem("darkMode") === "1") {
    darkModeToggle.checked = true;
    document.body.classList.add("dark");
  }
  const savedOut = localStorage.getItem(OUTPUT_DIR_KEY);
  if (savedOut) outputDir.value = savedOut;
  setRing(0);
  setRunning(false);
  updateFolderStatus();
  updateCmykHint();
  resetSession();
})();
