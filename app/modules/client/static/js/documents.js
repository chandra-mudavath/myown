/* app/modules/client/static/js/documents.js — client documents: filtering, view toggle, upload */
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
    // No description: name the file after its document type (e.g. "Form 1099-INT (interest)")
    const type = form.querySelector("[data-doc-type]")?.selectedOptions[0];
    if (!formData.get("document_name") && type?.dataset.name) formData.set("document_name", type.dataset.name);
    if (!formData.get("document_name")) formData.delete("document_name");
    if (!formData.get("document_type")) formData.delete("document_type");

    button.disabled = true;
    const label = button.innerHTML;
    button.textContent = "Uploading…";
    try {
        // Uploads go to the selected tax year; the server starts the year's case on the first one.
        // Was: /client/filings/${filingId}/upload (needed a filing to exist first)
        formData.delete("filing_id");
        const response = await fetch("/client/documents/upload", { method: "POST", body: formData });
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

// Category first, then Document type: the type list shows only the chosen category's types.
// Picking a type directly (e.g. from the checklist) still sets its category.
const docCat = document.querySelector("[data-doc-cat]");
const docType = document.querySelector("[data-doc-type]");
function filterTypes() {
    if (!docCat || !docType) return;
    const cat = docCat.value;
    docType.querySelectorAll("optgroup").forEach((group) => {
        const show = !cat || [...group.children].some((o) => o.dataset.category === cat);
        group.hidden = !show;
        group.disabled = !show;
    });
    const picked = docType.selectedOptions[0];
    if (picked?.value && cat && picked.dataset.category !== cat) docType.value = "";
    docType.disabled = !cat;
}
docCat?.addEventListener("change", filterTypes);
docType?.addEventListener("change", () => {
    const category = docType.selectedOptions[0]?.dataset.category;
    if (category && docCat && docCat.value !== category) {
        docCat.value = category;
        filterTypes();
    }
});
filterTypes();

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

// Checklist "Upload" buttons pick that document type in the upload form
document.querySelectorAll("[data-need-type]").forEach((btn) =>
    btn.addEventListener("click", () => {
        const select = document.querySelector("[data-doc-type]");
        if (!select) return;
        select.disabled = false;
        select.value = btn.dataset.needType;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        select.closest("form")?.scrollIntoView({ behavior: "smooth", block: "start" });
        select.focus();
    })
);
