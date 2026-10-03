/* Chat page for staff, admin and client portals. Talks to /chat/api/* (app/platform/api/chat.py).
   All text from the server is inserted with textContent, never as HTML. */
(function () {
    "use strict";

    const root = document.querySelector("[data-chat]");
    if (!root) return;

    const portal = root.dataset.portal;
    const isClient = portal === "client";
    const POLL_THREAD_MS = 8000;
    const POLL_INBOX_MS = 30000;
    const $ = (sel, el = root) => el.querySelector(sel);
    const $$ = (sel, el = root) => Array.from(el.querySelectorAll(sel));

    const state = {
        me: null,
        inbox: null,
        threadId: null,
        detail: null,
        messages: new Map(),
        lastAt: null,
        mode: "all",
        files: [],
        timers: [],
    };

    // ── helpers ────────────────────────────────────────────────────────────
    function h(tag, attrs, ...children) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === null || v === undefined || v === false) continue;
            if (k === "class") el.className = v;
            else if (k === "text") el.textContent = v;
            else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
            else if (k === "dataset") Object.assign(el.dataset, v);
            else el.setAttribute(k, v === true ? "" : v);
        }
        for (const c of children.flat()) {
            if (c === null || c === undefined || c === false) continue;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        }
        return el;
    }

    const groupIcon = () => {
        const ns = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("stroke-width", "2");
        svg.setAttribute("stroke-linecap", "round");
        svg.setAttribute("aria-hidden", "true");
        for (const d of ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M22 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"]) {
            const p = document.createElementNS(ns, "path");
            p.setAttribute("d", d);
            svg.append(p);
        }
        const c = document.createElementNS(ns, "circle");
        c.setAttribute("cx", "9"); c.setAttribute("cy", "7"); c.setAttribute("r", "4");
        svg.append(c);
        return svg;
    };

    async function api(path, options = {}) {
        const opts = { credentials: "same-origin", headers: { Accept: "application/json" }, ...options };
        if (opts.json !== undefined) {
            opts.method = opts.method || "POST";
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(opts.json);
            delete opts.json;
        }
        const res = await fetch(path, opts);
        let data = null;
        try { data = await res.json(); } catch (_) { /* empty body */ }
        if (!res.ok) {
            const detail = data && data.detail;
            const msg = typeof detail === "string" ? detail : "Something went wrong. Please try again.";
            throw new Error(msg);
        }
        return data;
    }

    const fmtTime = (iso) => {
        if (!iso) return "";
        const d = new Date(iso);
        const now = new Date();
        const sameDay = d.toDateString() === now.toDateString();
        if (sameDay) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const days = (now - d) / 86400000;
        if (days < 6) return d.toLocaleDateString([], { weekday: "short" });
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
    };
    const fmtDay = (iso) => new Date(iso).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
    const fmtSize = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

    function store(key, value) {
        try {
            if (value === undefined) return JSON.parse(localStorage.getItem("chat:" + key) || "null");
            localStorage.setItem("chat:" + key, JSON.stringify(value));
        } catch (_) { return null; }
        return null;
    }

    function setBadges(total) {
        document.querySelectorAll("[data-chat-unread]").forEach((b) => {
            b.textContent = total > 99 ? "99+" : String(total);
            b.hidden = !total;
            b.style.display = total ? "" : "none";
        });
    }

    function avatarFor(item) {
        if (item.kind === "GROUP") return h("span", { class: "chat-avatar chat-avatar--group" }, groupIcon());
        if (item.kind === "CASE") return h("span", { class: "chat-avatar chat-avatar--case", text: item.case ? String(item.case.tax_year).slice(-2) : "TX" });
        if (item.kind === "QUERY") return h("span", { class: "chat-avatar chat-avatar--query", text: "?" });
        return h("span", { class: "chat-avatar", text: item.initials || "?" });
    }

    // ── inbox ──────────────────────────────────────────────────────────────
    const chevron = () => {
        const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        s.setAttribute("viewBox", "0 0 12 12"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
        s.setAttribute("stroke-width", "2"); s.setAttribute("class", "chat-chev"); s.setAttribute("aria-hidden", "true");
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("d", "M3 4.5l3 3 3-3"); s.append(p);
        return s;
    };

    function itemRow(item) {
        const side = item.kind === "QUERY" && !isClient && item.unclaimed
            ? h("span", { class: "chat-pill chat-pill--amber", text: "Unclaimed" })
            : item.kind === "QUERY"
                ? h("span", { class: "chat-pill " + (item.status === "open" ? "chat-pill--amber" : "chat-pill--green"), text: item.status === "open" ? "Open" : "Resolved" })
                : h("span", { class: "chat-item__time", text: fmtTime(item.last_message_at) });
        const caseChip = item.case && item.kind !== "CASE" ? h("span", { class: "chat-casechip", text: item.case.case_number }) : null;
        return h("button", {
            type: "button",
            class: "chat-item" + (item.id === state.threadId ? " is-active" : "") + (item.unread ? " is-unread" : ""),
            dataset: { threadId: item.id, search: [item.title, item.subtitle, item.number, item.case && item.case.case_number].join(" ").toLowerCase() },
            onclick: () => openThread(item.id),
        },
            avatarFor(item),
            h("span", { class: "chat-item__main" },
                h("span", { class: "chat-item__top" }, h("strong", { text: item.title }), caseChip),
                h("span", { class: "chat-item__prev", text: item.preview || item.subtitle })),
            h("span", { class: "chat-item__side" },
                side,
                item.unread ? h("span", { class: "chat-unread", text: item.unread > 99 ? "99+" : String(item.unread), "aria-label": item.unread + " unread" }) : null),
        );
    }

    function sectionEl(section, extra) {
        const collapsed = store("collapsed") || [];
        const unread = section.items.reduce((n, i) => n + (i.unread || 0), 0);
        const details = h("details", { class: "chat-section", open: !collapsed.includes(section.key) },
            h("summary", {},
                chevron(),
                h("span", { text: section.label }),
                h("span", { class: "chat-count" + (unread ? "" : " is-zero"), text: unread ? String(unread) : String(section.items.length) }),
                section.can_add ? h("button", {
                    type: "button", class: "chat-plus", "aria-label": "New " + (section.key === "groups" ? "group" : "direct message"),
                    onclick: (e) => { e.preventDefault(); openNewDialog(section.key === "groups" ? "group" : "direct"); },
                }, "+") : null),
            section.items.map(itemRow),
            extra || null,
            !section.items.length && !extra ? h("p", { class: "chat-muted chat-pad", text: "Nothing here yet." }) : null,
        );
        details.addEventListener("toggle", () => {
            const list = new Set(store("collapsed") || []);
            details.open ? list.delete(section.key) : list.add(section.key);
            store("collapsed", Array.from(list));
        });
        return details;
    }

    function renderInbox() {
        const box = $("[data-chat-sections]");
        box.replaceChildren();
        for (const s of state.inbox.sections) {
            let extra = null;
            if (s.key === "filings" && state.inbox.filings_without_chat.length) {
                extra = h("div", { class: "chat-start" },
                    h("p", { class: "chat-muted", text: "Start a chat about a filing:" }),
                    state.inbox.filings_without_chat.map((f) => h("button", {
                        type: "button", class: "chat-btn chat-btn--soft chat-btn--sm",
                        onclick: () => openCase(f.id),
                    }, `Tax year ${f.tax_year} · ${f.case_number}`)));
            }
            box.append(sectionEl(s, extra));
        }
        if (state.me && state.me.admin) {
            const hidden = h("details", { class: "chat-section chat-section--hidden" },
                h("summary", {}, chevron(), h("span", { text: "Hidden (audit)" })),
                h("p", { class: "chat-muted chat-pad", text: "Loading…" }));
            hidden.addEventListener("toggle", async () => {
                if (!hidden.open) return;
                try {
                    const data = await api("/chat/api/hidden");
                    hidden.querySelectorAll(".chat-item, p").forEach((n) => n.remove());
                    if (!data.items.length) hidden.append(h("p", { class: "chat-muted chat-pad", text: "No hidden conversations." }));
                    data.items.forEach((i) => hidden.append(itemRow(i)));
                } catch (err) { hidden.append(h("p", { class: "chat-error", text: err.message })); }
            });
            box.append(hidden);
        }
        applyFilter();
        const total = state.inbox.sections.reduce((n, s) => n + s.items.reduce((m, i) => m + (i.unread || 0), 0), 0);
        setBadges(total);
    }

    async function loadInbox() {
        try {
            state.inbox = await api("/chat/api/inbox");
            state.me = state.inbox.me;
            renderInbox();
        } catch (err) {
            $("[data-chat-sections]").replaceChildren(h("p", { class: "chat-error chat-pad", text: err.message }));
        }
    }

    function applyFilter() {
        const q = ($("[data-chat-filter]").value || "").trim().toLowerCase();
        $$(".chat-item").forEach((el) => { el.hidden = q && !(el.dataset.search || "").includes(q); });
    }

    // ── thread ─────────────────────────────────────────────────────────────
    function showConversation(on) {
        $("[data-chat-empty]").hidden = on;
        $("[data-chat-thread]").hidden = !on;
        root.classList.toggle("is-open-thread", on);
    }

    async function openCase(filingId) {
        try {
            const { thread_id } = await api(`/chat/api/cases/${encodeURIComponent(filingId)}/open`, { method: "POST" });
            await loadInbox();
            await openThread(thread_id);
        } catch (err) { toast(err.message); }
    }

    async function openThread(id) {
        state.threadId = id;
        state.messages = new Map();
        state.lastAt = null;
        state.files = [];
        renderFiles();
        $$(".chat-item").forEach((el) => el.classList.toggle("is-active", el.dataset.threadId === id));
        try {
            state.detail = await api(`/chat/api/threads/${encodeURIComponent(id)}`);
        } catch (err) {
            toast(err.message);
            showConversation(false);
            return;
        }
        renderHead();
        $("[data-chat-messages]").replaceChildren();
        showConversation(true);
        await pollMessages(true);
        const url = new URL(window.location.href);
        url.searchParams.set("thread", id);
        url.searchParams.delete("case");
        history.replaceState(null, "", url);
        const row = $(`.chat-item[data-thread-id="${CSS.escape(id)}"]`);
        if (row) { row.classList.remove("is-unread"); const u = row.querySelector(".chat-unread"); if (u) u.remove(); }
        if (!state.detail.read_only) $("[data-chat-input]").focus();
    }

    function renderHead() {
        const d = state.detail;
        const avatar = avatarFor(d);
        avatar.setAttribute("data-chat-head-avatar", "");
        $("[data-chat-head-avatar]").replaceWith(avatar);
        $("[data-chat-head-title]").textContent = d.title;
        const sub = [d.subtitle];
        if (d.kind === "QUERY" && d.owner && !isClient) sub.push("Handled by " + d.owner);
        if (d.number) sub.push(d.number);
        $("[data-chat-head-sub]").textContent = sub.filter(Boolean).join(" · ");

        const members = $("[data-chat-head-members]");
        members.replaceChildren();
        if (d.kind === "GROUP" && d.members.length) {
            const stack = h("button", { type: "button", class: "chat-stack", title: "Members", onclick: () => openMembers() },
                d.members.slice(0, 4).map((m) => h("span", { class: "chat-avatar chat-avatar--xs", text: m.initials })),
                h("span", { class: "chat-stack__count", text: `${d.members.length} members` }));
            members.append(stack);
        }

        const actions = $("[data-chat-head-actions]");
        actions.replaceChildren();
        const btn = (label, fn, cls = "") => h("button", { type: "button", class: "chat-btn chat-btn--sm " + cls, onclick: fn }, label);
        if (d.case && d.case.url) actions.append(h("a", { class: "chat-btn chat-btn--sm chat-btn--soft", href: d.case.url }, d.kind === "CASE" && isClient ? "View filing" : "Open case " + d.case.case_number));
        if (d.kind === "GROUP" && (d.perms.manage || d.perms.leave)) actions.append(btn("Members", openMembers));
        if (d.perms.claim) actions.append(btn(d.owner ? "Take over" : "Claim", () => action("claim", { account_id: null })));
        if (d.perms.assign) actions.append(btn("Assign", openAssign));
        if (d.perms.resolve) actions.append(btn("Mark resolved", () => action("resolve"), "chat-btn--soft"));
        if (d.perms.reopen) actions.append(btn("Reopen", () => action("reopen")));
        if (d.perms.restore) actions.append(btn("Restore", () => action("restore"), "chat-btn--primary"));

        const banner = $("[data-chat-banner]");
        banner.hidden = true;
        if (d.read_only) {
            banner.hidden = false;
            banner.textContent = "This conversation is hidden from everyone. You're viewing it read-only for audit.";
        } else if (d.kind === "QUERY" && d.status !== "open") {
            banner.hidden = false;
            banner.textContent = isClient ? "This question is resolved. Reply below if you need more help, and it reopens." : "This question is resolved.";
        } else if (!isClient && d.kind === "CASE") {
            banner.hidden = false;
            banner.textContent = "The client sees replies. Internal notes are visible only to staff and admins.";
        }

        const compose = $("[data-chat-compose]");
        compose.hidden = !d.perms.post;
        $("[data-chat-mode]").hidden = !d.perms.note;
        setMode("all");
        $("[data-chat-input]").placeholder = d.kind === "CASE" && !isClient ? "Write to the client…" : "Write a message…";
    }

    function setMode(mode) {
        state.mode = mode;
        $$(".chat-mode").forEach((b) => {
            const on = b.dataset.mode === mode;
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        root.classList.toggle("is-note-mode", mode === "internal");
        $("[data-chat-hint]").textContent = mode === "internal"
            ? "Internal note: only staff and admins see this"
            : "Enter to send · Shift+Enter for a new line";
    }

    async function action(name, json) {
        const id = state.threadId;
        try {
            await api(`/chat/api/threads/${encodeURIComponent(id)}/${name}`, json ? { json } : { method: "POST" });
            await loadInbox();
            await openThread(id);
        } catch (err) { toast(err.message); }
    }

    // ── messages ───────────────────────────────────────────────────────────
    function messageEl(m) {
        if (m.kind === "system") {
            return h("div", { class: "chat-sys", dataset: { msgId: m.id } }, m.body, h("time", { text: " · " + fmtTime(m.created_at) }));
        }
        const cls = ["chat-msg", m.mine ? "chat-msg--me" : "chat-msg--them"];
        if (m.visibility === "internal") cls.push("chat-msg--note");
        if (m.deleted) cls.push("chat-msg--deleted");
        const label = m.visibility === "internal" ? "Internal note · only staff see this" : (m.mine ? "" : m.sender);
        const bubble = h("div", { class: "chat-bubble" },
            m.deleted ? h("em", { text: "Message removed" }) : (m.body ? h("p", { text: m.body }) : null),
            m.attachments.map((a) => h("a", { class: "chat-file", href: a.url, target: "_blank", rel: "noopener" },
                h("span", { class: "chat-file__ext", text: (a.name.split(".").pop() || "file").slice(0, 4).toUpperCase() }),
                h("span", { class: "chat-file__name", text: a.name }),
                h("span", { class: "chat-file__size", text: fmtSize(a.size) }))));
        const meta = h("div", { class: "chat-msg__meta" },
            h("time", { datetime: m.created_at, text: fmtTime(m.created_at) }),
            m.edited && !m.deleted ? h("span", { text: " · edited" }) : null,
            m.can_edit ? h("button", { type: "button", class: "chat-link", onclick: () => editMessage(m) }, "Edit") : null,
            m.mine && !m.deleted ? h("button", { type: "button", class: "chat-link", onclick: () => deleteMessage(m) }, "Delete") : null);
        return h("div", { class: cls.join(" "), dataset: { msgId: m.id } },
            label ? h("span", { class: "chat-msg__by", text: label }) : null, bubble, meta);
    }

    function renderMessages(list, initial) {
        const box = $("[data-chat-messages]");
        const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
        let lastDay = null;
        const existing = $$(".chat-day", box);
        if (existing.length) lastDay = existing[existing.length - 1].dataset.day;
        for (const m of list) {
            const known = state.messages.get(m.id);
            state.messages.set(m.id, m);
            if (!state.lastAt || m.created_at > state.lastAt) state.lastAt = m.created_at;
            if (known) {
                const old = box.querySelector(`[data-msg-id="${CSS.escape(m.id)}"]`);
                if (old && JSON.stringify(known) !== JSON.stringify(m)) old.replaceWith(messageEl(m));
                continue;
            }
            const day = new Date(m.created_at).toDateString();
            if (day !== lastDay) {
                box.append(h("div", { class: "chat-day", dataset: { day }, text: fmtDay(m.created_at) }));
                lastDay = day;
            }
            box.append(messageEl(m));
        }
        if (initial && !list.length) box.append(h("p", { class: "chat-muted chat-pad chat-first", text: "No messages yet. Say hello." }));
        if (list.length) { const first = $(".chat-first", box); if (first) first.remove(); }
        if (initial || nearBottom) box.scrollTop = box.scrollHeight;
    }

    async function pollMessages(initial) {
        if (!state.threadId) return;
        const id = state.threadId;
        const q = !initial && state.lastAt ? "?after=" + encodeURIComponent(state.lastAt) : "";
        try {
            const data = await api(`/chat/api/threads/${encodeURIComponent(id)}/messages${q}`);
            if (id === state.threadId) renderMessages(data.messages, initial);
        } catch (_) { /* next poll retries */ }
    }

    async function editMessage(m) {
        const el = $(`[data-msg-id="${CSS.escape(m.id)}"] .chat-bubble`);
        if (!el) return;
        const area = h("textarea", { class: "chat-edit", rows: "3", maxlength: "4000" });
        area.value = m.body;
        const save = h("button", { type: "button", class: "chat-btn chat-btn--sm chat-btn--primary" }, "Save");
        const cancel = h("button", { type: "button", class: "chat-btn chat-btn--sm" }, "Cancel");
        const wrap = h("div", { class: "chat-editwrap" }, area, h("div", { class: "chat-editwrap__btns" }, cancel, save));
        el.replaceWith(wrap);
        area.focus();
        cancel.onclick = () => wrap.replaceWith(messageEl(m).querySelector(".chat-bubble"));
        save.onclick = async () => {
            try {
                const { message } = await api(`/chat/api/messages/${encodeURIComponent(m.id)}/edit`, { json: { body: area.value } });
                state.messages.set(message.id, message);
                $(`[data-msg-id="${CSS.escape(m.id)}"]`).replaceWith(messageEl(message));
            } catch (err) { toast(err.message); }
        };
    }

    async function deleteMessage(m) {
        if (!(await confirmBox("Delete this message? It will show as removed for everyone."))) return;
        try {
            const { message } = await api(`/chat/api/messages/${encodeURIComponent(m.id)}/delete`, { method: "POST" });
            state.messages.set(message.id, message);
            $(`[data-msg-id="${CSS.escape(m.id)}"]`).replaceWith(messageEl(message));
        } catch (err) { toast(err.message); }
    }

    // ── composer ───────────────────────────────────────────────────────────
    function renderFiles() {
        const box = $("[data-chat-files]");
        box.replaceChildren(...state.files.map((f, i) => h("span", { class: "chat-chip" }, f.name,
            h("button", { type: "button", "aria-label": "Remove " + f.name, onclick: () => { state.files.splice(i, 1); renderFiles(); } }, "×"))));
        box.hidden = !state.files.length;
    }

    async function send(e) {
        e.preventDefault();
        const input = $("[data-chat-input]");
        const body = input.value.trim();
        if (!body && !state.files.length) return;
        const fd = new FormData();
        fd.append("body", body);
        fd.append("visibility", state.mode);
        state.files.forEach((f) => fd.append("files", f));
        const btn = $("[data-chat-send]");
        btn.disabled = true;
        try {
            const { message } = await api(`/chat/api/threads/${encodeURIComponent(state.threadId)}/messages`, { method: "POST", body: fd });
            input.value = "";
            autoGrow(input);
            state.files = [];
            renderFiles();
            renderMessages([message], false);
            $("[data-chat-messages]").scrollTop = $("[data-chat-messages]").scrollHeight;
            loadInbox();
        } catch (err) {
            toast(err.message);
        } finally {
            btn.disabled = false;
        }
    }

    function autoGrow(el) {
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 180) + "px";
    }

    // ── dialogs ────────────────────────────────────────────────────────────
    function dialog(name) { return $(`[data-chat-dialog="${name}"]`); }

    function confirmBox(text) {
        const d = dialog("confirm");
        $("[data-confirm-text]", d).textContent = text;
        d.returnValue = "";
        d.showModal();
        return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "yes"), { once: true }));
    }

    $$("[data-chat-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

    function debounce(fn, ms) {
        let t;
        return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
    }

    function personRow(p, onPick, picked) {
        return h("button", { type: "button", class: "chat-person" + (picked ? " is-picked" : ""), role: "option", onclick: () => onPick(p) },
            h("span", { class: "chat-avatar chat-avatar--xs", text: p.initials }),
            h("span", {}, h("strong", { text: p.name }), h("small", { text: [p.type, p.number].filter(Boolean).join(" · ") })));
    }

    // New direct / group
    const newState = { tab: "direct", people: [], caseItem: null };

    function openNewDialog(tab) {
        const d = dialog("new");
        if (!d) return;
        newState.people = [];
        newState.caseItem = null;
        $("[data-new-name]", d).value = "";
        $("[data-new-search]", d).value = "";
        const caseSearch = $("[data-new-case-search]", d);
        if (caseSearch) caseSearch.value = "";
        $("[data-new-error]", d).hidden = true;
        setNewTab(tab);
        searchPeople("");
        renderNewChosen();
        d.showModal();
        $("[data-new-search]", d).focus();
    }

    function setNewTab(tab) {
        const d = dialog("new");
        newState.tab = tab;
        $$("[data-new-tab]", d).forEach((b) => {
            const on = b.dataset.newTab === tab;
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-selected", on ? "true" : "false");
        });
        $$("[data-new-only]", d).forEach((el) => { el.hidden = el.dataset.newOnly !== tab; });
        $("[data-new-people-label]", d).textContent = tab === "group" ? "Members (at least 2)" : "Person";
        $("[data-new-submit]", d).textContent = tab === "group" ? "Create group" : "Start chat";
        if (tab === "direct" && newState.people.length > 1) newState.people = newState.people.slice(0, 1);
        renderNewChosen();
    }

    function renderNewChosen() {
        const d = dialog("new");
        if (!d) return;
        $("[data-new-chosen]", d).replaceChildren(...newState.people.map((p) => h("span", { class: "chat-chip" }, p.name,
            h("button", { type: "button", "aria-label": "Remove " + p.name, onclick: () => { newState.people = newState.people.filter((x) => x.account_id !== p.account_id); renderNewChosen(); } }, "×"))));
        const caseBox = $("[data-new-case-chosen]", d);
        if (caseBox) {
            caseBox.replaceChildren(newState.caseItem ? h("span", { class: "chat-chip chat-chip--case" }, `${newState.caseItem.case_number} · ${newState.caseItem.client_name}`,
                h("button", { type: "button", "aria-label": "Remove filing", onclick: () => { newState.caseItem = null; renderNewChosen(); } }, "×")) : "");
        }
    }

    const searchPeople = debounce(async (q) => {
        const d = dialog("new");
        if (!d) return;
        try {
            const { people } = await api("/chat/api/people?q=" + encodeURIComponent(q));
            $("[data-new-results]", d).replaceChildren(...people.map((p) => personRow(p, (picked) => {
                if (newState.tab === "direct") newState.people = [picked];
                else if (!newState.people.some((x) => x.account_id === picked.account_id)) newState.people.push(picked);
                renderNewChosen();
            }, newState.people.some((x) => x.account_id === p.account_id))));
            if (!people.length) $("[data-new-results]", d).append(h("p", { class: "chat-muted chat-pad", text: "No one matches." }));
        } catch (err) { $("[data-new-results]", d).replaceChildren(h("p", { class: "chat-error", text: err.message })); }
    }, 200);

    const searchCases = debounce(async (q) => {
        const d = dialog("new");
        const box = d && $("[data-new-case-results]", d);
        if (!box) return;
        if (!q.trim()) { box.replaceChildren(); return; }
        try {
            const { cases } = await api("/chat/api/cases?q=" + encodeURIComponent(q));
            box.replaceChildren(...cases.map((c) => h("button", { type: "button", class: "chat-person", role: "option", onclick: () => { newState.caseItem = c; box.replaceChildren(); $("[data-new-case-search]", d).value = ""; renderNewChosen(); } },
                h("span", { class: "chat-avatar chat-avatar--xs chat-avatar--case", text: String(c.tax_year).slice(-2) }),
                h("span", {}, h("strong", { text: c.case_number }), h("small", { text: `${c.client_name} · ${c.tax_year}` })))));
            if (!cases.length) box.append(h("p", { class: "chat-muted chat-pad", text: "No filing matches." }));
        } catch (err) { box.replaceChildren(h("p", { class: "chat-error", text: err.message })); }
    }, 250);

    async function submitNew(e) {
        e.preventDefault();
        const d = dialog("new");
        const err = $("[data-new-error]", d);
        err.hidden = true;
        const caseId = newState.caseItem ? newState.caseItem.id : null;
        try {
            let res;
            if (newState.tab === "direct") {
                if (!newState.people.length) throw new Error("Pick a person.");
                res = await api("/chat/api/direct", { json: { account_id: newState.people[0].account_id, case_id: caseId } });
            } else {
                res = await api("/chat/api/groups", { json: { name: $("[data-new-name]", d).value, member_ids: newState.people.map((p) => p.account_id), case_id: caseId } });
            }
            d.close();
            await loadInbox();
            await openThread(res.thread_id);
        } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    }

    // Group members / admin assign
    const membersState = { mode: "members" };

    function renderMembersDialog() {
        const d = dialog("members");
        const det = state.detail;
        const assign = membersState.mode === "assign";
        $("#chat-members-title", d).textContent = assign ? "Assign this question" : "Members";
        const rename = $("[data-members-rename]", d);
        rename.hidden = assign || !det.perms.manage;
        $("[data-members-name]", d).value = det.title;
        const list = $("[data-members-list]", d);
        list.replaceChildren();
        if (!assign) {
            det.members.forEach((m) => list.append(h("li", {},
                h("span", { class: "chat-avatar chat-avatar--xs", text: m.initials }),
                h("span", { class: "chat-memberlist__name" }, h("strong", { text: m.name }), h("small", { text: (m.owner ? "Owner · " : "") + (m.role || "") })),
                det.perms.manage && !m.owner ? h("button", { type: "button", class: "chat-link", onclick: () => removeMember(m) }, "Remove") : null)));
            if (det.perms.leave) list.append(h("li", { class: "chat-memberlist__leave" }, h("button", { type: "button", class: "chat-btn chat-btn--sm chat-btn--danger", onclick: leaveGroup }, "Leave group")));
        }
        $("[data-members-add]", d).hidden = !(assign || det.perms.manage);
        $("[data-members-error]", d).hidden = true;
        $("[data-members-search]", d).value = "";
        $("[data-members-results]", d).replaceChildren();
        if (assign || det.perms.manage) searchMembers("");
    }

    function openMembers() { membersState.mode = "members"; renderMembersDialog(); dialog("members").showModal(); }
    function openAssign() { membersState.mode = "assign"; renderMembersDialog(); dialog("members").showModal(); }

    const searchMembers = debounce(async (q) => {
        const d = dialog("members");
        try {
            const { people } = await api("/chat/api/people?q=" + encodeURIComponent(q));
            const current = new Set((state.detail.members || []).map((m) => m.account_id));
            const list = membersState.mode === "assign" ? people : people.filter((p) => !current.has(p.account_id));
            $("[data-members-results]", d).replaceChildren(...list.map((p) => personRow(p, membersState.mode === "assign" ? assignTo : addMember)));
        } catch (err) { showMembersError(err.message); }
    }, 200);

    function showMembersError(msg) { const e = $("[data-members-error]", dialog("members")); e.textContent = msg; e.hidden = false; }

    async function refreshDetail() {
        state.detail = await api(`/chat/api/threads/${encodeURIComponent(state.threadId)}`);
        renderHead();
        pollMessages(false);
        loadInbox();
    }

    async function addMember(p) {
        try { await api(`/chat/api/groups/${state.threadId}/members`, { json: { account_ids: [p.account_id] } }); await refreshDetail(); renderMembersDialog(); }
        catch (err) { showMembersError(err.message); }
    }

    async function removeMember(m) {
        if (!(await confirmBox(`Remove ${m.name} from the group?`))) return;
        try { await api(`/chat/api/groups/${state.threadId}/members/${m.account_id}/remove`, { method: "POST" }); await refreshDetail(); renderMembersDialog(); }
        catch (err) { showMembersError(err.message); }
    }

    async function leaveGroup() {
        if (!(await confirmBox("Leave this group? You won't see new messages."))) return;
        try {
            await api(`/chat/api/groups/${state.threadId}/leave`, { method: "POST" });
            dialog("members").close();
            state.threadId = null;
            showConversation(false);
            loadInbox();
        } catch (err) { showMembersError(err.message); }
    }

    async function assignTo(p) {
        try { await api(`/chat/api/threads/${state.threadId}/claim`, { json: { account_id: p.account_id } }); dialog("members").close(); await refreshDetail(); }
        catch (err) { showMembersError(err.message); }
    }

    // Client: ask a question
    async function openAsk() {
        const d = dialog("ask");
        if (!d) return;
        const form = $("[data-chat-ask-form]", d);
        form.reset();
        $("[data-ask-error]", d).hidden = true;
        try {
            const [{ topics }, { cases }] = await Promise.all([api("/chat/api/topics"), api("/chat/api/cases")]);
            $("[data-ask-topic]", d).replaceChildren(...topics.map((t) => h("option", { value: t.id, text: t.name })));
            $("[data-ask-case]", d).replaceChildren(h("option", { value: "", text: "Not about a specific filing" }),
                ...cases.map((c) => h("option", { value: c.id, text: `Tax year ${c.tax_year} · ${c.case_number}` })));
        } catch (err) { toast(err.message); return; }
        d.showModal();
    }

    async function submitAsk(e) {
        e.preventDefault();
        const d = dialog("ask");
        const err = $("[data-ask-error]", d);
        err.hidden = true;
        try {
            const { thread_id } = await api("/chat/api/queries", { method: "POST", body: new FormData(e.target) });
            d.close();
            await loadInbox();
            await openThread(thread_id);
        } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    }

    // ── toast ──────────────────────────────────────────────────────────────
    function toast(text) {
        let t = document.querySelector(".chat-toast");
        if (!t) { t = h("div", { class: "chat-toast", role: "status", "aria-live": "polite" }); document.body.append(t); }
        t.textContent = text;
        t.classList.add("is-on");
        clearTimeout(t._timer);
        t._timer = setTimeout(() => t.classList.remove("is-on"), 4000);
    }

    // ── wiring ─────────────────────────────────────────────────────────────
    $("[data-chat-filter]").addEventListener("input", applyFilter);
    $("[data-chat-compose]").addEventListener("submit", send);
    const input = $("[data-chat-input]");
    input.addEventListener("input", () => autoGrow(input));
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("[data-chat-compose]").requestSubmit(); }
    });
    $("[data-chat-file]").addEventListener("change", (e) => {
        state.files.push(...Array.from(e.target.files));
        e.target.value = "";
        renderFiles();
    });
    $$(".chat-mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    $("[data-chat-back]").addEventListener("click", () => { showConversation(false); state.threadId = null; });

    $$("[data-chat-new]").forEach((b) => b.addEventListener("click", () => openNewDialog(b.dataset.chatNew)));
    const newDialog = dialog("new");
    if (newDialog) {
        $$("[data-new-tab]", newDialog).forEach((b) => b.addEventListener("click", () => setNewTab(b.dataset.newTab)));
        $("[data-new-search]", newDialog).addEventListener("input", (e) => searchPeople(e.target.value));
        const cs = $("[data-new-case-search]", newDialog);
        if (cs) cs.addEventListener("input", (e) => searchCases(e.target.value));
        $("[data-chat-new-form]", newDialog).addEventListener("submit", submitNew);
    }
    const membersDialog = dialog("members");
    if (membersDialog) {
        $("[data-members-search]", membersDialog).addEventListener("input", (e) => searchMembers(e.target.value));
        $("[data-members-rename]", membersDialog).addEventListener("submit", async (e) => {
            e.preventDefault();
            try { await api(`/chat/api/groups/${state.threadId}/rename`, { json: { name: $("[data-members-name]", membersDialog).value } }); await refreshDetail(); }
            catch (err) { showMembersError(err.message); }
        });
    }
    const askBtn = $("[data-chat-ask]");
    if (askBtn) askBtn.addEventListener("click", openAsk);
    const askDialog = dialog("ask");
    if (askDialog) $("[data-chat-ask-form]", askDialog).addEventListener("submit", submitAsk);

    // Polling pauses while the tab is in the background.
    state.timers.push(setInterval(() => { if (!document.hidden) pollMessages(false); }, POLL_THREAD_MS));
    state.timers.push(setInterval(() => { if (!document.hidden) loadInbox(); }, POLL_INBOX_MS));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollMessages(false); loadInbox(); } });

    (async () => {
        await loadInbox();
        if (root.dataset.openCase) await openCase(root.dataset.openCase);
        else if (root.dataset.openThread) await openThread(root.dataset.openThread);
    })();
})();
