/* Kleine, framework-unabhängige Laufzeit-Helfer.
 * Bewusst getrennt von app.js: Polling, Request-Abbruch und Dialog-Fokus
 * können isoliert getestet/geändert werden, ohne die Fachlogik anzufassen.
 */
(() => {
  'use strict';

  function createPoller(task, options = {}) {
    const minDelay = Math.max(250, Number(options.minDelay || 2000));
    const maxDelay = Math.max(minDelay, Number(options.maxDelay || 60000));
    const backoff = Math.max(1, Number(options.backoff || 1.5));
    const isActive = typeof options.isActive === 'function' ? options.isActive : () => true;
    let timer = null;
    let stopped = true;
    let delay = minDelay;

    async function tick() {
      if (stopped) return;
      if (!isActive()) {
        schedule(maxDelay);
        return;
      }
      let changed = false;
      try {
        changed = (await task()) === true;
      } catch (_) {
        changed = false;
      }
      delay = changed ? minDelay : Math.min(maxDelay, Math.round(delay * backoff));
      schedule(delay);
    }

    function schedule(ms) {
      if (stopped) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(tick, ms);
    }

    return {
      start({ immediate = true } = {}) {
        if (!stopped) return;
        stopped = false;
        delay = minDelay;
        immediate ? tick() : schedule(delay);
      },
      stop() {
        stopped = true;
        if (timer) clearTimeout(timer);
        timer = null;
      },
      wake() {
        if (stopped) return;
        delay = minDelay;
        schedule(0);
      },
      get running() { return !stopped; },
    };
  }

  function initAccessibleDialogs() {
    const dialogs = [...document.querySelectorAll('.modal-backdrop')];
    let active = null;
    let returnFocus = null;

    const focusables = (dialog) => [...dialog.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
      'textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    )].filter((el) => el.offsetParent !== null && !el.hasAttribute('inert'));

    function activate(dialog) {
      if (active === dialog) return;
      active = dialog;
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const panel = dialog.querySelector('.modal') || dialog;
      panel.setAttribute('tabindex', '-1');
      requestAnimationFrame(() => (focusables(panel)[0] || panel).focus({ preventScroll: true }));
    }

    function deactivate(dialog) {
      if (active !== dialog) return;
      active = null;
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus({ preventScroll: true });
      returnFocus = null;
    }

    const visible = (el) => getComputedStyle(el).display !== 'none' && !el.hasAttribute('hidden');
    dialogs.forEach((dialog) => {
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');
      const observer = new MutationObserver(() => visible(dialog) ? activate(dialog) : deactivate(dialog));
      observer.observe(dialog, { attributes: true, attributeFilter: ['style', 'class', 'hidden'] });
      if (visible(dialog)) activate(dialog);
    });

    document.addEventListener('keydown', (event) => {
      if (!active) return;
      const panel = active.querySelector('.modal') || active;
      if (event.key === 'Escape') {
        const close = panel.querySelector('[data-dialog-close]');
        if (close instanceof HTMLElement) {
          event.preventDefault();
          close.click();
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables(panel);
      if (!items.length) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  window.RezepteRuntime = Object.freeze({ createPoller, initAccessibleDialogs });
})();
