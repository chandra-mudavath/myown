/* app/static/js/auth.js — Login & Register form enhancements */
'use strict';

// ── Password visibility toggle ──────────────────────────────────
const toggleBtn = document.getElementById('toggle-password');
const passwordInput = document.getElementById('password');

if (toggleBtn && passwordInput) {
  toggleBtn.addEventListener('click', () => {
    const isText = passwordInput.type === 'text';
    passwordInput.type = isText ? 'password' : 'text';
    toggleBtn.setAttribute('aria-label', isText ? 'Show password' : 'Hide password');
  });
}

// ── Password strength meter (register page) ─────────────────────
if (passwordInput && document.getElementById('bar-1')) {
  passwordInput.addEventListener('input', () => {
    const val = passwordInput.value;
    let score = 0;
    if (val.length >= 8)         score++;
    if (/[A-Z]/.test(val))       score++;
    if (/[0-9]/.test(val))       score++;
    if (/[^A-Za-z0-9]/.test(val)) score++;

    const colors = ['bg-red-500', 'bg-orange-500', 'bg-yellow-400', 'bg-emerald-400'];
    const labels = ['', 'Weak', 'Fair', 'Good', 'Strong'];

    for (let i = 1; i <= 4; i++) {
      const bar = document.getElementById(`bar-${i}`);
      bar.className = `h-1 flex-1 rounded-full ${i <= score ? colors[score - 1] : 'bg-slate-700'}`;
    }
    const label = document.getElementById('strength-label');
    if (label) label.textContent = val.length ? labels[score] : '';
  });
}

// ── Form submit — show spinner, disable button ──────────────────
const form = document.getElementById('login-form') || document.getElementById('register-form');
const submitBtn = document.getElementById('submit-btn');
const btnText = document.getElementById('btn-text');
const btnSpinner = document.getElementById('btn-spinner');

if (form && submitBtn) {
  form.addEventListener('submit', (e) => {
    const inputs = form.querySelectorAll('input[required]');
    let valid = true;
    inputs.forEach(inp => { if (!inp.value.trim()) valid = false; });

    if (!valid) { e.preventDefault(); return; }

    submitBtn.disabled = true;
    btnText.textContent = 'Please wait…';
    btnSpinner?.classList.remove('hidden');
  });
}

// ── Fade-in on load ─────────────────────────────────────────────
document.querySelectorAll('form, h1, p').forEach((el, i) => {
  el.style.animationDelay = `${i * 60}ms`;
  el.classList.add('fade-in');
});
