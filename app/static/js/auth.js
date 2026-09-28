/* app/static/js/auth.js — Login & Register form enhancements */
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

// Compose the stored international number from the registration controls.
const countryCode = document.getElementById("country_code");
const phoneLocal = document.getElementById("phone_local");
const phone = document.getElementById("phone");
const countryPicker = document.getElementById("country-picker");
const countryPickerToggle = document.getElementById("country-picker-toggle");
const countryPickerMenu = document.getElementById("country-picker-menu");
const countrySearch = document.getElementById("country-search");
const countryOptions = document.getElementById("country-options");
const selectedCountryFlag = document.getElementById("selected-country-flag");
const selectedCountryCode = document.getElementById("selected-country-code");

const DEFAULT_COUNTRY = { region: "US", dial_code: "1", name: "United States" };

const syncPhone = () => {
    if (!countryCode || !phoneLocal || !phone) return;
    phoneLocal.value = phoneLocal.value.replace(/\D/g, "").slice(0, 10);
    const dialCode = (countryCode.value || "").replace(/\D/g, "");
    phone.value = `${dialCode ? `+${dialCode}` : ""}${phoneLocal.value}`;
};

// Windows has no color emoji flags, so render real flag images instead.
const flagImg = (region, className) => {
    const code = (region || "us").toLowerCase();
    return `<img class="${className}" src="https://flagcdn.com/24x18/${code}.png" srcset="https://flagcdn.com/48x36/${code}.png 2x" width="20" height="15" alt="" decoding="async" loading="lazy" />`;
};

let countryItems = [];

const chooseCountry = (country) => {
    if (!countryCode || !countryPickerToggle || !countryPickerMenu || !selectedCountryFlag || !selectedCountryCode)
        return;

    const dialCode = String(country.dial_code ?? "").replace(/\D/g, "");
    countryCode.value = dialCode ? `+${dialCode}` : "";
    selectedCountryFlag.innerHTML = flagImg(country.region || "us", "country-flag-img");
    selectedCountryCode.textContent = dialCode ? `+${dialCode}` : "";
    countryPickerToggle.setAttribute("aria-label", `${country.name || country.region || "Country"} +${dialCode}`);
    countryPickerMenu.hidden = true;
    countryPickerToggle.setAttribute("aria-expanded", "false");
    syncPhone();
};

const renderCountries = (query = "") => {
    if (!countryOptions) return;

    const normalizedQuery = query.trim().toLowerCase();
    const matches = countryItems
        .filter((country) =>
            `${country.name} ${country.region} +${country.dial_code}`.toLowerCase().includes(normalizedQuery)
        )
        .sort((left, right) => {
            const leftName = left.name.toLowerCase();
            const rightName = right.name.toLowerCase();
            const leftRank = leftName === normalizedQuery ? 0 : leftName.startsWith(normalizedQuery) ? 1 : 2;
            const rightRank = rightName === normalizedQuery ? 0 : rightName.startsWith(normalizedQuery) ? 1 : 2;
            return leftRank - rightRank || leftName.localeCompare(rightName);
        });

    if (!matches.length) {
        countryOptions.innerHTML =
            '<div class="country-option" style="cursor: default; opacity: 0.7;">No country found</div>';
        return;
    }

    countryOptions.replaceChildren(
        ...matches.map((country) => {
            const option = document.createElement("button");
            option.type = "button";
            option.className = "country-option";
            option.setAttribute("role", "option");
            option.innerHTML = `${flagImg(country.region, "country-option-flag")}<span class="country-option-code">+${country.dial_code}</span><span class="country-option-name">${country.name}</span>`;
            option.addEventListener("click", () => chooseCountry(country));
            return option;
        })
    );
};

const loadCountries = async () => {
    if (!countryCode || !countryPicker) return;
    try {
        const response = await fetch("/auth/countries", { credentials: "same-origin" });
        if (!response.ok) throw new Error("Unable to load countries");
        const countries = await response.json();
        const names = new Intl.DisplayNames(["en"], { type: "region" });
        countryItems = countries
            .map(({ region, dial_code }) => ({
                region,
                dial_code,
                name: names.of(region) || region,
            }))
            .sort((left, right) => left.name.localeCompare(right.name));

        const defaultMatch =
            countryItems.find((country) => country.region === DEFAULT_COUNTRY.region) || countryItems[0];
        if (defaultMatch) {
            chooseCountry(defaultMatch);
        }
        renderCountries();
    } catch {
        if (selectedCountryCode) selectedCountryCode.textContent = "";
        if (countryCode) countryCode.value = "+1";
        if (selectedCountryFlag) selectedCountryFlag.innerHTML = flagImg("us", "country-flag-img");
    }
};

loadCountries();
phoneLocal?.addEventListener("input", syncPhone);

countryPickerToggle?.addEventListener("click", () => {
    if (!countryPickerMenu || !countrySearch) return;
    const isOpen = !countryPickerMenu.hidden;
    countryPickerMenu.hidden = isOpen;
    countryPickerToggle.setAttribute("aria-expanded", String(!isOpen));
    if (!isOpen) {
        countrySearch.value = "";
        renderCountries();
        countrySearch.focus();
    }
});

countrySearch?.addEventListener("input", () => renderCountries(countrySearch.value));
document.addEventListener("click", (event) => {
    if (countryPicker && !countryPicker.contains(event.target)) {
        countryPickerMenu.hidden = true;
        countryPickerToggle?.setAttribute("aria-expanded", "false");
    }
});

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

