/* app/platform/static/js/auth.js — Login & Register form enhancements */
"use strict";

// ── Password visibility toggle ──────────────────────────────────
const toggleBtn = document.getElementById("toggle-password");
const passwordInput = document.getElementById("password");

if (toggleBtn && passwordInput) {
    toggleBtn.addEventListener("click", () => {
        const isText = passwordInput.type === "text";
        passwordInput.type = isText ? "password" : "text";
        toggleBtn.setAttribute("aria-label", isText ? "Show password" : "Hide password");
        toggleBtn.setAttribute("title", isText ? "Show password" : "Hide password");
        toggleBtn.classList.toggle("is-visible", !isText);
    });
}

// ── Password strength meter (register page) ─────────────────────
if (passwordInput && document.getElementById("bar-1")) {
    passwordInput.addEventListener("input", () => {
        const val = passwordInput.value;
        let score = 0;
        if (val.length >= 8) score++;
        if (/[A-Z]/.test(val)) score++;
        if (/[0-9]/.test(val)) score++;
        if (/[^A-Za-z0-9]/.test(val)) score++;

        const colors = ["bg-red-500", "bg-orange-500", "bg-yellow-400", "bg-emerald-400"];
        const labels = ["", "Weak", "Fair", "Good", "Strong"];

        for (let i = 1; i <= 4; i++) {
            const bar = document.getElementById(`bar-${i}`);
            bar.className = `h-1 flex-1 rounded-full ${i <= score ? colors[score - 1] : "bg-slate-700"}`;
        }
        const label = document.getElementById("strength-label");
        if (label) label.textContent = val.length ? labels[score] : "";
    });
}

// Country-code picker + phone field on the register page (country-picker.js).
const syncPhone = initCountryPhonePicker("");

// ── Form submit — show spinner, disable button ──────────────────
const form = document.getElementById("login-form") || document.getElementById("register-form");
const submitBtn = document.getElementById("submit-btn");
const btnText = document.getElementById("btn-text");
const btnSpinner = document.getElementById("btn-spinner");

if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
        syncPhone();
        const inputs = form.querySelectorAll("input[required]");
        let valid = true;
        inputs.forEach((inp) => {
            if (!inp.value.trim()) valid = false;
        });

        if (!valid) {
            e.preventDefault();
            return;
        }

        submitBtn.disabled = true;
        if (btnText) btnText.textContent = "Please wait…";
        btnSpinner?.classList.remove("hidden");
    });
}

// ── Fade-in on load ─────────────────────────────────────────────
document.querySelectorAll("form, h1, p").forEach((el, i) => {
    el.style.animationDelay = `${i * 60}ms`;
    el.classList.add("fade-in");
});

// ── Tax Year Selection Preview ──────────────────────────────────
const yearSelect = document.getElementById("tax_year");
const yearPreview = document.getElementById("year-preview");
if (yearSelect && yearPreview) {
    yearSelect.addEventListener("change", function () {
        if (this.value) {
            yearPreview.textContent = "Your selected year is " + this.value;
            yearPreview.style.display = "block";
        } else {
            yearPreview.style.display = "none";
        }
    });
}

// ── Tax Year Countdown Redirect ─────────────────────────────────
const countdownRing = document.getElementById("countdown-ring");
const countdownNum = document.getElementById("countdown-num");
const countdownText = document.getElementById("countdown-text");
if (countdownRing || countdownNum || countdownText) {
    const circumference = 176;
    let remaining = 3;
    if (countdownRing) countdownRing.style.strokeDashoffset = 0;
    const interval = setInterval(function () {
        remaining -= 1;
        if (countdownNum) countdownNum.textContent = remaining;
        if (countdownText) countdownText.textContent = remaining;
        if (countdownRing) countdownRing.style.strokeDashoffset = circumference * (1 - remaining / 3);
        if (remaining <= 0) {
            clearInterval(interval);
            window.location.href = "/client/dashboard";
        }
    }, 1000);
}

