/* Fills the sidebar "Messages" unread badge on every portal page. The chat page keeps it current itself. */
(function () {
    "use strict";
    const badges = document.querySelectorAll("[data-chat-unread]");
    if (!badges.length || document.querySelector("[data-chat]")) return;
    fetch("/chat/api/unread", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
            if (!data) return;
            badges.forEach((b) => {
                b.textContent = data.total > 99 ? "99+" : String(data.total);
                b.hidden = !data.total;
                b.style.display = data.total ? "" : "none";
            });
        })
        .catch(() => {});
})();
