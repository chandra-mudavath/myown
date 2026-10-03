// Staff pages: navigation loading state and the case-table filter toggle.
(function () {
    const body = document.body;
    const status = document.querySelector("[data-loading-status]");
    let slowTimer = null;

    // The progress bar shows at once; skeletons only appear if the next page is slow,
    // so fast navigations don't flash.
    const startLoading = () => {
        if (body.classList.contains("is-navigating")) return;
        body.classList.add("is-navigating");
        if (status) status.textContent = "Loading…";
        slowTimer = setTimeout(() => body.classList.add("is-loading-slow"), 180);
    };

    const stopLoading = () => {
        clearTimeout(slowTimer);
        body.classList.remove("is-navigating", "is-loading-slow");
        if (status) status.textContent = "";
    };

    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link || event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return;
        if (link.getAttribute("href").startsWith("javascript:")) return;

        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) return;
        const samePage = url.pathname === window.location.pathname && url.search === window.location.search;
        if (samePage && url.hash) return;

        startLoading();
    });

    document.addEventListener("submit", (event) => {
        if (!event.defaultPrevented) startLoading();
    });

    // Back/forward cache restores the page as it was left, loading state included.
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) stopLoading();
    });

    // Dashboard clients list: instant filter over the rows already on the page.
    const clientFilter = document.querySelector("[data-client-filter]");
    if (clientFilter) {
        const rows = [...document.querySelectorAll("[data-client-row]")];
        const empty = document.querySelector("[data-client-empty]");
        const count = document.querySelector("[data-client-count]");
        clientFilter.addEventListener("input", () => {
            const term = clientFilter.value.trim().toLowerCase();
            let shown = 0;
            rows.forEach((row) => {
                const match = !term || row.dataset.search.includes(term);
                row.hidden = !match;
                if (match) shown += 1;
            });
            if (empty) empty.hidden = shown > 0;
            if (count) count.textContent = shown.toLocaleString();
        });
    }

    // Documents page: status filter + "show older versions" toggle over the table rows.
    const docFilter = document.querySelector("[data-doc-filter]");
    if (docFilter) {
        const buttons = [...docFilter.querySelectorAll("[data-filter]")];
        const rows = [...document.querySelectorAll("[data-doc-item]")];
        const none = document.querySelector("[data-doc-none]");
        const showOld = document.querySelector("[data-show-old]");
        let key = "all";

        const apply = () => {
            let shown = 0;
            rows.forEach((row) => {
                const match = (key === "all" || row.dataset.status === key) && (!("old" in row.dataset) || showOld?.checked);
                row.hidden = !match;
                if (match) shown += 1;
            });
            if (none) none.hidden = shown > 0;
        };

        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                key = button.dataset.filter;
                buttons.forEach((b) => {
                    b.classList.toggle("is-active", b === button);
                    b.setAttribute("aria-pressed", String(b === button));
                });
                apply();
            });
        });
        showOld?.addEventListener("change", apply);
    }

    // Documents page: preview dialog (PDF / image inline, other types get a download prompt).
    const previewDialog = document.querySelector("[data-preview-dialog]");
    if (previewDialog) {
        const stage = previewDialog.querySelector("[data-preview-stage]");
        document.querySelectorAll("[data-preview]").forEach((button) => {
            button.addEventListener("click", () => {
                const { url, kind, name, file } = button.dataset;
                previewDialog.querySelector("[data-preview-title]").textContent = name;
                previewDialog.querySelector("[data-preview-file]").textContent = file;
                previewDialog.querySelector("[data-preview-open]").href = url;
                previewDialog.querySelector("[data-preview-download]").href = `${url}?download=1`;
                stage.replaceChildren();
                if (kind === "pdf") {
                    const frame = document.createElement("iframe");
                    frame.className = "doc-stage__frame";
                    frame.src = `${url}#view=FitH`;
                    frame.title = `Preview of ${name}`;
                    stage.append(frame);
                } else if (kind === "image") {
                    const img = document.createElement("img");
                    img.className = "doc-stage__img";
                    img.src = url;
                    img.alt = `Preview of ${name}`;
                    stage.append(img);
                } else {
                    const note = document.createElement("div");
                    note.className = "doc-stage__none";
                    note.innerHTML = "<strong>No preview for this file type</strong><span>Use Download to open it.</span>";
                    stage.append(note);
                }
                previewDialog.showModal();
            });
        });
        // Drop the file from memory when the dialog closes
        previewDialog.addEventListener("close", () => stage.replaceChildren());
    }

    // Documents page: reject dialog asks for the reason sent to the client.
    const rejectDialog = document.querySelector("[data-reject-dialog]");
    if (rejectDialog) {
        const form = rejectDialog.querySelector("[data-reject-form]");
        const reason = rejectDialog.querySelector("textarea");
        const openReject = (button) => {
            form.action = button.dataset.action;
            rejectDialog.querySelector("[data-reject-name]").textContent = button.dataset.name;
            reason.value = button.dataset.reason || "";
            rejectDialog.showModal();
            reason.focus();
        };
        document.querySelectorAll("[data-reject]").forEach((button) => {
            button.addEventListener("click", () => openReject(button));
        });
        // Server sent us back because the reason was empty: reopen the dialog for that document
        const retry = document.querySelector("[data-reject-open]");
        if (retry) openReject(retry);
    }

    // Documents page: comments dialog (conversation with the client about one document).
    const threadDialog = document.querySelector("[data-thread-dialog]");
    if (threadDialog) {
        const list = threadDialog.querySelector("[data-thread-list]");
        const empty = threadDialog.querySelector("[data-thread-empty]");
        document.querySelectorAll("[data-thread-open]").forEach((button) => {
            button.addEventListener("click", () => {
                const source = document.querySelector(`[data-thread-for="${button.dataset.threadOpen}"]`);
                list.innerHTML = source ? source.innerHTML : "";
                empty.hidden = list.children.length > 0;
                threadDialog.querySelector("[data-thread-name]").textContent = button.dataset.name;
                threadDialog.querySelector("[data-thread-form]").action = button.dataset.action;
                threadDialog.showModal();
                list.lastElementChild?.scrollIntoView({ block: "end" });
                threadDialog.querySelector("textarea").focus();
            });
        });
    }

    // Case tables + case page: "Move stage" dialog, preselecting the next workflow stage.
    const stageDialog = document.querySelector("[data-stage-dialog]");
    if (stageDialog) {
        const form = stageDialog.querySelector("[data-stage-form]");
        const select = stageDialog.querySelector("select");
        document.querySelectorAll("[data-move-stage]").forEach((button) => {
            button.addEventListener("click", () => {
                form.action = button.dataset.action;
                stageDialog.querySelector("[data-stage-case]").textContent = button.dataset.case;
                stageDialog.querySelector("[data-stage-current]").textContent = button.dataset.current;
                [...select.options].forEach((o) => { o.disabled = o.value === button.dataset.currentKey; });
                select.value = button.dataset.next || select.options[0].value;
                form.dataset.caseLabel = button.dataset.case.split(" · ")[0];
                stageDialog.showModal();
                select.focus();
            });
        });
        // Remember what moved so the reloaded page can confirm it
        form.addEventListener("submit", () => {
            try {
                const label = select.options[select.selectedIndex].text.replace(/^\d+\.\s*/, "");
                sessionStorage.setItem("staff-flash", `${form.dataset.caseLabel} moved to ${label}`);
            } catch (e) { /* storage unavailable: skip the confirmation */ }
        });
    }

    // One-shot confirmation message after a redirect (e.g. a stage move)
    try {
        const flash = sessionStorage.getItem("staff-flash");
        if (flash) {
            sessionStorage.removeItem("staff-flash");
            const toast = document.createElement("div");
            toast.className = "staff-toast";
            toast.setAttribute("role", "status");
            toast.textContent = flash;
            document.body.append(toast);
            setTimeout(() => toast.classList.add("is-leaving"), 3500);
            setTimeout(() => toast.remove(), 4000);
        }
    } catch (e) { /* storage unavailable */ }

    // Notifications page: filter rows by activity type or "new".
    const notifFilter = document.querySelector("[data-notif-filter]");
    if (notifFilter) {
        const buttons = [...notifFilter.querySelectorAll("[data-filter]")];
        const rows = [...document.querySelectorAll("[data-notif-row]")];
        const none = document.querySelector("[data-notif-none]");
        buttons.forEach((button) => {
            button.addEventListener("click", () => {
                const key = button.dataset.filter;
                buttons.forEach((b) => {
                    b.classList.toggle("is-active", b === button);
                    b.setAttribute("aria-pressed", String(b === button));
                });
                let shown = 0;
                rows.forEach((row) => {
                    const match = key === "all" || (key === "new" ? row.dataset.new === "yes" : row.dataset.kind === key);
                    row.hidden = !match;
                    if (match) shown += 1;
                });
                if (none) none.hidden = shown > 0;
            });
        });
    }

    document.querySelectorAll("[data-dialog-close]").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog")?.close());
    });
    // Click on the backdrop closes a dialog
    document.querySelectorAll("dialog.doc-dialog").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) dialog.close();
        });
    });

    // Documents page: after approve/reject, bring the updated row into view
    document.querySelector("tr.is-highlight")?.scrollIntoView({ block: "center" });

    // Case page: bring the current stage into view in the pipeline strip.
    const pipeline = document.querySelector("[data-pipeline]");
    const currentStep = pipeline?.querySelector('[aria-current="step"]');
    if (pipeline && currentStep) {
        pipeline.parentElement.scrollLeft =
            currentStep.offsetLeft - (pipeline.parentElement.clientWidth - currentStep.offsetWidth) / 2;
    }

    document.querySelectorAll("[data-filters-toggle]").forEach((button) => {
        const panel = document.getElementById(button.getAttribute("aria-controls"));
        if (!panel) return;
        button.addEventListener("click", () => {
            const open = button.getAttribute("aria-expanded") !== "true";
            button.setAttribute("aria-expanded", String(open));
            panel.hidden = !open;
            if (open) panel.querySelector("select")?.focus();
        });
    });
})();
