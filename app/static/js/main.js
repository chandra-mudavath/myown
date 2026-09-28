/* app/static/js/main.js — global utilities */
"use strict";

// API helper — wraps fetch with JSON defaults and error handling
window.api = {
    async request(method, url, body = null) {
        const opts = {
            method,
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
        };
        if (body) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        if (!res.ok) throw await res.json();
        return res.json();
    },
    get: (url) => window.api.request("GET", url),
    post: (url, body) => window.api.request("POST", url, body),
};

// Global Theme Toggle — the single source of theme behaviour for every page.
// theme-init.js applies the saved theme before first paint; this keeps it in sync.
const THEME_TOGGLE_SELECTOR = ".theme-toggle, .auth-theme-toggle";

const readSavedTheme = () => {
    try {
        return window.localStorage.getItem("theme");
    } catch {
        return null;
    }
};

const applyTheme = (theme) => {
    const isDark = theme === "dark";
    const label = isDark ? "Switch to light theme" : "Switch to dark theme";
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");

    document.querySelectorAll(THEME_TOGGLE_SELECTOR).forEach((btn) => {
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", label);
        btn.setAttribute("aria-pressed", String(isDark));
    });
};

document.addEventListener("DOMContentLoaded", () => {
    applyTheme(readSavedTheme() === "dark" ? "dark" : "light");

    document.querySelectorAll(THEME_TOGGLE_SELECTOR).forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            const nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
            applyTheme(nextTheme);
            try {
                window.localStorage.setItem("theme", nextTheme);
            } catch {
                // Storage unavailable (e.g. private mode): theme still applies for this page view.
            }
        });
    });

    // Dynamic footer year
    const footerYear = document.getElementById("footer-year");
    if (footerYear) {
        footerYear.textContent = new Date().getFullYear();
    }
});

// Keep tabs in sync when the theme is changed in another window.
window.addEventListener("storage", (e) => {
    if (e.key === "theme") applyTheme(e.newValue === "dark" ? "dark" : "light");
});

// Pages restored by the browser Back/Forward cache skip DOMContentLoaded, so
// re-read the saved theme in case it changed on another page in the meantime.
window.addEventListener("pageshow", (e) => {
    if (e.persisted) applyTheme(readSavedTheme() === "dark" ? "dark" : "light");
});

// Global avatar lightbox handlers
window.openAvatarModal = function (imageUrl) {
    const modal = document.getElementById("avatar-lightbox-modal");
    const img = document.getElementById("avatar-lightbox-img");
    if (modal && img && imageUrl) {
        img.src = imageUrl;
        modal.classList.add("is-open");
    }
};

window.closeAvatarModal = function () {
    const modal = document.getElementById("avatar-lightbox-modal");
    if (modal) {
        modal.classList.remove("is-open");
    }
};

document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") window.closeAvatarModal();
});

document.addEventListener("click", function (e) {
    const modal = document.getElementById("avatar-lightbox-modal");
    if (e.target === modal) window.closeAvatarModal();
});
