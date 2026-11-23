// Small UI helpers shared by the listener app and the admin console.

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

// ------------------------------------------------------------------ toast

let toastTimer = 0;
/** Short status message. `info` swaps the check mark for a neutral "i". */
export function toast(text, { error = false, info = false, ms } = {}) {
  const box = document.getElementById('toast');
  if (!box) return;
  // An open sheet is a modal dialog in the browser's top layer and covers the
  // rest of the page, toast included. As a popover the toast joins the top layer
  // too; reopening it puts it above a sheet that was opened after it.
  if (box.showPopover) {
    try {
      if (box.matches(':popover-open')) box.hidePopover();
      box.showPopover();
    } catch { /* no popover support: plain fixed element */ }
  }
  box.textContent = text;
  box.classList.toggle('error', error);
  box.classList.toggle('info', info && !error);
  if (!box.classList.contains('show')) void box.offsetWidth;   // start the entry transition
  box.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('show'), ms ?? (error ? 5000 : 2600));
}

// ------------------------------------------------------------------ sheets

/** Close a dialog with its exit animation (instant when motion is reduced). */
export function closeSheet(dlg, returnValue) {
  if (!dlg.open || dlg.classList.contains('closing')) return;
  const done = () => {
    clearTimeout(timer);
    dlg.removeEventListener('animationend', onEnd);
    dlg.classList.remove('closing');
    dlg.style.removeProperty('transform');
    dlg.close(returnValue);
  };
  const onEnd = (e) => { if (e.target === dlg) done(); };
  if (reducedMotion.matches) { done(); return; }
  dlg.classList.add('closing');
  dlg.addEventListener('animationend', onEnd);
  const timer = setTimeout(done, 400);   // safety net if animationend never fires
}

/**
 * Backdrop tap, [data-close] buttons and Escape close the sheet with an animation;
 * on phones the grip and the header can be pulled down to dismiss it.
 */
export function wireSheet(dlg, { onClose } = {}) {
  dlg.addEventListener('click', (e) => {
    if (e.target === dlg || e.target.closest('[data-close]')) closeSheet(dlg);
  });
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); closeSheet(dlg); });
  if (onClose) dlg.addEventListener('close', onClose);

  let startY = 0, lastY = 0, lastT = 0, speed = 0, dragging = false;
  const handles = dlg.querySelectorAll('.sheet-grip, .sheet-head');
  const onMove = (e) => {
    if (!dragging) return;
    const dy = Math.max(0, e.clientY - startY);
    const now = performance.now();
    speed = (e.clientY - lastY) / Math.max(1, now - lastT);
    lastY = e.clientY; lastT = now;
    dlg.style.transform = `translateY(${dy}px)`;
  };
  const onUp = (e) => {
    if (!dragging) return;
    dragging = false;
    dlg.classList.remove('dragging');
    const dy = Math.max(0, e.clientY - startY);
    if (dy > 110 || (dy > 30 && speed > 0.6)) {
      // Continue from where the finger left the sheet.
      dlg.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(100%)' }],
        { duration: 180, easing: 'ease-in', fill: 'forwards' })
        .finished.then(() => {
          dlg.close();
          for (const a of dlg.getAnimations()) a.cancel();
          dlg.style.removeProperty('transform');
        })
        .catch(() => { dlg.close(); dlg.style.removeProperty('transform'); });
    } else {
      dlg.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
        { duration: 260, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' });
      dlg.style.removeProperty('transform');
    }
  };
  for (const h of handles) {
    h.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.target.closest('button, a, input, select, textarea')) return;
      if (matchMedia('(min-width: 640px)').matches) return;   // centred dialog on wide screens
      dragging = true;
      startY = lastY = e.clientY;
      lastT = performance.now();
      speed = 0;
      dlg.classList.add('dragging');
      h.setPointerCapture(e.pointerId);
    });
    h.addEventListener('pointermove', onMove);
    h.addEventListener('pointerup', onUp);
    h.addEventListener('pointercancel', onUp);
  }
}

// ------------------------------------------------------------------ misc

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export const canCopy = () => !!(window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText);

/** A short tick on Android phones; ignored elsewhere. */
export function haptic(ms = 8) {
  try { navigator.vibrate?.(ms); } catch { /* unsupported */ }
}
