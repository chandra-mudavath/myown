function showSection(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('section-' + name).classList.add('active');
  document.getElementById('nav-' + name).classList.add('active');
  const titles = { overview:'Admin Overview', cases:'Tax Cases', staff:'Staff Members', clients:'Clients', audit:'Audit Trail', config:'Configuration' };
  document.getElementById('pageTitle').textContent = titles[name] || name;
}
