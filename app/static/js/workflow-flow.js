/* Home page workflow: the step nearest the middle of the screen becomes active and the
   live tracker (progress ring, stage scene, stage checklist) follows it. */
document.addEventListener("DOMContentLoaded", () => {
    const flow = document.querySelector("[data-flow]");
    if (!flow) return;

    const tracker = flow.querySelector(".flow-tracker");
    const steps = [...flow.querySelectorAll(".flow-step")];
    const stageButtons = [...flow.querySelectorAll("[data-go]")];
    const titleEl = flow.querySelector("[data-flow-title]");
    const percentEl = flow.querySelector("[data-flow-percent]");
    const statusEl = flow.querySelector("[data-flow-status]");
    const lineEl = flow.querySelector("[data-flow-line]");
    const stepsWrap = flow.querySelector(".flow-steps");
    const total = steps.length;
    let active = 0;

    // Vertical centre of a step's number badge, measured inside .flow-steps.
    const badgeCentre = (i) => {
        const badge = steps[i].querySelector("b");
        return steps[i].offsetTop + badge.offsetTop + badge.offsetHeight / 2;
    };

    const setActive = (index) => {
        if (index === active) return;
        active = index;
        const percent = Math.round((index / total) * 100);

        tracker.dataset.active = String(index);
        tracker.style.setProperty("--p", percent);
        titleEl.textContent = steps[index - 1].dataset.title;
        percentEl.textContent = `${percent}%`;
        statusEl.textContent = index === total ? "Completed" : "In progress";
        tracker.classList.toggle("is-complete", index === total);

        steps.forEach((step, i) => {
            step.classList.toggle("is-active", i === index - 1);
            step.classList.toggle("is-done", i < index - 1);
        });
        stageButtons.forEach((button) => {
            const n = Number(button.dataset.go);
            button.classList.toggle("is-done", n < index);
            button.classList.toggle("is-current", n === index);
            button.setAttribute("aria-current", n === index ? "step" : "false");
        });

        // Fill the connecting line down to the active step's number badge.
        lineEl.style.height = `${badgeCentre(index - 1) - badgeCentre(0)}px`;
    };

    // Activate whichever step crosses the middle band of the viewport.
    const observer = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) setActive(Number(entry.target.dataset.step));
            });
        },
        { rootMargin: "-45% 0px -50% 0px" }
    );
    steps.forEach((step) => observer.observe(step));

    const scrollToStep = (n) => {
        steps[n - 1].scrollIntoView({ behavior: "smooth", block: "center" });
        setActive(n);
    };
    stageButtons.forEach((button) => button.addEventListener("click", () => scrollToStep(Number(button.dataset.go))));
    steps.forEach((step) => step.addEventListener("click", () => setActive(Number(step.dataset.step))));

    // Stretch the track between the first and last badges.
    const lineBox = lineEl.parentElement;
    const placeLine = () => {
        const badge = steps[0].querySelector("b");
        lineBox.style.top = `${badgeCentre(0)}px`;
        lineBox.style.height = `${badgeCentre(total - 1) - badgeCentre(0)}px`;
        lineBox.style.left = `${steps[0].offsetLeft + badge.offsetLeft + badge.offsetWidth / 2 - 1}px`;
        if (active) lineEl.style.height = `${badgeCentre(active - 1) - badgeCentre(0)}px`;
    };
    placeLine();
    window.addEventListener("resize", placeLine);
    stepsWrap.classList.add("is-ready");

    setActive(1);
});
