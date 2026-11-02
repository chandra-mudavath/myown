document.addEventListener("DOMContentLoaded", () => {
    const calc = document.querySelector("[data-refer-calc]");
    if (!calc) return;

    const sliders = [...calc.querySelectorAll('input[type="range"]')];
    const totalEl = calc.querySelector("[data-total]");
    const barEl = calc.querySelector("[data-bar]");
    const maxTotal = sliders.reduce((sum, s) => sum + Number(s.max) * Number(s.dataset.rate), 0);
    const format = (value) => `$${value.toLocaleString("en-US")}`;
    let shown = 0;
    let frame = null;

    const animateTo = (target) => {
        cancelAnimationFrame(frame);
        const start = shown;
        const startTime = performance.now();
        const step = (now) => {
            const t = Math.min((now - startTime) / 350, 1);
            shown = Math.round(start + (target - start) * (1 - Math.pow(1 - t, 3)));
            totalEl.textContent = format(shown);
            if (t < 1) frame = requestAnimationFrame(step);
        };
        frame = requestAnimationFrame(step);
    };

    const update = () => {
        let total = 0;
        sliders.forEach((slider) => {
            calc.querySelector(`[data-out="${slider.dataset.key}"]`).textContent = slider.value;
            total += Number(slider.value) * Number(slider.dataset.rate);
        });
        animateTo(total);
        barEl.style.width = `${Math.max(total / maxTotal, 0.02) * 100}%`;
    };

    sliders.forEach((slider) => slider.addEventListener("input", update));
    update();
});
