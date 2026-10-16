/* app/modules/client/static/js/client.js — shared behaviour for the signed-in client portal */
"use strict";

(function () {
    const app = document.querySelector("[data-cx-app]");
    if (!app) return;

    const store = {
        get(key) {
            try { return window.localStorage.getItem(key); } catch { return null; }
        },
        set(key, value) {
            try { window.localStorage.setItem(key, value); } catch { /* storage unavailable */ }
        },
    };

    // ── Toast ───────────────────────────────────────────
    const toastEl = document.querySelector("[data-cx-toast]");
    let toastTimer;
    window.cxToast = function (message, isError) {
        if (!toastEl) return;
        toastEl.textContent = message;
        toastEl.classList.toggle("is-error", Boolean(isError));
        toastEl.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3800);
    };

    // ── Sidebar: overlay on small screens, collapse on desktop ──
    const toggle = document.querySelector("[data-cx-sidebar-toggle]");
    const overlay = document.querySelector("[data-cx-overlay]");
    const isSmall = () => window.matchMedia("(max-width: 1023px)").matches;

    if (store.get("cx-sidebar") === "collapsed") app.classList.add("is-collapsed");

    const closeMobile = () => {
        app.classList.remove("is-open");
        if (overlay) overlay.hidden = true;
        toggle?.setAttribute("aria-expanded", "false");
    };

    toggle?.addEventListener("click", () => {
        if (isSmall()) {
            app.classList.remove("is-collapsed");
            const open = app.classList.toggle("is-open");
            if (overlay) overlay.hidden = !open;
            toggle.setAttribute("aria-expanded", String(open));
        } else {
            const collapsed = app.classList.toggle("is-collapsed");
            store.set("cx-sidebar", collapsed ? "collapsed" : "open");
            toggle.setAttribute("aria-expanded", String(!collapsed));
        }
    });
    overlay?.addEventListener("click", closeMobile);

    // ── Account menu ───────────────────────────────────
    const menuBtn = document.querySelector("[data-cx-menu-btn]");
    const menu = document.querySelector("[data-cx-menu]");
    const closeMenu = () => {
        if (!menu) return;
        menu.hidden = true;
        menuBtn?.setAttribute("aria-expanded", "false");
    };
    menuBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = menu.hidden;
        menu.hidden = !open;
        menuBtn.setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", (e) => {
        if (menu && !menu.hidden && !menu.contains(e.target)) closeMenu();
    });
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        closeMenu();
        if (isSmall()) closeMobile();
    });

    // ── Year switcher auto-submits ─────────────────────
    document.querySelectorAll("[data-cx-autosubmit]").forEach((el) =>
        el.addEventListener("change", () => el.form?.submit())
    );

    // ── Tabs / wizard steps ────────────────────────────
    // <div data-cx-tabs> … <button role="tab" aria-controls="panel-id"> … <section role="tabpanel" id="panel-id">
    // Any element with data-cx-goto="panel-id" jumps to that tab. The URL hash selects a tab on load.
    function activate(tabs, panelId, focus) {
        const buttons = tabs.querySelectorAll('[role="tab"]');
        let found = false;
        buttons.forEach((btn, i) => {
            const on = btn.getAttribute("aria-controls") === panelId;
            if (on) found = true;
            btn.setAttribute("aria-selected", String(on));
            btn.tabIndex = on ? 0 : -1;
            if (on && focus) btn.focus();
            // Wizard steps before the active one are marked done.
            if (btn.closest(".cx-steps")) btn.classList.toggle("is-done", i < [...buttons].findIndex((b) => b.getAttribute("aria-controls") === panelId));
        });
        if (!found) return false;
        tabs.querySelectorAll('[role="tabpanel"]').forEach((p) => { p.hidden = p.id !== panelId; });
        return true;
    }

    document.querySelectorAll("[data-cx-tabs]").forEach((tabs) => {
        const buttons = [...tabs.querySelectorAll('[role="tab"]')];
        buttons.forEach((btn, idx) => {
            btn.addEventListener("click", () => {
                activate(tabs, btn.getAttribute("aria-controls"));
                if (tabs.hasAttribute("data-cx-hash")) {
                    history.replaceState(null, "", "#" + btn.getAttribute("aria-controls"));
                    syncSidebarToHash();
                }
            });
            btn.addEventListener("keydown", (e) => {
                const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!dir) return;
                const next = buttons[(idx + dir + buttons.length) % buttons.length];
                activate(tabs, next.getAttribute("aria-controls"), true);
            });
        });

        const hash = window.location.hash.slice(1);
        if (!(hash && activate(tabs, hash))) {
            const initial = buttons.find((b) => b.getAttribute("aria-selected") === "true") || buttons[0];
            if (initial) activate(tabs, initial.getAttribute("aria-controls"));
        }
    });

    // Sidebar links like /client/tax-summary#payment only change the hash when you're already on the
    // page, so switch the tab on hashchange too, and highlight the matching sidebar item.
    function syncSidebarToHash() {
        const hash = window.location.hash;
        const items = [...document.querySelectorAll(".cx-nav__item[href]")];
        const path = window.location.pathname;
        if (!items.some((a) => a.getAttribute("href").startsWith(path + "#"))) return;
        const target = (hash && items.find((a) => a.getAttribute("href") === path + hash))
            || items.find((a) => a.getAttribute("href") === path);
        if (!target) return;
        items.forEach((a) => {
            const on = a === target;
            a.classList.toggle("is-active", on);
            if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
        });
    }
    window.addEventListener("hashchange", () => {
        const id = window.location.hash.slice(1);
        const tabs = id && document.getElementById(id)?.closest("[data-cx-tabs][data-cx-hash]");
        if (tabs && activate(tabs, id)) tabs.scrollIntoView({ behavior: "smooth", block: "start" });
        syncSidebarToHash();
    });
    syncSidebarToHash();

    document.addEventListener("click", (e) => {
        const go = e.target.closest("[data-cx-goto]");
        if (!go) return;
        const id = go.getAttribute("data-cx-goto");
        const tabs = document.getElementById(id)?.closest("[data-cx-tabs]");
        if (tabs && activate(tabs, id)) {
            e.preventDefault();
            tabs.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    });

    // ── Conditional sections ───────────────────────────
    // data-cx-show-when="field_name=value" (value may be "a|b") shows the element when the radio/select matches.
    const conditionals = [...document.querySelectorAll("[data-cx-show-when]")];
    function refreshConditionals() {
        conditionals.forEach((el) => {
            const [name, values] = el.getAttribute("data-cx-show-when").split("=");
            const form = el.closest("form") || document;
            const field = form.querySelector(`[name="${name}"]:checked`) || form.querySelector(`select[name="${name}"]`);
            const match = field ? values.split("|").includes(field.value) : false;
            el.hidden = !match;
            el.querySelectorAll("input, select, textarea").forEach((f) => { f.disabled = !match; });
        });
    }
    if (conditionals.length) {
        document.addEventListener("change", refreshConditionals);
        refreshConditionals();
    }

    // ── Repeatable rows ────────────────────────────────
    // [data-cx-add-row] copies the first row of the nearby [data-cx-rows] (emptied); [data-cx-remove-row]
    // removes its row, or just clears it when it's the only one left.
    const clearRow = (row) => {
        row.querySelectorAll("input").forEach((f) => { f.value = f.defaultValue; });
        row.querySelectorAll("select").forEach((f) => { f.selectedIndex = 0; });
    };
    document.addEventListener("click", (e) => {
        const add = e.target.closest("[data-cx-add-row]");
        if (add) {
            const body = add.parentElement.querySelector("[data-cx-rows]");
            if (!body?.rows.length) return;
            const row = body.rows[0].cloneNode(true);
            clearRow(row);
            body.append(row);
            row.querySelector("input")?.focus();
            return;
        }
        const remove = e.target.closest("[data-cx-remove-row]");
        if (remove) {
            const row = remove.closest("tr");
            if (row.parentElement.rows.length > 1) row.remove(); else clearRow(row);
        }
    });

    // ── Dropzones ──────────────────────────────────────
    document.querySelectorAll("[data-cx-dropzone]").forEach((zone) => {
        const input = zone.querySelector('input[type="file"]');
        const list = document.getElementById(zone.getAttribute("data-cx-dropzone"));
        const render = () => {
            if (!list || !input) return;
            list.innerHTML = "";
            [...input.files].forEach((f) => {
                const li = document.createElement("li");
                const name = document.createElement("span");
                name.textContent = f.name;
                const size = document.createElement("small");
                size.textContent = (f.size / 1024).toFixed(1) + " KB";
                li.append(name, size);
                list.append(li);
            });
        };
        ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, () => zone.classList.add("is-drag")));
        ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("is-drag")));
        input?.addEventListener("change", render);
    });

    // ── UI-only forms (no server endpoint yet) ─────────
    // data-cx-demo="Message" validates the form and shows a toast instead of posting.
    document.querySelectorAll("form[data-cx-demo]").forEach((form) =>
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            if (!form.reportValidity()) return;
            window.cxToast(form.getAttribute("data-cx-demo") || "Saved.");
            const next = form.getAttribute("data-cx-next");
            const tabs = next && document.getElementById(next)?.closest("[data-cx-tabs]");
            if (tabs && activate(tabs, next)) tabs.scrollIntoView({ behavior: "smooth", block: "start" });
        })
    );

    // ── Copy buttons ───────────────────────────────────
    document.querySelectorAll("[data-cx-copy]").forEach((btn) =>
        btn.addEventListener("click", async () => {
            const target = document.getElementById(btn.getAttribute("data-cx-copy"));
            if (!target) return;
            try {
                await navigator.clipboard.writeText(target.value || target.textContent);
                window.cxToast("Copied to clipboard.");
            } catch {
                target.select?.();
                window.cxToast("Press Ctrl+C to copy.");
            }
        })
    );

    // ── Greeting (uses the viewer's local clock) ───────
    const greet = document.querySelector("[data-cx-greeting]");
    if (greet) {
        const h = new Date().getHours();
        greet.textContent = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    }
    const today = document.querySelector("[data-cx-today]");
    if (today) {
        today.textContent = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
    }
})();
