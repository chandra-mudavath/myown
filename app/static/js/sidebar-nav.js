const sidebarToggle = document.getElementById('sidebar-toggle');
const dashboardSidebar = document.getElementById('dashboard-sidebar');
const sidebarOverlay = document.getElementById('sidebar-overlay');

if (sidebarToggle && dashboardSidebar && sidebarOverlay) {
  const closeSidebar = () => {
    dashboardSidebar.classList.remove('open');
    sidebarOverlay.hidden = true;
    sidebarToggle.setAttribute('aria-expanded', 'false');
  };

  sidebarToggle.addEventListener('click', () => {
    const isOpen = dashboardSidebar.classList.toggle('open');
    sidebarOverlay.hidden = !isOpen;
    sidebarToggle.setAttribute('aria-expanded', String(isOpen));
  });

  sidebarOverlay.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeSidebar();
  });

  // Minimize the menu once an action is chosen; the destination page loads underneath.
  dashboardSidebar.querySelectorAll('.sidebar-nav-item').forEach(item => {
    item.addEventListener('click', closeSidebar);
  });
}
