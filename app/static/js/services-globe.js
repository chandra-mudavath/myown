/* Rotating services globe on the home page: a dotted sphere drawn on canvas with the
   service links riding on the same sphere. Pauses on hover/focus, drag to spin. */
document.addEventListener("DOMContentLoaded", () => {
    const globe = document.querySelector("[data-services-globe]");
    if (!globe) return;

    const canvas = globe.querySelector("canvas");
    const ctx = canvas.getContext("2d");
    const tags = [...globe.querySelectorAll(".services-globe-tags a")];
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const DOT_COUNT = 520;
    const AUTO_SPEED = 0.0035;
    const TILT = -0.32;

    // Evenly spread points on a unit sphere (Fibonacci lattice).
    const sphere = (count) =>
        Array.from({ length: count }, (_, i) => {
            const y = 1 - (2 * (i + 0.5)) / count;
            const r = Math.sqrt(1 - y * y);
            const theta = Math.PI * (3 - Math.sqrt(5)) * i;
            return [Math.cos(theta) * r, y, Math.sin(theta) * r];
        });

    const dots = sphere(DOT_COUNT);
    const tagPoints = sphere(tags.length);

    let size = 0;
    let radius = 0;
    let dpr = 1;
    let angleY = 0;
    let angleX = TILT;
    let speed = reduceMotion ? 0 : AUTO_SPEED;
    let targetSpeed = speed;
    let visible = true;
    let dotColor = "37, 99, 235";

    const readColor = () => {
        const probe = getComputedStyle(globe).getPropertyValue("--globe-dot").trim();
        if (probe) dotColor = probe;
    };

    const resize = () => {
        size = globe.clientWidth;
        radius = size * 0.4;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = size * dpr;
        canvas.height = size * dpr;
        readColor();
    };

    const rotate = ([x, y, z]) => {
        const cosY = Math.cos(angleY);
        const sinY = Math.sin(angleY);
        const x1 = x * cosY - z * sinY;
        const z1 = x * sinY + z * cosY;
        const cosX = Math.cos(angleX);
        const sinX = Math.sin(angleX);
        return [x1, y * cosX - z1 * sinX, y * sinX + z1 * cosX];
    };

    const render = () => {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, size, size);
        const centre = size / 2;
        const dotRadius = radius * 0.94;

        dots.forEach((point) => {
            const [x, y, z] = rotate(point);
            const depth = (z + 1) / 2; // 0 = back, 1 = front
            ctx.fillStyle = `rgba(${dotColor}, ${0.08 + depth * 0.55})`;
            ctx.beginPath();
            ctx.arc(centre + x * dotRadius, centre + y * dotRadius, 0.8 + depth * 1.3, 0, Math.PI * 2);
            ctx.fill();
        });

        tags.forEach((tag, i) => {
            const [x, y, z] = rotate(tagPoints[i]);
            const depth = (z + 1) / 2;
            const scale = 0.62 + depth * 0.43;
            tag.style.transform = `translate(-50%, -50%) translate(${x * radius}px, ${y * radius}px) scale(${scale})`;
            tag.style.opacity = (0.3 + depth * 0.7).toFixed(2);
            tag.style.zIndex = String(Math.round(depth * 100));
            tag.classList.toggle("is-back", depth < 0.35);
        });
    };

    const tick = () => {
        if (visible && !document.hidden) {
            speed += (targetSpeed - speed) * 0.06;
            angleY += speed;
            render();
        }
        window.requestAnimationFrame(tick);
    };

    // Pause while the pointer or keyboard focus is on the globe so links are easy to hit.
    const pause = () => { targetSpeed = 0; };
    const resume = () => { if (!reduceMotion && !globe.matches(":hover") && !globe.contains(document.activeElement)) targetSpeed = AUTO_SPEED; };
    globe.addEventListener("pointerenter", pause);
    globe.addEventListener("pointerleave", resume);
    globe.addEventListener("focusin", pause);
    globe.addEventListener("focusout", () => window.setTimeout(resume, 0));

    // Drag to spin; a drag must not count as a click on a service link.
    let dragStart = null;
    let dragged = false;
    globe.addEventListener("pointerdown", (event) => {
        dragStart = { x: event.clientX, y: event.clientY, angleY, angleX };
        dragged = false;
    });
    window.addEventListener("pointermove", (event) => {
        if (!dragStart) return;
        const dx = event.clientX - dragStart.x;
        const dy = event.clientY - dragStart.y;
        if (Math.abs(dx) + Math.abs(dy) > 6) {
            dragged = true;
            globe.classList.add("is-dragging");
        }
        if (!dragged) return;
        angleY = dragStart.angleY + dx * 0.008;
        angleX = Math.max(-1.1, Math.min(1.1, dragStart.angleX + dy * 0.008));
        render();
    });
    window.addEventListener("pointerup", () => {
        dragStart = null;
        globe.classList.remove("is-dragging");
    });
    globe.addEventListener("dragstart", (event) => event.preventDefault());
    tags.forEach((tag) =>
        tag.addEventListener("click", (event) => {
            if (dragged) event.preventDefault();
        })
    );

    new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }).observe(globe);
    new MutationObserver(() => { readColor(); render(); }).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"],
    });
    window.addEventListener("resize", () => { resize(); render(); });

    resize();
    render();
    globe.classList.add("is-ready");
    window.requestAnimationFrame(tick);
});
