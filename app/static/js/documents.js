/* app/static/js/documents.js — client documents: filtering, view toggle, upload */
"use strict";

function filterDocuments() {
    const searchInput = document.getElementById("docSearchInput");
    const categoryInput = document.getElementById("categorySelect");
    if (!searchInput || !categoryInput) return;

    const query = searchInput.value.trim().toLowerCase();
    const category = categoryInput.value;
    let visible = 0;

    // Rows in the list view and cards in the grid view share the same data attributes.
    document.querySelectorAll(".document-item-element").forEach((item) => {
        const matchesCat = category === "All" || item.dataset.category === category;
        const matchesSearch = (item.dataset.filename || "").includes(query);
        item.hidden = !(matchesCat && matchesSearch);
        if (!item.hidden && item.tagName === "TR") visible += 1;
    });

    document.querySelectorAll(".folder-card").forEach((chip) => {
        chip.classList.toggle("is-active", chip.dataset.catFilter === category);
    });

    const noMatch = document.getElementById("docsNoMatch");
    if (noMatch) noMatch.hidden = visible > 0;
}

function selectCategoryFolder(categoryKey) {
    const categoryInput = document.getElementById("categorySelect");
    if (!categoryInput) return;
    categoryInput.value = categoryInput.value === categoryKey ? "All" : categoryKey;
    filterDocuments();
}

function switchView(viewType) {
    const grid = document.getElementById("gridDisplayContainer");
    const list = document.getElementById("treeDisplayContainer");
    if (!grid || !list) return;
    grid.hidden = viewType !== "grid";
    list.hidden = viewType === "grid";
    document.getElementById("btnGridView")?.classList.toggle("active", viewType === "grid");
    document.getElementById("btnTreeView")?.classList.toggle("active", viewType !== "grid");
}

async function handleQuickUpload(e) {
    e.preventDefault();
    const form = e.target;
    const button = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const filingId = formData.get("filing_id");
    if (!formData.get("document_name")) formData.delete("document_name");

    button.disabled = true;
    const label = button.innerHTML;
    button.textContent = "Uploading…";
    try {
        const response = await fetch(`/client/filings/${filingId}/upload`, { method: "POST", body: formData });
        if (response.ok) {
            window.location.reload();
            return;
        }
        const result = await response.json().catch(() => ({}));
        window.cxToast?.(typeof result.detail === "string" ? result.detail : "Upload failed. Please check file type and size.", true);
    } catch (err) {
        window.cxToast?.("An error occurred during upload.", true);
    }
    button.disabled = false;
    button.innerHTML = label;
}

// Reply forms: show the chosen file's name on the attach button
document.querySelectorAll("[data-reply-file]").forEach((input) => {
    input.addEventListener("change", () => {
        const label = input.closest("label");
        const text = label?.querySelector("[data-file-label]");
        const name = input.files?.[0]?.name;
        if (text && name) text.textContent = name;
        label?.classList.toggle("has-file", Boolean(name));
    });
});

window.filterDocuments = filterDocuments;
window.selectCategoryFolder = selectCategoryFolder;
window.switchView = switchView;
window.handleQuickUpload = handleQuickUpload;
