// Layout facts the stylesheet cannot work out on its own. Classic script in <head>,
// so it applies before the first paint (no jump of the player dock on launch).
//
// - html.ios: the dock sits where iOS puts its own floating bars (17 pt from the
//   edges); Android keeps it clear of its gesture and button bar instead.
// - --vp-short / html.vp-short: an iPhone home screen app added while the page used
//   a translucent status bar (iOS 26+, WebKit bug 301108) is drawn from the top of
//   the screen but ends a status bar's height short of the bottom edge, and nothing
//   paints in that strip. The page now uses an opaque status bar, which iOS applies
//   only when the app is added to the Home Screen again; until then the dock rests on
//   the edge of the page and drops its shadow, so the strip blends in.
// - No pinch zoom, like an installed app (the text size setting covers readability).
(() => {
  const root = document.documentElement;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const iphone = /iPhone|iPod/.test(navigator.userAgent);
  const standalone = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  root.classList.toggle('ios', ios);

  function insetTop() {
    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;top:0;left:0;width:0;height:env(safe-area-inset-top);visibility:hidden;pointer-events:none';
    root.appendChild(probe);
    const h = probe.getBoundingClientRect().height;
    probe.remove();
    return h;
  }

  function fit() {
    let short = 0;
    if (iphone && standalone()) {
      // iOS keeps screen.width/height in portrait, whatever the orientation.
      const landscape = innerWidth > innerHeight;
      const full = landscape ? Math.min(screen.width, screen.height) : Math.max(screen.width, screen.height);
      const gap = Math.round(full - innerHeight);
      const top = insetTop();
      // Short by exactly the status bar under which the page is drawn: the bug.
      // (With an opaque status bar the page is legitimately shorter, but starts
      // below the bar, so the top inset is 0.)
      if (top > 0 && gap > 8 && Math.abs(gap - top) <= 8) short = gap;
    }
    root.style.setProperty('--vp-short', `${short}px`);
    root.classList.toggle('vp-short', short > 0);
  }

  fit();
  addEventListener('resize', fit);

  // iOS ignores user-scalable=no; CSS touch-action stops pinch zoom, this covers older iOS.
  document.addEventListener('gesturestart', (e) => e.preventDefault(), { passive: false });
})();
