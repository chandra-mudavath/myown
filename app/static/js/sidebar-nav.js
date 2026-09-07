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

    // --- Start sidebar OPEN by default ---
    openSidebar();

    sidebarToggle.addEventListener("click", () => {
        const isOpen = dashboardSidebar.classList.contains("open");
        if (isOpen) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });

    sidebarOverlay.addEventListener("click", closeSidebar);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeSidebar();
    });

    // On mobile: close sidebar after clicking a nav item
    dashboardSidebar.querySelectorAll(".sidebar-nav-item").forEach((item) => {
        item.addEventListener("click", () => {
            if (isMobile()) closeSidebar();
        });
    });
}

