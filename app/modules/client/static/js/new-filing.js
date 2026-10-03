const filingForm = document.querySelector("[data-filing-accordion]");

if (filingForm) {
    const storageKey = "urtax-new-filing-draft";
    const sections = [...filingForm.querySelectorAll("[data-filing-section]")];

    const getSectionFields = (section) => [...section.querySelectorAll("input, select, textarea")].filter((field) => field.name);
    const getStorageFieldKey = (field) => field.type === "checkbox" ? `${field.name}:${field.value}` : field.name;

    const savedValues = (() => {
        try {
            return JSON.parse(localStorage.getItem(storageKey) || "{}");
        } catch {
            return {};
        }
    })();

    getSectionFields(filingForm).forEach((field) => {
        const storageFieldKey = getStorageFieldKey(field);
        if (savedValues[storageFieldKey] !== undefined && !field.disabled) {
            if (field.type === "checkbox") field.checked = savedValues[storageFieldKey] === true;
            else field.value = savedValues[storageFieldKey];
        }
    });

    sections.forEach((section) => {
        const header = section.querySelector(".filing-section-header");
        const content = section.querySelector(".filing-section-content");
        const toggle = section.querySelector(".filing-section-toggle");

        header.addEventListener("click", () => {
            const isOpen = header.getAttribute("aria-expanded") === "true";

            sections.forEach((otherSection) => {
                const otherHeader = otherSection.querySelector(".filing-section-header");
                const otherContent = otherSection.querySelector(".filing-section-content");
                const otherToggle = otherSection.querySelector(".filing-section-toggle");
                const shouldOpen = otherSection === section && !isOpen;

                otherHeader.setAttribute("aria-expanded", String(shouldOpen));
                otherContent.hidden = !shouldOpen;
                otherToggle.textContent = shouldOpen ? "−" : "+";
                otherSection.classList.toggle("is-open", shouldOpen);
            });
        });

        const saveButton = section.querySelector("[data-save-section]");
        if (saveButton) {
            saveButton.addEventListener("click", () => saveSection(section));
        }
    });

    const openSection = (section) => {
        const header = section.querySelector(".filing-section-header");
        const content = section.querySelector(".filing-section-content");
        const toggle = section.querySelector(".filing-section-toggle");

        sections.forEach((otherSection) => {
            const otherHeader = otherSection.querySelector(".filing-section-header");
            const otherContent = otherSection.querySelector(".filing-section-content");
            const otherToggle = otherSection.querySelector(".filing-section-toggle");
            const shouldOpen = otherSection === section;
            otherHeader.setAttribute("aria-expanded", String(shouldOpen));
            otherContent.hidden = !shouldOpen;
            otherToggle.textContent = shouldOpen ? "−" : "+";
            otherSection.classList.toggle("is-open", shouldOpen);
        });

        header.focus();
    };

    function saveSection(section) {
        const fields = getSectionFields(section).filter((field) => !field.disabled);
        const status = section.querySelector(".filing-section-status");
        const actions = section.querySelector(".section-actions");
        let message = actions.querySelector(".section-save-message");

        if (!message) {
            message = document.createElement("span");
            message.className = "section-save-message";
            message.setAttribute("role", "status");
            actions.prepend(message);
        }

        // Check if all fields (or required fields) in the section are filled and valid
        const requiredFields = fields.filter((field) => field.required);
        const invalidRequiredField = requiredFields.find((field) => !field.checkValidity() || field.value.trim() === "");

        // Check if any required field is missed/invalid
        if (invalidRequiredField) {
            status.textContent = "In Progress";
            status.className = "filing-section-status status-in-progress";
            message.textContent = "Please fill in all required fields marked with *.";
            message.className = "section-save-message is-error";
            invalidRequiredField.reportValidity();
            return;
        }

        // Special section validation checks
        if (section.dataset.incomeSection !== undefined && !fields.some((field) => field.checked)) {
            status.textContent = "In Progress";
            status.className = "filing-section-status status-in-progress";
            message.textContent = "Select at least one income category to complete this section.";
            message.className = "section-save-message is-error";
            return;
        }

        // Save valid field values
        fields.forEach((field) => {
            const storageFieldKey = getStorageFieldKey(field);
            if (field.type === "checkbox") {
                savedValues[storageFieldKey] = field.checked;
            } else {
                savedValues[storageFieldKey] = field.value;
            }
        });
        if (section === dependentSection) savedValues.dependent_count = dependentList.children.length;
        try {
            localStorage.setItem(storageKey, JSON.stringify(savedValues));
        } catch {
            // The form remains usable if browser storage is unavailable.
        }

        // Mark as Complete only when all validations pass
        status.textContent = "Complete";
        status.className = "filing-section-status status-complete";
        message.textContent = "Section completed and saved.";
        message.className = "section-save-message is-success";

        const nextSection = sections[sections.indexOf(section) + 1];
        if (nextSection && !nextSection.hidden) openSection(nextSection);
    }

    const filedBefore = filingForm.querySelector("#filed_us_taxes_before");
    const previousYearField = filingForm.querySelector("#previous-tax-year-field");
    const previousYearInput = filingForm.querySelector("#previous_tax_year_filed");

    const updatePreviousYear = () => {
        const applies = filedBefore.value === "yes";
        previousYearField.hidden = !applies;
        previousYearInput.required = applies;
        if (!applies) previousYearInput.value = "";
    };

    filedBefore.addEventListener("change", updatePreviousYear);
    updatePreviousYear();

    const filingStatus = filingForm.querySelector("#filing_status");
    const spouseSection = filingForm.querySelector("[data-spouse-section]");
    const spouseFields = [...spouseSection.querySelectorAll("input, select")];
    const spouseStatuses = ["married_jointly", "married_separately"];

    const updateSpouseSection = () => {
        const isApplicable = spouseStatuses.includes(filingStatus.value);
        const header = spouseSection.querySelector(".filing-section-header");
        const content = spouseSection.querySelector(".filing-section-content");
        const toggle = spouseSection.querySelector(".filing-section-toggle");
        const status = spouseSection.querySelector(".filing-section-status");

        spouseSection.hidden = false;
        spouseSection.classList.toggle("is-not-applicable", !isApplicable);
        spouseFields.forEach((field) => {
            field.disabled = !isApplicable;
            field.required = isApplicable && field.id !== "spouse_email" && field.id !== "spouse_phone" && field.id !== "spouse_country_of_citizenship";
        });

        if (!isApplicable) {
            header.setAttribute("aria-expanded", "false");
            content.hidden = true;
            toggle.textContent = "+";
            status.textContent = "Not Applicable";
            spouseSection.classList.remove("is-open");
        } else {
            status.textContent = "Not Started";
        }
    };

    filingStatus.addEventListener("change", updateSpouseSection);
    updateSpouseSection();

    const dependentSection = filingForm.querySelector("[data-dependents-section]");
    const dependentList = dependentSection.querySelector("[data-dependent-list]");
    let dependentCount = Number(savedValues.dependent_count || 0);

    const addDependent = (values = {}) => {
        const index = dependentList.children.length;
        const card = document.createElement("article");
        card.className = "dependent-card";
        card.dataset.dependentCard = "";
        card.innerHTML = `
            <div class="dependent-card-header">
                <h3>Dependent ${index + 1}</h3>
                <div class="dependent-card-actions">
                    <button class="button button-quiet" type="button" data-edit-dependent>Edit</button>
                    <button class="button button-quiet" type="button" data-remove-dependent>Remove</button>
                </div>
            </div>
            <div class="fields">
                <label>First name <span class="required">Required</span><input name="dependent_${index}_first_name" type="text" placeholder="Enter first name" required /></label>
                <label>Last name <span class="required">Required</span><input name="dependent_${index}_last_name" type="text" placeholder="Enter last name" required /></label>
                <label>Date of birth <span class="required">Required</span><input name="dependent_${index}_date_of_birth" type="date" required /></label>
                <label>Relationship <span class="required">Required</span><input name="dependent_${index}_relationship" type="text" placeholder="e.g. Child" required /></label>
                <label>Months lived with taxpayer <span class="required">Required</span><input name="dependent_${index}_months_lived" type="number" min="0" max="12" placeholder="0-12" required /></label>
                <label>Student status <span class="required">Required</span><select name="dependent_${index}_student_status" required><option value="">Select status</option><option value="no">No</option><option value="full_time">Full-time student</option><option value="part_time">Part-time student</option></select></label>
                <label>Disability status <span class="required">Required</span><select name="dependent_${index}_disability_status" required><option value="">Select status</option><option value="no">No</option><option value="yes">Yes</option></select></label>
            </div>`;
        dependentList.append(card);
        getSectionFields(card).forEach((field) => {
            if (values[field.name] !== undefined) field.value = values[field.name];
            else if (savedValues[getStorageFieldKey(field)] !== undefined) field.value = savedValues[getStorageFieldKey(field)];
        });
        card.querySelector("[data-edit-dependent]").addEventListener("click", () => card.querySelector("input, select").focus());
        card.querySelector("[data-remove-dependent]").addEventListener("click", () => {
            card.remove();
            savedValues.dependent_count = dependentList.children.length;
            [...dependentList.children].forEach((item, itemIndex) => {
                item.querySelector("h3").textContent = `Dependent ${itemIndex + 1}`;
            });
        });
    };

    while (dependentCount > dependentList.children.length) addDependent();
    dependentSection.querySelector("[data-add-dependent]").addEventListener("click", () => addDependent());

    const docInputs = document.querySelectorAll(".doc-file-input");
    docInputs.forEach((input) => {
        input.addEventListener("change", (e) => {
            const card = e.target.closest(".document-category-card");
            if (!card) return;
            const countLabel = card.querySelector(".file-count-label");
            const fileList = card.querySelector(".file-list");
            const files = Array.from(e.target.files);

            if (files.length === 0) {
                countLabel.textContent = "No files selected";
                fileList.innerHTML = "";
                return;
            }

            countLabel.textContent = `${files.length} file(s) selected`;
            fileList.innerHTML = files.map((file) => `<li>${file.name} (${(file.size / 1024).toFixed(1)} KB)</li>`).join("");
        });
    });
}