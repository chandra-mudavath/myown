document.addEventListener('DOMContentLoaded', () => {
  const landingPage = document.querySelector('.landing-page');
  const savedTheme = window.localStorage.getItem('landing-theme');
  document.body.classList.add('loaded-success');

  const applyTheme = (isDark) => {
    document.body.classList.toggle('dark-theme', isDark);
    landingPage?.classList.toggle('dark-theme', isDark);
    landingPage?.classList.toggle('light-theme', !isDark);

    const themeToggle = document.getElementById('theme-toggle');
    if (!themeToggle) return;
    themeToggle.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
    themeToggle.setAttribute('title', isDark ? 'Switch to light theme' : 'Switch to dark theme');
    themeToggle.setAttribute('data-theme', isDark ? 'dark' : 'light');
  };

  applyTheme(savedTheme === 'dark');

  const themeToggle = document.getElementById('theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const darkTheme = !document.body.classList.contains('dark-theme');
      applyTheme(darkTheme);
      window.localStorage.setItem('landing-theme', darkTheme ? 'dark' : 'light');
    });
  }


  const heroCaption = document.querySelector('.hero-art span');
  if (heroCaption) {
    heroCaption.classList.add('hero-art-typed');
    heroCaption.dataset.typedWords = 'One team. One clear filing process.|One workflow. Every return on track.|One practice. Complete visibility.';
  }

  document.querySelectorAll('[data-typed-words]').forEach((typedElement) => {
    const words = typedElement.dataset.typedWords.split('|');
    let wordIndex = 0;
    let characterIndex = typedElement.textContent.length;
    let deleting = true;

    window.setInterval(() => {
      const currentWord = words[wordIndex];
      if (deleting) {
        characterIndex -= 1;
        if (characterIndex <= 0) {
          deleting = false;
          wordIndex = (wordIndex + 1) % words.length;
        }
      } else {
        characterIndex += 1;
        if (characterIndex >= words[wordIndex].length) deleting = true;
      }
      typedElement.textContent = (deleting ? currentWord : words[wordIndex]).slice(0, characterIndex);
    }, 115);
  });

  const menuButton = document.querySelector('.menu-mobile');
  const menu = document.getElementById('landing-menu');
  if (menuButton && menu) {
    menuButton.addEventListener('click', () => {
      const isOpen = menuButton.classList.toggle('show');
      menu.classList.toggle('hidden', !isOpen);
      menu.classList.toggle('mobile-open', isOpen);
      menuButton.setAttribute('aria-expanded', String(isOpen));
    });

    menu.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        menuButton.classList.remove('show');
        menu.classList.add('hidden');
        menu.classList.remove('mobile-open');
        menuButton.setAttribute('aria-expanded', 'false');
      });
    });
  }

  const navigation = document.querySelector('.main-nav');
  const backTop = document.querySelector('.back-top');
  const sections = [...document.querySelectorAll('.section[id]')];
  const links = [...document.querySelectorAll('.navbar a[href^="#"]')];

  window.addEventListener('scroll', () => {
    const scrollPosition = window.scrollY;
    navigation?.classList.toggle('navbar-scrolled', scrollPosition >= 80);
    backTop?.classList.toggle('visible', scrollPosition > 300);

    let activeId = 'hero';
    sections.forEach((section) => {
      if (section.offsetTop <= scrollPosition + 120) activeId = section.id;
    });
    links.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${activeId}`));
  });

  links.forEach((link) => {
    link.addEventListener('click', (event) => {
      const target = document.querySelector(link.getAttribute('href'));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: 'smooth' });
    });
  });

  document.getElementById('contact-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const name = document.getElementById('contact-name').value.trim();
    const email = document.getElementById('contact-email').value.trim();
    if (!name || !email) {
      alert('Please fill in your name and email.');
      return;
    }
    document.getElementById('contact-success').classList.remove('hidden');
  });
});
