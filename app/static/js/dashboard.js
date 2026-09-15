const accountMenuButton = document.querySelector(".account-menu-button");
const accountMenu = document.getElementById("account-menu");

if (accountMenuButton && accountMenu) {
    accountMenuButton.addEventListener("click", (event) => {
        event.stopPropagation();
        const isOpen = accountMenuButton.getAttribute("aria-expanded") === "true";
        accountMenuButton.setAttribute("aria-expanded", String(!isOpen));
        accountMenu.hidden = isOpen;
    });

    document.addEventListener("click", (event) => {
        if (!accountMenuButton.contains(event.target) && !accountMenu.contains(event.target)) {
            accountMenuButton.setAttribute("aria-expanded", "false");
            accountMenu.hidden = true;
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            accountMenuButton.setAttribute("aria-expanded", "false");
            accountMenu.hidden = true;
            accountMenuButton.focus();
        }
    });

    accountMenu.addEventListener("click", (event) => {
        if (event.target.closest("a")) {
            accountMenuButton.setAttribute("aria-expanded", "false");
            accountMenu.hidden = true;
        }
    });
}

let uploadedFiles = [];
let currentStep = 1;

function showSection(name) {
    document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
    document.querySelectorAll(".nav-link").forEach((n) => n.classList.remove("active"));
    document.getElementById("section-" + name).classList.add("active");
    const navEl = document.getElementById("nav-" + name);
    if (navEl) navEl.classList.add("active");
}

function goStep(step) {
    ["form-step-1", "form-step-2", "form-step-3"].forEach((id) => document.getElementById(id).classList.add("hidden"));
    document.getElementById("form-step-" + step).classList.remove("hidden");
    currentStep = step;

    // Update dots
    [1, 2, 3].forEach((i) => {
        const dot = document.getElementById("step-dot-" + i);
        const lbl = document.getElementById("step-label-" + i);
        if (i < step) {
            dot.className =
                "w-8 h-8 bg-emerald-500 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-sm";
            dot.innerHTML = "✓";
            lbl.className = "text-sm font-bold text-emerald-600";
        } else if (i === step) {
            dot.className =
                "w-8 h-8 bg-brand-600 rounded-full flex items-center justify-center text-white text-xs font-bold shadow-md";
            dot.textContent = i;
            lbl.className = "text-sm font-bold text-brand-600";
        } else {
            dot.className =
                "w-8 h-8 bg-slate-100 border border-slate-300 rounded-full flex items-center justify-center text-slate-500 text-xs font-bold";
            dot.textContent = i;
            lbl.className = "text-sm font-medium text-slate-500";
        }
    });
}

function handleDragOver(e) {
    e.preventDefault();
    document.getElementById("dropZone").classList.add("dragging");
}
function handleDragLeave(e) {
    document.getElementById("dropZone").classList.remove("dragging");
}
function handleDrop(e) {
    e.preventDefault();
    document.getElementById("dropZone").classList.remove("dragging");
    addFiles(e.dataTransfer.files);
}
function handleFileSelect(e) {
    addFiles(e.target.files);
}

function addFiles(fileList) {
    Array.from(fileList).forEach((file) => {
        if (!uploadedFiles.find((f) => f.name === file.name)) {
            uploadedFiles.push(file);
            renderFileList();
        }
    });
}

function renderFileList() {
    const list = document.getElementById("fileList");
    list.innerHTML = uploadedFiles
        .map(
            (f, i) => `
    <div class="file-item flex items-center gap-3 p-3 rounded-xl shadow-sm">
      <span class="text-lg bg-blue-50 p-2 rounded-lg border border-blue-100">📄</span>
      <div class="flex-1 min-w-0">
        <p class="text-sm text-brand-900 font-bold truncate">${f.name}</p>
        <div class="progress-bar mt-1.5 w-full"><div class="progress-fill" style="width:100%"></div></div>
      </div>
      <span class="text-xs text-emerald-600 font-bold flex-shrink-0 bg-emerald-50 px-2 py-1 rounded border border-emerald-100">✓ Ready</span>
      <button onclick="removeFile(${i})" class="text-slate-400 hover:text-red-500 transition-colors flex-shrink-0 text-xs bg-slate-50 hover:bg-red-50 w-6 h-6 rounded flex items-center justify-center font-bold">✕</button>
    </div>`
        )
        .join("");
}

function removeFile(i) {
    uploadedFiles.splice(i, 1);
    renderFileList();
}

function submitCase() {
    document.getElementById("submitCaseBtn").disabled = true;
    document.getElementById("submitCaseBtn").textContent = "Submitting…";
    setTimeout(() => {
        document.getElementById("submitCaseBtn").classList.add("hidden");
        document.getElementById("submitSuccess").classList.remove("hidden");
    }, 1600);
}

function simulateDownload(btn, filename) {
    const notice = document.getElementById("signedUrlNotice");
    const urlText = document.getElementById("signedUrlText");
    const token = Math.random().toString(36).substr(2, 24);
    const expiry = new Date(Date.now() + 5 * 60 * 1000).toLocaleTimeString();
    urlText.textContent = `https://secure-storage.urtax.com/vault/${filename}?X-Signature=${token}&Expires=${expiry}`;
    notice.classList.remove("hidden");
    btn.textContent = "Link Generated ✓";
    btn.classList.add("bg-emerald-50", "text-emerald-700", "border-emerald-200");
    setTimeout(() => {
        btn.textContent = "Download ↓";
        btn.classList.remove("bg-emerald-50", "text-emerald-700", "border-emerald-200");
        notice.classList.add("hidden");
    }, 12000);
}
