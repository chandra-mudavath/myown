// Currency converter on Make Payment → Pay in INR (client feedback, Nov 2026).
// Every currency in both lists (From defaults to USD, To to INR); the amount is typed by the client.
// Market rates from our server (base USD), for information only.
(function () {
    const box = document.querySelector("[data-fx]");
    if (!box) return;
    const amount = box.querySelector("[data-fx-amount]");
    const from = box.querySelector("[data-fx-from]");
    const to = box.querySelector("[data-fx-to]");
    const result = box.querySelector("[data-fx-result]");
    const note = box.querySelector("[data-fx-note]");
    let rates = null;

    let names = null;
    try { names = new Intl.DisplayNames(["en"], { type: "currency" }); } catch (e) { /* older browsers: codes only */ }
    const label = (code) => {
        let name = "";
        try { name = names ? names.of(code) : ""; } catch (e) { name = ""; }
        return name && name !== code ? `${code} – ${name}` : code;
    };
    const fmt = (value, code) => {
        try {
            return new Intl.NumberFormat("en", { style: "currency", currency: code, maximumFractionDigits: 2 }).format(value);
        } catch (e) {
            return `${value.toFixed(2)} ${code}`;
        }
    };

    function fill(select, codes, selected) {
        select.innerHTML = "";
        codes.forEach((code) => {
            const opt = document.createElement("option");
            opt.value = code;
            opt.textContent = label(code);
            if (code === selected) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function convert() {
        const value = parseFloat(amount.value);
        if (!rates) return;
        if (!amount.value || Number.isNaN(value) || value < 0) {
            result.textContent = "Enter an amount to convert.";
            return;
        }
        const a = rates[from.value], b = rates[to.value];
        if (!a || !b) { result.textContent = "No rate for this pair."; return; }
        const converted = (value / a) * b;
        const unit = b / a;
        result.innerHTML = "";
        const strong = document.createElement("strong");
        strong.textContent = `${fmt(value, from.value)} = ${fmt(converted, to.value)}`;
        const small = document.createElement("small");
        small.textContent = `1 ${from.value} = ${unit.toLocaleString("en", { maximumFractionDigits: 4 })} ${to.value}`;
        result.append(strong, small);
    }

    fetch("/client/payments/fx-rates", { headers: { Accept: "application/json" } })
        .then((res) => (res.ok ? res.json() : Promise.reject(res)))
        .then((data) => {
            rates = data.rates;
            const codes = Object.keys(rates).sort();
            fill(from, codes, from.value || "USD");
            fill(to, codes, to.value || "INR");
            if (data.updated) {
                const when = new Date(data.updated);
                const stamp = Number.isNaN(when.getTime()) ? data.updated : when.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
                note.textContent = `Market rate updated ${stamp}, for information only. ` + note.textContent.replace(/^Market rate, for information only\.\s*/, "");
            }
            convert();
        })
        .catch(() => {
            result.textContent = "Exchange rates aren't available right now. Please try again later.";
            [amount, from, to].forEach((el) => { el.disabled = true; });
        });

    amount.addEventListener("input", convert);
    from.addEventListener("change", convert);
    to.addEventListener("change", convert);
    box.querySelector("[data-fx-swap]").addEventListener("click", () => {
        const f = from.value;
        from.value = to.value;
        to.value = f;
        convert();
    });
})();
