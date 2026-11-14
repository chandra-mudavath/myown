(function () {
    const profileRoot = document.querySelector("[data-profile]");
    let initialProfile = JSON.parse(profileRoot.dataset.profile);
    const globalMessage = document.getElementById("global-message");
    document.getElementById("completion-bar").style.width = `${initialProfile.completion_percentage}%`;

    // FastAPI returns `detail` as a string, or a list of validation errors.
    function errorText(result, fallback) {
        if (Array.isArray(result.detail)) return result.detail.map((d) => String(d.msg).replace(/^Value error, /, "")).join(" ");
        return result.detail || fallback;
    }

    function showMessage(message, isError) {
        globalMessage.textContent = message;
        globalMessage.className = `cx-alert cx-message visible ${isError ? "cx-alert--error" : "cx-alert--success"}`;
        globalMessage.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function setProfile(profile) {
        document.getElementById("completion-value").textContent = `${profile.completion_percentage}%`;
        document.getElementById("completion-bar").style.width = `${profile.completion_percentage}%`;
        document.getElementById("completion-help").textContent = profile.missing_fields.length
            ? `Complete: ${profile.missing_fields.join(", ")}.`
            : "Your profile is complete.";
        const personal = document.querySelector('[data-form="personal"]');
        personal.querySelector('[name="first_name"]').value = profile.first_name || "";
        personal.querySelector('[name="middle_name"]').value = profile.middle_name || "";
        personal.querySelector('[name="no_middle_name"]').checked = !!profile.no_middle_name;
        personal.querySelector('[name="last_name"]').value = profile.last_name || "";
        personal.querySelector('[name="gender"]').value = profile.gender || "";
        personal.querySelector('[name="date_of_birth"]').value = profile.date_of_birth || "";
        personal.querySelector('[name="occupation"]').value = profile.occupation || "";
        // The SSN never comes back from the server: the box stays empty and shows the masked number
        const ssn = personal.querySelector('[name="ssn"]');
        ssn.value = "";
        ssn.placeholder = profile.ssn_masked || "123-45-6789";
        ssn.required = !profile.ssn_masked;
        personal.querySelector("[data-ssn-hint]").textContent = profile.ssn_masked
            ? `Saved as ${profile.ssn_masked}. Leave blank to keep it.`
            : "9 digits, as on your Social Security card.";
        document.querySelector('[data-form="address"] [name="address_line_1"]').value = profile.address_line_1 || "";
        document.querySelector('[data-form="address"] [name="address_line_2"]').value = profile.address_line_2 || "";
        document.querySelector('[data-form="address"] [name="city"]').value = profile.city || "";
        document.querySelector('[data-form="address"] [name="state_province"]').value = profile.state_province || "";
        document.querySelector('[data-form="address"] [name="postal_code"]').value = profile.postal_code || "";
        document.querySelector('[data-form="address"] [name="country"]').value = profile.country || "";
        document.querySelector('[data-form="contact"] [name="phone"]').value = profile.phone || "";
    }

    function enterEdit(section) {
        section.classList.add("editing");
        section.querySelectorAll("input:not([readonly]), select").forEach((input) => {
            input.disabled = false;
        });
        section.querySelector(".edit-button").hidden = true;
        syncMiddleName();
    }

    function cancelEdit(section) {
        setProfile(initialProfile);
        section.classList.remove("editing");
        section.querySelectorAll("input:not([readonly]), select").forEach((input) => {
            input.disabled = true;
        });
        section.querySelector(".edit-button").hidden = false;
    }

    // "No middle name" ticked: the middle name box is cleared, switched off and not required
    const noMiddle = document.querySelector("[data-no-middle]");
    const middleName = document.querySelector("[data-middle-name]");
    function syncMiddleName() {
        if (!noMiddle || !middleName) return;
        const editing = !noMiddle.disabled;
        if (noMiddle.checked) middleName.value = "";
        middleName.disabled = !editing || noMiddle.checked;
        middleName.required = !noMiddle.checked;
    }
    if (noMiddle) noMiddle.addEventListener("change", syncMiddleName);

    document
        .querySelectorAll(".edit-button")
        .forEach((button) => button.addEventListener("click", () => enterEdit(button.closest(".profile-section"))));
    document
        .querySelectorAll(".cancel-button")
        .forEach((button) => button.addEventListener("click", () => cancelEdit(button.closest(".profile-section"))));
    document.querySelectorAll("[data-form]").forEach((form) =>
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const section = form.closest(".profile-section");
            const saveButton = form.querySelector(".save-button");
            saveButton.disabled = true;
            saveButton.textContent = "Saving...";
            const payload = Object.fromEntries(new FormData(form).entries());
            Object.keys(payload).forEach((key) => {
                if (payload[key] === "") payload[key] = null;
            });
            try {
                const response = await fetch(form.dataset.endpoint, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(errorText(result, "We could not save your changes. Please try again."));
                setProfile(result.profile);
                initialProfile = result.profile;
                section.classList.remove("editing");
                section.querySelectorAll("input:not([readonly]), select").forEach((input) => {
                    input.disabled = true;
                });
                section.querySelector(".edit-button").hidden = false;
                showMessage(result.message, false);
            } catch (error) {
                showMessage(error.message, true);
            }
            saveButton.disabled = false;
            saveButton.textContent = form.dataset.form === "contact" ? "Save phone" : "Save changes";
        })
    );

    document.getElementById("email-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = event.target.querySelector("button");
        button.disabled = true;
        button.textContent = "Requesting...";
        try {
            const response = await fetch("/client/api/email-request", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email: event.target.email.value }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(errorText(result, "We could not submit your request. Please try again."));
            showMessage(result.message, false);
            event.target.reset();
        } catch (error) {
            showMessage(error.message, true);
        }
        button.disabled = false;
        button.textContent = "Request change";
    });

    const avatarInput = document.getElementById("avatar-input");
    const avatarPreview = document.getElementById("avatar-preview");
    if (avatarInput) {
        avatarInput.addEventListener("change", async function () {
            if (!this.files || !this.files[0]) return;
            const formData = new FormData();
            formData.append("file", this.files[0]);
            try {
                const res = await fetch("/client/api/avatar", { method: "POST", body: formData });
                const data = await res.json();
                if (res.ok && data.avatar_url) {
                    if (avatarPreview) avatarPreview.src = data.avatar_url;
                    showMessage("Profile picture updated!", false);
                } else {
                    showMessage(data.detail || "Failed to update profile picture.", true);
                }
            } catch (err) {
                showMessage("Error uploading profile picture.", true);
            }
        });
    }
})();
