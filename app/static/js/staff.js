function showSection(name) {
    document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
    document.querySelectorAll(".nav-link").forEach((n) => n.classList.remove("active"));
    document.getElementById("section-" + name).classList.add("active");
    const navEl = document.getElementById("nav-" + name);
    if (navEl) navEl.classList.add("active");
}

let signedUrlInterval = null;
let signedSeconds = 300;

function openCase(caseId, client, stage, priority) {
    document.getElementById("modalCaseId").textContent = "#" + caseId;
    document.getElementById("modalClientName").textContent = client;
    document.getElementById("advanceCurrentStage").textContent = stage;

    const stageEl = document.getElementById("modalStage");
    const stageMap = {
        Review: "badge-yellow",
        "Doc Collection": "badge-blue",
        Preparation: "badge-purple",
        "Tax Filer": "badge-orange",
        Acknowledger: "badge-teal",
        Payment: "badge-green",
    };
    stageEl.textContent = stage;
    stageEl.className = "badge " + (stageMap[stage] || "badge-gray");

    switchModalTab("docs");
    document.getElementById("caseModal").classList.add("open");
    document.getElementById("signedUrlOutput").classList.add("hidden");
    document.getElementById("stageSuccess").classList.add("hidden");
    document.getElementById("rejectForm").classList.add("hidden");
}

function closeCaseModal() {
    document.getElementById("caseModal").classList.remove("open");
    if (signedUrlInterval) {
        clearInterval(signedUrlInterval);
        signedUrlInterval = null;
    }
}

function switchModalTab(tab) {
    ["docs", "notes", "advance"].forEach((t) => {
        document.getElementById("modal-" + t).classList.add("hidden");
        document.getElementById("modal-tab-" + t).className =
            "flex-1 text-xs font-semibold py-2 rounded-xl glass text-slate-400 hover:text-white transition-colors";
    });
    document.getElementById("modal-" + tab).classList.remove("hidden");
    document.getElementById("modal-tab-" + tab).className =
        "flex-1 text-xs font-semibold py-2 rounded-xl bg-white/10 text-white";
}

function generateSignedUrl(filename) {
    const token = Math.random().toString(36).substr(2, 32);
    const url = `https://s3.us-east-1.amazonaws.com/regiustax-vault/cases/2025/${token}/${filename}`;
    document.getElementById("signedUrlValue").textContent = url;
    document.getElementById("signedUrlOutput").classList.remove("hidden");
    signedSeconds = 300;
    if (signedUrlInterval) clearInterval(signedUrlInterval);
    signedUrlInterval = setInterval(() => {
        signedSeconds--;
        const m = Math.floor(signedSeconds / 60);
        const s = String(signedSeconds % 60).padStart(2, "0");
        document.getElementById("signedUrlTimer").textContent = m + ":" + s;
        if (signedSeconds <= 0) {
            clearInterval(signedUrlInterval);
            document.getElementById("signedUrlOutput").classList.add("hidden");
        }
    }, 1000);
}

function addNote() {
    const text = document.getElementById("noteText").value.trim();
    if (!text) return;
    alert("Note added to case log and immutable audit trail.");
    document.getElementById("noteText").value = "";
}

function showRejectForm(type) {
    document.getElementById("rejectForm").classList.remove("hidden");
    document.getElementById("rejectReason").placeholder =
        type === "doc"
            ? "Explain why documents are incomplete and what the client needs to resubmit…"
            : "Explain the data errors that the preparator needs to fix…";
}

function advanceStage(toStage) {
    document.getElementById("stageSuccess").classList.remove("hidden");
    document.getElementById("stageSuccessMsg").textContent = `✓ Case advanced to ${toStage}!`;
}

function submitRejection() {
    const reason = document.getElementById("rejectReason").value.trim();
    if (!reason) {
        alert("Please provide a written justification for the rejection.");
        return;
    }
    document.getElementById("rejectForm").classList.add("hidden");
    document.getElementById("stageSuccess").classList.remove("hidden");
    document.getElementById("stageSuccessMsg").textContent = "↩ Case rejected and sent back. Justification logged.";
}
