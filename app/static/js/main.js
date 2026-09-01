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
