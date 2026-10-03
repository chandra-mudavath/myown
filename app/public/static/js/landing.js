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

        const navbarHeight = () => document.querySelector(".main-nav")?.offsetHeight ?? 80;

        const scrollToItemIfNeeded = (item) => {
            const trigger = item.querySelector(".service-trigger");
            if (!trigger) return;
            const rect = trigger.getBoundingClientRect();
            const offset = navbarHeight() + 16;
            // Only scroll if trigger is above the visible area (hidden behind navbar)
            if (rect.top < offset) {
                const top = window.scrollY + rect.top - offset;
                window.scrollTo({ top, behavior: "smooth" });
            }
        };

        serviceItems.forEach((item) => {
            item.querySelector(".service-trigger")?.addEventListener("click", () => {
                const shouldOpen = !item.classList.contains("is-open");
                serviceItems.forEach((serviceItem) => setServiceState(serviceItem, shouldOpen && serviceItem === item));
                if (shouldOpen) {
                    // After transitions complete, check if trigger slipped above navbar
                    window.setTimeout(() => scrollToItemIfNeeded(item), 650);
                }
            });
        });

        const hashItem = serviceFromHash();
        if (hashItem) {
            serviceItems.forEach((item) => setServiceState(item, item === hashItem));
            window.requestAnimationFrame(() => {
                window.requestAnimationFrame(() => scrollToItemIfNeeded(hashItem));
            });
        }

        window.addEventListener("hashchange", () => {
            const item = serviceFromHash();
            if (!item) return;
            serviceItems.forEach((serviceItem) => setServiceState(serviceItem, serviceItem === item));
            window.setTimeout(() => scrollToItemIfNeeded(item), 650);
        });
    }

    const navigation = document.querySelector(".main-nav");
    const backTop = document.querySelector(".back-top");
    backTop?.addEventListener("click", (event) => {
        event.preventDefault();
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    });
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

// Contact form country-code picker (country-picker.js).
let syncContactPhone = () => {};

document.addEventListener("DOMContentLoaded", () => {
    syncContactPhone = initCountryPhonePicker("contact-");
});
