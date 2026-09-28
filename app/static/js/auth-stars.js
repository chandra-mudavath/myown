/* app/static/js/auth-stars.js — twinkling star field behind the auth pages.
   Fills #au-starfield with sparkle stars and small glints, avoiding the form,
   top bar and right-hand copy. Optional data-max caps the count. Each star
   twinkles on a 1s cycle with a random offset so they don't blink together. */
"use strict";

(function () {
    const field = document.getElementById("au-starfield");
    if (!field) return;

    const rectOf = (selector, pad) => {
        const el = document.querySelector(selector);
        if (!el || !el.offsetParent) return null;
        const r = el.getBoundingClientRect();
        return { left: r.left - pad, right: r.right + pad, top: r.top - pad, bottom: r.bottom + pad };
    };
    const inside = (x, y, r) => r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;

    const scatter = () => {
        field.replaceChildren();
        const box = field.getBoundingClientRect();
        const avoid = [
            rectOf(".au-form", 24),
            rectOf(".au-top", 8),
            rectOf(".au-copy__text", 16),
            rectOf(".au-copy .au-back", 12),
        ];
        const max = Number(field.dataset.max) || 48;
        const target = Math.round(Math.min(max, Math.max(20, (box.width * box.height) / 14000)));
        const frag = document.createDocumentFragment();
        for (let placed = 0, tries = 0; placed < target && tries < target * 30; tries++) {
            const x = box.left + Math.random() * box.width;
            const y = box.top + Math.random() * box.height;
            if (avoid.some((r) => inside(x, y, r))) continue;
            const sparkle = Math.random() < 0.55;
            const size = sparkle ? 7 + Math.random() * 11 : 2 + Math.random() * 2.5;
            const star = document.createElement("span");
            star.className = sparkle ? "au-star" : "au-star au-star--glint";
            star.style.cssText =
                `left:${(x - box.left).toFixed(0)}px;top:${(y - box.top).toFixed(0)}px;` +
                `--s:${size.toFixed(1)}px;--d:${(-Math.random()).toFixed(2)}s;`;
            frag.appendChild(star);
            placed++;
        }
        field.appendChild(frag);
    };

    scatter();
    let resizeTimer;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(scatter, 150);
    });
})();
