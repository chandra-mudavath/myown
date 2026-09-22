/* app/static/js/documents.js — Client document filtering & view toggle */
"use strict";

function filterDocuments() {
    const searchInput = document.getElementById("docSearchInput");
    const categorySelect = document.getElementById("categorySelect");
    if (!searchInput || !categorySelect) return;

    const searchQuery = searchInput.value.toLowerCase();
    const selectedCategory = categorySelect.value;

    // Filter grid card items
    const allDocItems = document.querySelectorAll(".document-item-element");
    allDocItems.forEach((item) => {
        const itemCat = item.getAttribute("data-category");
        const fileName = (item.getAttribute("data-filename") || "").toLowerCase();
        const matchesCat = selectedCategory === "All" || itemCat === selectedCategory;
        const matchesSearch = fileName.includes(searchQuery);

        if (matchesCat && matchesSearch) {
            item.style.display = "flex";
        } else {
            item.style.display = "none";
        }
    });

    // Update active state on top folder cards
    const folderCards = document.querySelectorAll(".folder-card");
    folderCards.forEach((card) => {
        const folderCat = card.getAttribute("data-cat-filter");
        if (folderCat === selectedCategory) {
            card.classList.add("active-folder");
        } else {
            card.classList.remove("active-folder");
        }
    });
}

function selectCategoryFolder(categoryKey) {
    const categorySelect = document.getElementById("categorySelect");
    if (!categorySelect) return;
    if (categorySelect.value === categoryKey) {
        categorySelect.value = "All";
    } else {
        categorySelect.value = categoryKey;
    }
    filterDocuments();
}

function switchView(viewType) {
    const gridContainer = document.getElementById("gridDisplayContainer");
    const treeContainer = document.getElementById("treeDisplayContainer");
    const btnGrid = document.getElementById("btnGridView");
    const btnTree = document.getElementById("btnTreeView");

    if (!gridContainer || !treeContainer) return;

    if (viewType === "grid") {
        gridContainer.style.display = "grid";
        treeContainer.style.display = "none";
        btnGrid?.classList.add("active");
        btnTree?.classList.remove("active");
    } else {
        gridContainer.style.display = "none";
        treeContainer.style.display = "block";
        btnTree?.classList.add("active");
        btnGrid?.classList.remove("active");
    }
}

function changeYearFilter(year) {
    document.cookie = "tax_year=" + year + "; path=/; max-age=" + 180 * 86400;
    window.location.reload();
}

function openUploadModal() {
    const modal = document.getElementById("uploadModal");
    if (modal) modal.style.display = "flex";
}

function closeUploadModal() {
    const modal = document.getElementById("uploadModal");
    if (modal) modal.style.display = "none";
}

async function handleQuickUpload(e) {
    e.preventDefault();
    const form = e.target;
    const formData = new FormData(form);
    const filingId = formData.get("filing_id");

    try {
        const response = await fetch(`/client/filings/${filingId}/upload`, {
            method: "POST",
            body: formData,
        });
        if (response.ok) {
            window.location.reload();
        } else {
            alert("Upload failed. Please check file type and size.");
        }
    } catch (err) {
        alert("An error occurred during upload.");
    }
}

// Bind to window for HTML event handlers
window.filterDocuments = filterDocuments;
window.selectCategoryFolder = selectCategoryFolder;
window.switchView = switchView;
window.changeYearFilter = changeYearFilter;
window.openUploadModal = openUploadModal;
window.closeUploadModal = closeUploadModal;
window.handleQuickUpload = handleQuickUpload;
