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

    // 50 rows a page for the Dashboard clients and Notifications tables (client feedback, Nov 2026).
    // The filters pick the matching rows; the pager shows one page of them and fills in the footer.
    const PAGE_SIZE = 50;
    function makePager(foot) {
        let items = [];
        let page = 1;
        const btn = (label, target, opts = {}) => {
            const el = document.createElement(opts.disabled || opts.current ? "span" : "button");
            el.className = opts.num ? "staff-pager__num" + (opts.current ? " is-current" : "") : "staff-pager__btn" + (opts.disabled ? " is-disabled" : "");
            if (el.tagName === "BUTTON") {
                el.type = "button";
                el.addEventListener("click", () => {
                    page = target;
                    render();
                    foot.closest("section")?.scrollIntoView({ behavior: "smooth", block: "start" });
                });
            }
            if (opts.current) el.setAttribute("aria-current", "page");
            if (opts.disabled) el.setAttribute("aria-disabled", "true");
            el.textContent = label;
            return el;
        };
        function render() {
            if (!foot) return;
            const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
            page = Math.min(Math.max(page, 1), pages);
            const start = (page - 1) * PAGE_SIZE;
            items.forEach((row, i) => { row.hidden = i < start || i >= start + PAGE_SIZE; });
            foot.hidden = items.length === 0;
            foot.innerHTML = "";
            const range = document.createElement("p");
            range.className = "staff-cases-range";
            range.textContent = `Showing ${(start + 1).toLocaleString()} to ${Math.min(start + PAGE_SIZE, items.length).toLocaleString()} of ${items.length.toLocaleString()} entries`;
            foot.appendChild(range);
            if (pages > 1) {
                const nav = document.createElement("nav");
                nav.className = "staff-pager";
                nav.setAttribute("aria-label", "Pagination");
                nav.appendChild(btn("‹ Previous", page - 1, { disabled: page === 1 }));
                const wanted = new Set([1, pages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= pages));
                let last = 0;
                [...wanted].sort((a, b) => a - b).forEach((n) => {
                    if (n - last > 1) {
                        const gap = document.createElement("span");
                        gap.className = "staff-pager__gap";
                        gap.textContent = "…";
                        nav.appendChild(gap);
                    }
                    nav.appendChild(btn(String(n), n, { num: true, current: n === page }));
                    last = n;
                });
                nav.appendChild(btn("Next ›", page + 1, { disabled: page === pages }));
                foot.appendChild(nav);
            }
        }
        return { set(list) { items = list; page = 1; render(); } };
    }

    // Dashboard clients list: instant filter over the rows already on the page.
    const clientFilter = document.querySelector("[data-client-filter]");
    if (clientFilter) {
        const rows = [...document.querySelectorAll("[data-client-row]")];
        const empty = document.querySelector("[data-client-empty]");
        const count = document.querySelector("[data-client-count]");
        const pager = makePager(document.querySelector('[data-pager="client"]'));
        const applyClients = () => {
            const term = clientFilter.value.trim().toLowerCase();
            const matches = rows.filter((row) => !term || row.dataset.search.includes(term));
            { const keep = new Set(matches); rows.forEach((row) => { row.hidden = !keep.has(row); }); }
            if (empty) empty.hidden = matches.length > 0;
            if (count) count.textContent = matches.length.toLocaleString();
            pager.set(matches);
        };
        clientFilter.addEventListener("input", applyClients);
        applyClients();
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
                const comment = form.querySelector("textarea[name=comment]");
                if (comment) comment.value = "";
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

    // One-shot confirmation message after a redirect (e.g. a stage move). A refused move comes back
    // with ?move_error=… instead, which replaces the "moved" message.
    try {
        const params = new URLSearchParams(window.location.search);
        const moveError = params.get("move_error");
        let flash = sessionStorage.getItem("staff-flash");
        if (moveError) {
            flash = moveError;
            params.delete("move_error");
            history.replaceState(null, "", window.location.pathname + (params.toString() ? "?" + params : "") + window.location.hash);
        }
        if (flash) {
            sessionStorage.removeItem("staff-flash");
            const toast = document.createElement("div");
            toast.className = "staff-toast" + (moveError ? " staff-toast--error" : "");
            toast.setAttribute("role", "status");
            toast.textContent = flash;
            document.body.append(toast);
            setTimeout(() => toast.classList.add("is-leaving"), 3500);
            setTimeout(() => toast.remove(), 4000);
        }
    } catch (e) { /* storage unavailable */ }

    // Notifications page: filter rows by activity type or "new".
    // Notifications: activity filter buttons plus a search box (client feedback, Nov 2026); both apply together.
    const notifFilter = document.querySelector("[data-notif-filter]");
    if (notifFilter) {
        const buttons = [...notifFilter.querySelectorAll("[data-filter]")];
        const rows = [...document.querySelectorAll("[data-notif-row]")];
        const none = document.querySelector("[data-notif-none]");
        const search = document.querySelector("[data-notif-search]");
        const count = document.querySelector("[data-notif-count]");
        let key = "all";
        const pager = makePager(document.querySelector('[data-pager="notif"]'));
        const apply = () => {
            const term = (search?.value || "").trim().toLowerCase();
            const matches = rows.filter((row) => {
                const kindOk = key === "all" || (key === "new" ? row.dataset.new === "yes" : row.dataset.kind === key);
                return kindOk && (!term || (row.dataset.search || "").includes(term));
            });
            { const keep = new Set(matches); rows.forEach((row) => { row.hidden = !keep.has(row); }); }
            if (none) none.hidden = matches.length > 0;
            if (count) count.textContent = matches.length.toLocaleString();
            pager.set(matches);
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
        search?.addEventListener("input", apply);
        apply();
    }

    // Case page tabs (client feedback, Nov 2026): a client tab in the sidebar (or a stage in the tracker) shows only
    // that part of the case under the case header and tracker. #tab-<name> picks the tab; an older #sec-… link opens
    // the tab holding that section. No hash: Personal Info.
    const caseTabs = document.querySelector("[data-case-tabs]");
    if (caseTabs) {
        const sections = [...caseTabs.querySelectorAll(".case-section[data-tab]")];
        const navLinks = [...document.querySelectorAll(".staff-clientnav__item")];
        const tabsOf = (el) => (el.dataset.tab || "").split(" ");
        const showTab = (fromClick) => {
            const hash = decodeURIComponent(location.hash.slice(1));
            let tab = hash.startsWith("tab-") ? hash.slice(4) : "";
            let target = null;
            if (!tab && hash) {
                target = document.getElementById(hash);
                const section = target?.closest(".case-section[data-tab]");
                if (section) tab = tabsOf(section)[0];
            }
            if (!sections.some((sec) => tabsOf(sec).includes(tab))) tab = "personal";
            sections.forEach((sec) => { sec.hidden = !tabsOf(sec).includes(tab); });
            caseTabs.classList.add("is-tabbed");
            navLinks.forEach((link) => {
                const on = link.getAttribute("href").endsWith("#tab-" + tab) && link.pathname === location.pathname;
                link.classList.toggle("active", on);
                if (on) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
            });
            if (target && !target.classList.contains("case-section")) target.scrollIntoView({ block: "start" });
            else if (fromClick) caseTabs.scrollIntoView({ behavior: "smooth", block: "start" });
        };
        window.addEventListener("hashchange", () => showTab(true));
        showTab(false);
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
