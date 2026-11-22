// Colour theme. Classic (non-module) script loaded in <head> so the saved theme
// applies before first paint, without a flash of the wrong colours.
// <script src="/js/theme.js" data-key="lt.theme" data-default="dark"> picks the
// storage key and the default; the admin console uses the built-in defaults.
(() => {
  const opts = (document.currentScript && document.currentScript.dataset) || {};
  const KEY = opts.key || 'lt.adminTheme';
  const THEMES = ['dark', 'light', 'auto'];
  const FALLBACK = THEMES.includes(opts.default) ? opts.default : 'auto';
  const dark = matchMedia('(prefers-color-scheme: dark)');
  const root = document.documentElement;

  function read() {
    try { const v = localStorage.getItem(KEY); return THEMES.includes(v) ? v : FALLBACK; } catch { return FALLBACK; }
  }

  function paint() {
    const theme = root.dataset.theme;
    const isDark = theme === 'dark' || (theme === 'auto' && dark.matches);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = isDark ? '#10162B' : '#F1F3F9';
    for (const b of document.querySelectorAll('[data-theme-set]')) {
      b.setAttribute('aria-pressed', String(b.dataset.themeSet === theme));
    }
  }

  function set(theme) {
    if (!THEMES.includes(theme)) return;
    root.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* private mode */ }
    paint();
  }

  root.dataset.theme = read();
  paint();
  dark.addEventListener?.('change', paint);
  document.addEventListener('DOMContentLoaded', paint);
  document.addEventListener('click', (e) => {
    const b = e.target.closest?.('[data-theme-set]');
    if (b) set(b.dataset.themeSet);
  });
})();
