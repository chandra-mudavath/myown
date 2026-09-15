document.addEventListener("DOMContentLoaded", () => {
    document.body.classList.add("loaded-success");

    const heroCaption = document.querySelector(".hero-art span");
    if (heroCaption) {
        heroCaption.classList.add("hero-art-typed");
        heroCaption.dataset.typedWords =
            "One team. One clear filing process.|One workflow. Every return on track.|One practice. Complete visibility.";
    }

    document.querySelectorAll("[data-typed-words]").forEach((typedElement) => {
        const words = typedElement.dataset.typedWords.split("|");
        let wordIndex = 0;
        let characterIndex = typedElement.textContent.length;
        let deleting = true;

        window.setInterval(() => {
            const currentWord = words[wordIndex];
            if (deleting) {
                characterIndex -= 1;
                if (characterIndex <= 0) {
                    deleting = false;
                    wordIndex = (wordIndex + 1) % words.length;
                }
            } else {
                characterIndex += 1;
                if (characterIndex >= words[wordIndex].length) deleting = true;
            }
            typedElement.textContent = (deleting ? currentWord : words[wordIndex]).slice(0, characterIndex);
        }, 115);
    });

    const revealTargets = [...document.querySelectorAll(".landing-page .section")];
    if (revealTargets.length && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        const revealDirections = ["top", "right", "bottom", "left"];
        revealTargets.forEach((section, index) => {
            if (section.id === "hero") return;
            section.dataset.scrollReveal = revealDirections[(index - 1) % revealDirections.length];
            [...section.children].forEach((child, childIndex) => {
                child.classList.add("scroll-reveal-child");
                child.style.setProperty("--reveal-delay", `${childIndex * 90}ms`);
            });
        });

        const revealObserver = new IntersectionObserver(
            (entries, observer) => {
                entries.forEach((entry) => {
                    if (!entry.isIntersecting) return;
                    entry.target.classList.add("is-scroll-visible");
                    entry.target.querySelectorAll(".scroll-reveal-child").forEach((child) => {
                        child.classList.add("is-scroll-visible");
                    });
                    observer.unobserve(entry.target);
                });
            },
            { threshold: 0.14, rootMargin: "0px 0px -8%" }
        );

        revealTargets.forEach((section) => {
            if (section.id !== "hero") revealObserver.observe(section);
        });
    }

    const workflowContent = document.querySelector("[data-workflow]");
    if (workflowContent) {
        const workflowSteps = [...workflowContent.querySelectorAll(".workflow-step")];
        const showWorkflow = () => {
            workflowContent.classList.add("is-workflow-visible");
            workflowSteps.forEach((step, index) => {
                window.setTimeout(() => step.classList.add("is-workflow-visible"), index * 140);
            });
        };

        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            showWorkflow();
        } else {
            const workflowObserver = new IntersectionObserver(
                ([entry], observer) => {
                    if (!entry.isIntersecting) return;
                    showWorkflow();
                    observer.disconnect();
                },
                { threshold: 0.2 }
            );
            workflowObserver.observe(workflowContent);
        }
    }

    const menuButton = document.querySelector(".menu-mobile");
    const menu = document.getElementById("landing-menu");
    if (menuButton && menu) {
        menuButton.addEventListener("click", () => {
            const isOpen = menuButton.classList.toggle("show");
            menu.classList.toggle("hidden", !isOpen);
            menu.classList.toggle("mobile-open", isOpen);
            menuButton.setAttribute("aria-expanded", String(isOpen));
        });

        menu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                menuButton.classList.remove("show");
                menu.classList.add("hidden");
                menu.classList.remove("mobile-open");
                menuButton.setAttribute("aria-expanded", "false");
            });
        });
    }

    const servicesAccordion = document.querySelector("[data-services-accordion]");
    if (servicesAccordion) {
        const serviceItems = [...servicesAccordion.querySelectorAll("[data-service-item]")];

        const serviceFromHash = () => {
            const item = document.getElementById(window.location.hash.slice(1));
            return item?.matches("[data-service-item]") ? item : null;
        };

        const setServiceState = (item, isOpen) => {
            const trigger = item.querySelector(".service-trigger");
            const panel = item.querySelector(".service-panel");
            const toggle = item.querySelector(".service-toggle");
            if (!trigger || !panel || !toggle) return;

            item.classList.toggle("is-open", isOpen);
            trigger.setAttribute("aria-expanded", String(isOpen));
            toggle.textContent = isOpen ? "−" : "+";

            if (isOpen) {
                panel.hidden = false;
            } else {
                window.setTimeout(() => {
                    if (!item.classList.contains("is-open")) panel.hidden = true;
                }, 600);
            }
        };

        serviceItems.forEach((item) => {
            item.querySelector(".service-trigger")?.addEventListener("click", () => {
                const shouldOpen = !item.classList.contains("is-open");
                serviceItems.forEach((serviceItem) => setServiceState(serviceItem, shouldOpen && serviceItem === item));
            });
        });

        const hashItem = serviceFromHash();
        if (hashItem) {
            serviceItems.forEach((item) => setServiceState(item, item === hashItem));
            window.requestAnimationFrame(() => hashItem.scrollIntoView({ behavior: "smooth", block: "start" }));
        }

        window.addEventListener("hashchange", () => {
            const item = serviceFromHash();
            if (!item) return;
            serviceItems.forEach((serviceItem) => setServiceState(serviceItem, serviceItem === item));
            item.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    }

    const navigation = document.querySelector(".main-nav");
    const backTop = document.querySelector(".back-top");
    const sections = [...document.querySelectorAll(".section[id]")];
    const links = [...document.querySelectorAll('.navbar a[href^="#"]')];

    window.addEventListener("scroll", () => {
        const scrollPosition = window.scrollY;
        navigation?.classList.toggle("navbar-scrolled", scrollPosition >= 80);
        backTop?.classList.toggle("visible", scrollPosition > 300);

        let activeId = "hero";
        sections.forEach((section) => {
            if (section.offsetTop <= scrollPosition + 120) activeId = section.id;
        });
        links.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === `#${activeId}`));
    });

    links.forEach((link) => {
        link.addEventListener("click", (event) => {
            const target = document.querySelector(link.getAttribute("href"));
            if (!target) return;
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth" });
        });
    });

    document.getElementById("contact-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        const name = document.getElementById("contact-name").value.trim();
        const email = document.getElementById("contact-email").value.trim();
        const phone = document.getElementById("contact-phone");
        syncContactPhone();
        if (!name || !email) {
            alert("Please fill in your name and email.");
            return;
        }
        if (!phone?.value || phone.value.length < 11) {
            alert("Please enter a valid phone number.");
            return;
        }
        document.getElementById("contact-success").classList.remove("hidden");
    });
});

// Windows has no color emoji flags, so render real flag images instead.
const flagImg = (region, className) => {
    const code = (region || "us").toLowerCase();
    return `<img class="${className}" src="https://flagcdn.com/24x18/${code}.png" srcset="https://flagcdn.com/48x36/${code}.png 2x" width="20" height="15" alt="" decoding="async" loading="lazy" />`;
};

let syncContactPhone = () => {};

// Wires up the same country-code + phone widget used on the register page.
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

document.addEventListener("DOMContentLoaded", () => {
    syncContactPhone = initCountryPhonePicker("contact-");
});
