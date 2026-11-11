/* HR workspace: a click anywhere on a row opens it, the sidebar picker jumps to another employee on
   the same tab, and clicking a day in an attendance grid fills in the "Correct a day" form. */
(function () {
    document.querySelectorAll("tr[data-href]").forEach(function (row) {
        row.addEventListener("click", function (ev) {
            if (ev.target.closest("a, button, form, input, select")) return;
            window.location = row.dataset.href;
        });
    });
    var picker = document.querySelector("[data-hr-picker]");
    if (picker) {
        picker.addEventListener("change", function () {
            window.location = (picker.dataset.base || "/hr/employees") + "/" + encodeURIComponent(picker.value) + "?tab=" + encodeURIComponent(picker.dataset.tab || "summary");
        });
    }
    var correct = document.querySelector("form[data-hr-correct]");
    if (correct) {
        document.querySelectorAll("[data-day]").forEach(function (cell) {
            cell.addEventListener("click", function () {
                var day = correct.querySelector("[name=day]");
                if (day) day.value = cell.dataset.day;
                var who = correct.querySelector("select[name=staff_id]");
                if (who && cell.dataset.staff) who.value = cell.dataset.staff;
                correct.scrollIntoView({ behavior: "smooth", block: "center" });
                var status = correct.querySelector("[name=status]");
                if (status) status.focus();
            });
        });
    }
})();
