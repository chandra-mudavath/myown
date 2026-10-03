/* app/platform/static/js/theme-init.js — theme check before render to avoid FOUC */
(function() {
    var savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
    } else if (savedTheme === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
})();
