document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("sidebarToggle");
  const overlay = document.getElementById("sidebarOverlay");

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    btn?.setAttribute("aria-expanded", "true");
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    btn?.setAttribute("aria-expanded", "false");
  }

  function toggleSidebar() {
    document.body.classList.contains("sidebar-open") ? closeSidebar() : openSidebar();
  }

  btn?.addEventListener("click", toggleSidebar);
  overlay?.addEventListener("click", closeSidebar);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSidebar();
  });

  // Close after clicking a sidebar link (mobile UX)
  document.addEventListener("click", (e) => {
    if (!document.body.classList.contains("sidebar-open")) return;
    const insideSidebar = e.target.closest(".sidebar");
    if (insideSidebar && e.target.closest("a")) closeSidebar();
  });

  // initial ARIA
  btn?.setAttribute("aria-expanded", "false");
});
