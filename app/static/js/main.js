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

// Global Theme Toggle
document.addEventListener("DOMContentLoaded", () => {
    const applyTheme = (theme) => {
        const isDark = theme === "dark";
        document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');

        // Update all theme toggle buttons on the page
        document.querySelectorAll(".theme-toggle").forEach(btn => {
            btn.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
            btn.setAttribute("title", isDark ? "Switch to light theme" : "Switch to dark theme");
            btn.setAttribute("data-theme", isDark ? "dark" : "light");
            btn.setAttribute("aria-pressed", String(isDark));
        });
    };

    // Initial sync with localStorage
    const savedTheme = window.localStorage.getItem("theme");
    applyTheme(savedTheme === "dark" ? "dark" : "light");

    document.querySelectorAll(".theme-toggle").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
            applyTheme(nextTheme);
            window.localStorage.setItem("theme", nextTheme);
        });
    });
});
