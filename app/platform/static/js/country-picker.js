/* app/platform/static/js/country-picker.js — country-code + phone widget (register page, landing contact form) */

// Windows has no color emoji flags, so render real flag images instead.
const flagImg = (region, className) => {
    const code = (region || "us").toLowerCase();
    return `<img class="${className}" src="https://flagcdn.com/24x18/${code}.png" srcset="https://flagcdn.com/48x36/${code}.png 2x" width="20" height="15" alt="" decoding="async" loading="lazy" />`;
};

// Wires up a country-code picker + local phone input. `prefix` selects the element ids
// ("" for the register page, "contact-" for the landing contact form).
// Returns syncPhone(), which writes the full +<code><number> into the hidden phone field.
function initCountryPhonePicker(prefix) {
    const countryCode = document.getElementById(`${prefix}country_code`);
    const phoneLocal = document.getElementById(`${prefix}phone_local`);
    const phone = document.getElementById(`${prefix}phone`);
    const picker = document.getElementById(`${prefix}country-picker`);
    const toggle = document.getElementById(`${prefix}country-picker-toggle`);
    const menu = document.getElementById(`${prefix}country-picker-menu`);
    const search = document.getElementById(`${prefix}country-search`);
    const options = document.getElementById(`${prefix}country-options`);
    const selectedFlag = document.getElementById(`${prefix}selected-country-flag`);
    const selectedCode = document.getElementById(`${prefix}selected-country-code`);

    if (!countryCode || !phoneLocal || !phone || !picker) return () => {};

    let items = [];

    const syncPhone = () => {
        phoneLocal.value = phoneLocal.value.replace(/\D/g, "").slice(0, 10);
        const dialCode = (countryCode.value || "").replace(/\D/g, "");
        phone.value = `${dialCode ? `+${dialCode}` : ""}${phoneLocal.value}`;
    };

    const chooseCountry = (country) => {
        const dialCode = String(country.dial_code ?? "").replace(/\D/g, "");
        countryCode.value = dialCode ? `+${dialCode}` : "";
        if (selectedFlag) selectedFlag.innerHTML = flagImg(country.region || "us", "country-flag-img");
        if (selectedCode) selectedCode.textContent = dialCode ? `+${dialCode}` : "";
        toggle?.setAttribute("aria-label", `${country.name || country.region || "Country"} +${dialCode}`);
        if (menu) menu.hidden = true;
        toggle?.setAttribute("aria-expanded", "false");
        syncPhone();
    };

    const renderCountries = (query = "") => {
        if (!options) return;
        const normalizedQuery = query.trim().toLowerCase();
        const matches = items
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
            options.innerHTML =
                '<div class="country-option" style="cursor: default; opacity: 0.7;">No country found</div>';
            return;
        }

        options.replaceChildren(
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

    (async () => {
        try {
            const response = await fetch("/auth/countries", { credentials: "same-origin" });
            if (!response.ok) throw new Error("Unable to load countries");
            const countries = await response.json();
            const names = new Intl.DisplayNames(["en"], { type: "region" });
            items = countries
                .map(({ region, dial_code }) => ({
                    region,
                    dial_code,
                    name: names.of(region) || region,
                }))
                .sort((left, right) => left.name.localeCompare(right.name));

            const defaultMatch = items.find((country) => country.region === "US") || items[0];
            if (defaultMatch) chooseCountry(defaultMatch);
            renderCountries();
        } catch {
            if (selectedCode) selectedCode.textContent = "";
            countryCode.value = "+1";
            if (selectedFlag) selectedFlag.innerHTML = flagImg("us", "country-flag-img");
        }
    })();

    phoneLocal.addEventListener("input", syncPhone);

    toggle?.addEventListener("click", () => {
        if (!menu || !search) return;
        const isOpen = !menu.hidden;
        menu.hidden = isOpen;
        toggle.setAttribute("aria-expanded", String(!isOpen));
        if (!isOpen) {
            search.value = "";
            renderCountries();
            search.focus();
        }
    });

    search?.addEventListener("input", () => renderCountries(search.value));
    document.addEventListener("click", (event) => {
        if (picker && !picker.contains(event.target)) {
            if (menu) menu.hidden = true;
            toggle?.setAttribute("aria-expanded", "false");
        }
    });

    return syncPhone;
}
