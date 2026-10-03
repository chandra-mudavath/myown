const sidebarToggle = document.getElementById("sidebar-toggle");
const dashboardSidebar = document.getElementById("dashboard-sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");

if (sidebarToggle && dashboardSidebar && sidebarOverlay) {
    const isMobile = () => window.innerWidth < 768;

    const closeSidebar = () => {
        dashboardSidebar.classList.remove("open");
        document.body.classList.remove("sidebar-open");
        sidebarOverlay.hidden = true;
        sidebarToggle.setAttribute("aria-expanded", "false");
    };

    const openSidebar = () => {
        dashboardSidebar.classList.add("open");
        document.body.classList.add("sidebar-open");
        sidebarToggle.setAttribute("aria-expanded", "true");
        // Show overlay only on mobile (overlay mode); desktop uses push layout
        sidebarOverlay.hidden = !isMobile();
    };

    // --- Start sidebar open on desktop; on mobile it would cover the page ---
    if (!isMobile()) openSidebar();

    sidebarToggle.addEventListener("click", () => {
        const isOpen = dashboardSidebar.classList.contains("open");
        if (isOpen) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });

    // ☰ inside the sidebar heading closes it; the header ☰ then reopens it
    dashboardSidebar.querySelectorAll("[data-sidebar-collapse]").forEach((btn) => {
        btn.addEventListener("click", () => {
            closeSidebar();
            sidebarToggle.focus();
        });
    });

    sidebarOverlay.addEventListener("click", closeSidebar);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeSidebar();
    });

    // On mobile: close sidebar after clicking a nav link (ignoring dropdown toggle buttons)
    dashboardSidebar.querySelectorAll("a.sidebar-nav-item").forEach((item) => {
        item.addEventListener("click", () => {
            if (isMobile()) closeSidebar();
        });
    });
}

// ── Staff Cases Sidebar Dropdown ────────────────────────────────
document.addEventListener("DOMContentLoaded", function () {
    const casesBtn = document.getElementById("cases-dropdown-btn");
    const casesMenu = document.getElementById("cases-dropdown-menu");

    if (casesBtn && casesMenu) {
        casesBtn.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            const isCurrentlyExpanded = casesBtn.getAttribute("aria-expanded") === "true";
            const newExpandedState = !isCurrentlyExpanded;

            casesBtn.setAttribute("aria-expanded", String(newExpandedState));
            if (newExpandedState) {
                casesMenu.removeAttribute("hidden");
            } else {
                casesMenu.setAttribute("hidden", "");
            }
        });
    }

    // ── Staff Header Avatar Menu ────────────────────────────────
    const staffAvatarBtn = document.getElementById("staff-avatar-btn");
    const staffAccountMenu = document.getElementById("staff-account-menu");

    if (staffAvatarBtn && staffAccountMenu) {
        staffAvatarBtn.addEventListener("click", function (e) {
            e.stopPropagation();
            const isOpen = staffAvatarBtn.getAttribute("aria-expanded") === "true";
            staffAvatarBtn.setAttribute("aria-expanded", String(!isOpen));
            staffAccountMenu.hidden = isOpen;
        });

        document.addEventListener("click", function (e) {
            if (!staffAvatarBtn.contains(e.target) && !staffAccountMenu.contains(e.target)) {
                staffAvatarBtn.setAttribute("aria-expanded", "false");
                staffAccountMenu.hidden = true;
            }
        });

        staffAccountMenu.addEventListener("click", function (e) {
            if (e.target.closest("a")) {
                staffAvatarBtn.setAttribute("aria-expanded", "false");
                staffAccountMenu.hidden = true;
            }
        });
    }

    // ── Staff Profile Avatar Upload ─────────────────────────────
    const staffAvatarInput = document.getElementById("staff-avatar-input");
    const staffAvatarPreview = document.getElementById("staff-avatar-preview");
    const staffInitialsDiv = document.getElementById("staff-avatar-initials");
    if (staffAvatarInput) {
        staffAvatarInput.addEventListener("change", async function () {
            if (!this.files || !this.files[0]) return;
            const formData = new FormData();
            formData.append("file", this.files[0]);
            try {
                const res = await fetch("/staff/profile/avatar", { method: "POST", body: formData });
                const data = await res.json();
                if (res.ok && data.avatar_url) {
                    if (staffAvatarPreview) {
                        staffAvatarPreview.src = data.avatar_url;
                        staffAvatarPreview.style.display = "block";
                    }
                    if (staffInitialsDiv) staffInitialsDiv.style.display = "none";
                    alert("Profile picture updated!");
                } else {
                    alert(data.detail || "Failed to update profile picture.");
                }
            } catch (err) {
                alert("Error uploading profile picture.");
            }
        });
    }
});


