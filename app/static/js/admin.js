document.addEventListener("DOMContentLoaded", function () {
    const modalOverlay = document.getElementById("admin-modal-overlay");
    const modalTitle = document.getElementById("modal-title");
    const modalBody = document.getElementById("modal-body");
    const modalCloseBtn = document.getElementById("modal-close-btn");
    let shouldRefreshOnClose = false;

    function openModal(title, contentHtml) {
        if (!modalOverlay || !modalTitle || !modalBody) return;
        shouldRefreshOnClose = false;
        modalTitle.textContent = title;
        modalBody.innerHTML = contentHtml;
        modalOverlay.removeAttribute("hidden");
    }

    function closeModal() {
        if (!modalOverlay) return;
        modalOverlay.setAttribute("hidden", "");
        if (shouldRefreshOnClose) {
            window.location.reload();
        }
    }

    if (modalCloseBtn) {
        modalCloseBtn.addEventListener("click", closeModal);
    }

    if (modalOverlay) {
        modalOverlay.addEventListener("click", function (e) {
            if (e.target === modalOverlay) closeModal();
        });
    }

    // Quick Action Handlers
    function bindClientHandler() {
        const html = `
            <form id="form-quick-client">
                <div class="modal-form-group">
                    <label>First Name *</label>
                    <input type="text" name="first_name" required placeholder="John">
                </div>
                <div class="modal-form-group">
                    <label>Last Name *</label>
                    <input type="text" name="last_name" required placeholder="Doe">
                </div>
                <div class="modal-form-group">
                    <label>Email Address *</label>
                    <input type="email" name="email" required placeholder="client@example.com">
                </div>
                <div class="modal-form-group">
                    <label>Phone Number *</label>
                    <input type="tel" name="phone" required placeholder="+1 555-0199">
                </div>
                <button type="submit" class="modal-btn-submit">Create Client</button>
            </form>
        `;
        openModal("+ Add New Client", html);

        document.getElementById("form-quick-client").addEventListener("submit", async function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            try {
                const res = await fetch("/admin/quick-action/client", { method: "POST", body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    shouldRefreshOnClose = true;
                    modalBody.innerHTML = `
                        <div class="success-credentials-box">
                            <h4 style="margin-top:0; color:#15803d; font-size:1.1rem;">🎉 Client Account Created!</h4>
                            <p><strong>Client Number:</strong> ${data.client_number}</p>
                            <p><strong>Email:</strong> ${data.email}</p>
                            <p style="margin-top:0.75rem;"><strong>Auto-Generated Temporary Password:</strong></p>
                            <p><span class="cred-highlight">${data.temp_password}</span></p>
                            <small style="display:block; margin-top:0.75rem; color:#166534;">Please share these credentials securely with the client.</small>
                            <button type="button" id="btn-close-and-refresh" class="modal-btn-submit" style="margin-top: 1rem; background: #166534;">Done & Refresh Dashboard</button>
                        </div>
                    `;
                    const doneBtn = document.getElementById("btn-close-and-refresh");
                    if (doneBtn) doneBtn.addEventListener("click", closeModal);
                } else {
                    alert(data.detail || "Failed to create client.");
                }
            } catch (err) {
                alert("Error submitting request.");
            }
        });
    }

    // Bind triggers for Client Creation
    ["btn-quick-new-client", "rail-btn-new-client"].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener("click", bindClientHandler);
    });

    // 2. Quick Action: + New Staff
    function bindStaffHandler() {
        const html = `
            <form id="form-quick-staff">
                <div class="modal-form-group">
                    <label>First Name *</label>
                    <input type="text" name="first_name" required placeholder="Jane">
                </div>
                <div class="modal-form-group">
                    <label>Last Name *</label>
                    <input type="text" name="last_name" required placeholder="Smith">
                </div>
                <div class="modal-form-group">
                    <label>Email Address *</label>
                    <input type="email" name="email" required placeholder="staff@urtax.com">
                </div>
                <div class="modal-form-group">
                    <label>Role *</label>
                    <select name="role">
                        <option value="INITIATOR">Initiator</option>
                        <option value="PREPARER">Preparer</option>
                        <option value="REVIEWER">Reviewer</option>
                        <option value="MANAGER">Manager</option>
                    </select>
                </div>
                <button type="submit" class="modal-btn-submit">Create Staff Member</button>
            </form>
        `;
        openModal("+ Add New Staff", html);

        document.getElementById("form-quick-staff").addEventListener("submit", async function (e) {
            e.preventDefault();
            const formData = new FormData(this);
            try {
                const res = await fetch("/admin/quick-action/staff", { method: "POST", body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    shouldRefreshOnClose = true;
                    modalBody.innerHTML = `
                        <div class="success-credentials-box">
                            <h4 style="margin-top:0; color:#15803d; font-size:1.1rem;">🎉 Staff Member Created!</h4>
                            <p><strong>Staff Number:</strong> ${data.staff_number}</p>
                            <p><strong>Email:</strong> ${data.email}</p>
                            <p style="margin-top:0.75rem;"><strong>Auto-Generated Temporary Password:</strong></p>
                            <p><span class="cred-highlight">${data.temp_password}</span></p>
                            <small style="display:block; margin-top:0.75rem; color:#166534;">Please share these credentials with the new staff member.</small>
                            <button type="button" id="btn-close-and-refresh-staff" class="modal-btn-submit" style="margin-top: 1rem; background: #166534;">Done & Refresh Dashboard</button>
                        </div>
                    `;
                    const doneBtn = document.getElementById("btn-close-and-refresh-staff");
                    if (doneBtn) doneBtn.addEventListener("click", closeModal);
                } else {
                    alert(data.detail || "Error creating staff");
                }
            } catch (err) {
                alert("Network or server error while creating staff.");
            }
        });
    }

    ["btn-quick-new-staff", "rail-btn-new-staff"].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener("click", bindStaffHandler);
    });

    // 3. Quick Action: + New Filing
    async function bindFilingHandler() {
        openModal("+ Create New Filing", "<p style='text-align:center;'>Loading clients...</p>");
        try {
            const res = await fetch("/admin/api/clients");
            const clients = await res.json();

            let clientOptionsHtml = clients.map(c => `<option value="${c.id}">${c.name} (${c.client_number})</option>`).join("");
            if (!clients.length) {
                clientOptionsHtml = `<option value="">No clients found - Create a client first</option>`;
            }

            const html = `
                <form id="form-quick-filing">
                    <div class="modal-form-group">
                        <label>Select Client *</label>
                        <select name="client_id" required>
                            ${clientOptionsHtml}
                        </select>
                    </div>
                    <div class="modal-form-group">
                        <label>Tax Year *</label>
                        <select name="tax_year" required>
                            <option value="2026" selected>2026</option>
                            <option value="2025">2025</option>
                            <option value="2024">2024</option>
                        </select>
                    </div>
                    <div class="modal-form-group">
                        <label>Filing Type *</label>
                        <select name="filing_type" required>
                            <option value="individual" selected>Individual (1040)</option>
                            <option value="business">Business (1065 / 1120)</option>
                            <option value="amended">Amended Return</option>
                        </select>
                    </div>
                    <button type="submit" class="modal-btn-submit">Start New Filing</button>
                </form>
            `;
            openModal("+ Create New Filing", html);

            document.getElementById("form-quick-filing").addEventListener("submit", async function (e) {
                e.preventDefault();
                const formData = new FormData(this);
                try {
                    const createRes = await fetch("/admin/quick-action/filing", { method: "POST", body: formData });
                    const data = await createRes.json();
                    if (createRes.ok && data.success) {
                        window.location.href = data.redirect_url;
                    } else {
                        alert(data.detail || "Error creating filing.");
                    }
                } catch (err) {
                    alert("Error submitting new filing form.");
                }
            });
        } catch (err) {
            openModal("+ Create New Filing", "<p style='color:red;'>Failed to load client list.</p>");
        }
    }

    ["btn-quick-new-filing", "rail-btn-new-filing"].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener("click", bindFilingHandler);
    });

    // 4. Quick Action: Upload Document
    function bindUploadDocHandler() {
        const promptHtml = `
            <div style="text-align: center; padding: 0.5rem 0;">
                <p style="font-size: 1rem; font-weight: 600; margin-bottom: 1.5rem; color: var(--ink-primary);">
                    How would you like to upload the document?
                </p>
                <div style="display: flex; flex-direction: column; gap: 0.85rem;">
                    <button type="button" id="btn-upload-existing-filing" class="modal-btn-submit" style="background: #2563eb;">
                        📄 Upload for Existing Filing
                    </button>
                    <button type="button" id="btn-upload-new-filing" class="modal-btn-submit" style="background: #059669;">
                        ✨ Start New Filing & Upload Document
                    </button>
                </div>
            </div>
        `;
        openModal("↑ Select Upload Destination", promptHtml);

        // Handler: Existing Filing
        document.getElementById("btn-upload-existing-filing").addEventListener("click", async function () {
            openModal("↑ Upload Document for Existing Filing", "<p style='text-align:center;'>Loading filings...</p>");
            try {
                const res = await fetch("/admin/api/filings");
                const filings = await res.json();

                let filingOptionsHtml = filings.map(f => `<option value="${f.id}">${f.case_id} — ${f.client_name} (TY ${f.tax_year})</option>`).join("");
                if (!filings.length) {
                    filingOptionsHtml = `<option value="">No filings found</option>`;
                }

                const html = `
                    <form id="form-quick-upload-doc" enctype="multipart/form-data">
                        <div class="modal-form-group">
                            <label>Select Existing Case / Filing *</label>
                            <select name="filing_id" required>
                                ${filingOptionsHtml}
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Document Category / Type *</label>
                            <select name="doc_type" required>
                                <option value="w2" selected>W-2 / Income Statement</option>
                                <option value="1099">1099 / Misc Income</option>
                                <option value="id_proof">ID / SSN Proof</option>
                                <option value="general">General Tax Document</option>
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Select File *</label>
                            <input type="file" name="file" required>
                        </div>
                        <button type="submit" class="modal-btn-submit">Upload Document</button>
                    </form>
                `;
                openModal("↑ Upload Document for Existing Filing", html);

                document.getElementById("form-quick-upload-doc").addEventListener("submit", async function (e) {
                    e.preventDefault();
                    const formData = new FormData(this);
                    try {
                        const uploadRes = await fetch("/admin/quick-action/upload-document", { method: "POST", body: formData });
                        const data = await uploadRes.json();
                        if (uploadRes.ok && data.success) {
                            modalBody.innerHTML = `
                                <div class="success-credentials-box">
                                    <h4 style="margin-top:0; color:#15803d; font-size:1.1rem;">✅ Document Uploaded!</h4>
                                    <p>${data.message}</p>
                                    <p style="margin-top:0.5rem;">Associated Case: <strong>${data.case_number}</strong></p>
                                    <button type="button" id="btn-close-refresh-doc" class="modal-btn-submit" style="margin-top: 1rem; background: #166534;">Done & Refresh Dashboard</button>
                                </div>
                            `;
                            shouldRefreshOnClose = true;
                            document.getElementById("btn-close-refresh-doc").addEventListener("click", closeModal);
                        } else {
                            alert(data.detail || "Error uploading document.");
                        }
                    } catch (err) {
                        alert("Error uploading document.");
                    }
                });
            } catch (err) {
                openModal("↑ Upload Document for Existing Filing", "<p style='color:red;'>Failed to load filings list.</p>");
            }
        });

        // Handler: New Filing
        document.getElementById("btn-upload-new-filing").addEventListener("click", async function () {
            openModal("✨ Create New Filing with Document", "<p style='text-align:center;'>Loading clients...</p>");
            try {
                const res = await fetch("/admin/api/clients");
                const clients = await res.json();

                let clientOptionsHtml = clients.map(c => `<option value="${c.id}">${c.name} (${c.client_number})</option>`).join("");
                if (!clients.length) {
                    clientOptionsHtml = `<option value="">No clients found - Create a client first</option>`;
                }

                const html = `
                    <form id="form-quick-new-filing-doc" enctype="multipart/form-data">
                        <div class="modal-form-group">
                            <label>Select Client *</label>
                            <select name="client_id" required>
                                ${clientOptionsHtml}
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Tax Year *</label>
                            <select name="tax_year" required>
                                <option value="2026" selected>2026</option>
                                <option value="2025">2025</option>
                                <option value="2024">2024</option>
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Filing Type *</label>
                            <select name="filing_type" required>
                                <option value="individual" selected>Individual (1040)</option>
                                <option value="business">Business (1065 / 1120)</option>
                                <option value="amended">Amended Return</option>
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Document Category / Type *</label>
                            <select name="doc_type" required>
                                <option value="w2" selected>W-2 / Income Statement</option>
                                <option value="1099">1099 / Misc Income</option>
                                <option value="id_proof">ID / SSN Proof</option>
                                <option value="general">General Tax Document</option>
                            </select>
                        </div>
                        <div class="modal-form-group">
                            <label>Select Initial Document *</label>
                            <input type="file" name="file" required>
                        </div>
                        <button type="submit" class="modal-btn-submit">Create Filing & Upload Document</button>
                    </form>
                `;
                openModal("✨ Create New Filing with Document", html);

                document.getElementById("form-quick-new-filing-doc").addEventListener("submit", async function (e) {
                    e.preventDefault();
                    const formData = new FormData(this);
                    try {
                        const response = await fetch("/admin/quick-action/new-filing-with-doc", { method: "POST", body: formData });
                        const data = await response.json();
                        if (response.ok && data.success) {
                            window.location.href = data.redirect_url;
                        } else {
                            alert(data.detail || "Error creating filing with document.");
                        }
                    } catch (err) {
                        alert("Error creating filing with document.");
                    }
                });
            } catch (err) {
                openModal("✨ Create New Filing with Document", "<p style='color:red;'>Failed to load clients list.</p>");
            }
        });
    }

    ["btn-quick-upload-doc", "rail-btn-upload-doc"].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener("click", bindUploadDocHandler);
    });

    // Admin Avatar Upload Trigger
    const adminAvatarInput = document.getElementById("admin-avatar-input");
    const adminAvatarPreview = document.getElementById("admin-sidebar-preview");
    const adminInitialsSpan = document.getElementById("admin-sidebar-initials");
    if (adminAvatarInput) {
        adminAvatarInput.addEventListener("change", async function() {
            if (!this.files || !this.files[0]) return;
            const formData = new FormData();
            formData.append("file", this.files[0]);
            try {
                const res = await fetch("/admin/profile/avatar", { method: "POST", body: formData });
                const data = await res.json();
                if (res.ok && data.avatar_url) {
                    if (adminAvatarPreview) {
                        adminAvatarPreview.src = data.avatar_url;
                        adminAvatarPreview.style.display = "block";
                    }
                    if (adminInitialsSpan) adminInitialsSpan.style.display = "none";
                    alert("Admin profile picture updated successfully!");
                } else {
                    alert(data.detail || "Error uploading profile picture.");
                }
            } catch (err) {
                alert("Failed to upload profile picture.");
            }
        });
    }
});
