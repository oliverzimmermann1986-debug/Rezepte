// cart: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["cart"] = function () {
    return {
    cartSwipeStart(e, id) {
      const t = e.touches[0];
      this._swipe = {
        startX: t.clientX, startY: t.clientY,
        currentX: t.clientX, locked: null,
        id, el: e.currentTarget,
      };
    },
    cartSwipeMove(e, id) {
      if (this._swipe.id !== id) return;
      const t = e.touches[0];
      const dx = t.clientX - this._swipe.startX;
      const dy = t.clientY - this._swipe.startY;
      // Lock: erst Achse bestimmen (vermeidet konflikt mit vertikalem Scrollen)
      if (this._swipe.locked === null) {
        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
          this._swipe.locked = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
        }
      }
      if (this._swipe.locked !== 'x') return;
      this._swipe.currentX = t.clientX;
      // Bewege die Karte. Max ±150px (begrenzt sonst optisch)
      const limited = Math.max(-150, Math.min(150, dx));
      this._swipe.el.style.transform = `translateX(${limited}px)`;
      // Wenn Threshold erreicht: Hintergrund stärker zeigen via Klasse
      const parent = this._swipe.el.parentElement;
      parent.classList.toggle('swipe-active-left', dx >= 60);
      parent.classList.toggle('swipe-active-right', dx <= -60);
    },
    async cartSwipeEnd(e, it) {
      if (this._swipe.id !== it.id) return;
      const dx = this._swipe.currentX - this._swipe.startX;
      const el = this._swipe.el;
      const parent = el?.parentElement;
      const wasSwipe = this._swipe.locked === 'x';
      // Reset visual
      if (el) el.style.transform = '';
      if (parent) {
        parent.classList.remove('swipe-active-left', 'swipe-active-right');
      }
      this._swipe = { startX: 0, startY: 0, currentX: 0, locked: null, id: null, el: null };
      if (!wasSwipe) return;
      // Action triggern
      if (dx >= 60) {
        this.haptic(15);
        await this.toggleCartItem(it.id, !it.checked);
      } else if (dx <= -60) {
        this.haptic([20, 30, 20]);
        if (confirm(`„${it.name}" entfernen?`)) {
          await this.deleteCartItem(it.id);
        }
      }
    },

    async loadCart() {
      this.cart._loadController?.abort();
      const controller = new AbortController();
      this.cart._loadController = controller;
      const sequence = ++this.cart._loadSequence;
      const ownsRequest = () => sequence === this.cart._loadSequence && !controller.signal.aborted;
      const options = { silent: true, signal: controller.signal };
      this.cart.loading = true;
      this.cart.connectionError = '';
      try {
        const status = await this.api(
          'GET',
          '/api/einkauf/status',
          undefined,
          options
        );
        if (!status || !ownsRequest()) return;
        const external = !!status.configured;
        if (external !== this.cart.external) {
          this.cart.items = [];
          this.cart.list = [];
          this.cart.total = 0;
        }
        this.cart.external = external;

        if (external) {
          const groups = await this.api(
            'GET',
            '/api/einkauf/list?include_checked=true',
            undefined,
            options
          );
          if (!groups || !ownsRequest()) return;
          this.cart.list = Object.keys(groups)
            .sort((a, b) => a.localeCompare(b, 'de'))
            .map(category => ({ category, items: groups[category] || [] }));
          this.cart.total = Object.values(groups)
            .flat()
            .filter(item => !item.checked)
            .length;
          return;
        }

        const result = await this.api('GET', '/api/cart', undefined, options);
        if (result && ownsRequest()) this.cart.items = result.items || [];
      } catch (error) {
        if (ownsRequest()) {
          this.cart.connectionError = error?.detail || (this.cart.external
            ? 'Die verbundene Einkaufsliste ist nicht erreichbar.'
            : 'Die Einkaufsliste konnte nicht geladen werden.');
        }
      } finally {
        if (sequence === this.cart._loadSequence) this.cart.loading = false;
      }
    },

    _invalidateCartLoad() {
      // A snapshot requested before a saved mutation must not undo it on screen.
      this.cart._loadController?.abort();
      ++this.cart._loadSequence;
      this.cart.loading = false;
    },

    async addToCart() {
      if (this.cart.addBusy) return;
      this.cart.addBusy = true;
      try {
      if (this.cart.external) {
        const text = (this.cart.quickAdd || '').trim();
        if (!text) {
          this.showToast('Bitte einen Artikel eingeben', 'error');
          return;
        }
        await this.api('POST', '/api/einkauf/items', { raw_text: text });
        await this.api('POST', '/api/einkauf/consolidate');
        this.cart.quickAdd = '';
        await this.loadCart();
        return;
      }

      const name = (this.cart.add.name || '').trim();
      if (!name) { this.showToast('Zutat-Name fehlt', 'error'); return; }
      const r = await this.api('POST', '/api/cart/add', {
        name,
        amount: this.cart.add.amount,
        unit: this.cart.add.unit || null,
        category: this.cart.add.category || null,
      });
      if (r && r.ok) {
        this.cart.add = { name: '', amount: null, unit: '', category: '' };
        this.cart.suggestions = [];
        await this.loadCart();
      }
      } catch (error) {
        this.showToast('Artikel konnte nicht hinzugefügt werden: ' + error.message, 'error');
      } finally {
        this.cart.addBusy = false;
      }
    },

    async loadCartSuggestions() {
      this.cart._suggestionsController?.abort();
      const controller = new AbortController();
      this.cart._suggestionsController = controller;
      const sequence = ++this.cart._suggestionsSequence;
      this.cart.suggestions = [];
      if (this.cart.external) return;
      const query = (this.cart.add.name || '').trim();
      if (!query) return;
      const ownsRequest = () => sequence === this.cart._suggestionsSequence
        && !controller.signal.aborted && !this.cart.external
        && (this.cart.add.name || '').trim() === query;
      try {
        const result = await this.api(
          'GET', `/api/cart/suggestions?q=${encodeURIComponent(query)}&limit=8`,
          undefined, { silent: true, signal: controller.signal }
        );
        if (ownsRequest()) this.cart.suggestions = result?.items || [];
      } catch (_) {
        if (ownsRequest()) this.cart.suggestions = [];
      }
    },

    chooseCartSuggestion(item) {
      this.cart._suggestionsController?.abort();
      ++this.cart._suggestionsSequence;
      this.cart.add.name = item.name || '';
      this.cart.add.unit = item.default_unit || '';
      this.cart.add.category = item.category || '';
      this.cart.suggestions = [];
    },

    shoppingCategoryIcon(category) {
      return ({
        'Obst & Gemüse': '🍎', 'Bäckerei': '🥖', 'Fleisch & Fisch': '🥩',
        'Kühlregal': '🥛', 'Vorrat & Konserven': '🥫', 'Getränke': '🥤',
        'Tiefkühl': '❄️', 'Drogerie & Haushalt': '🧴', 'Sonstiges': '🛒',
      })[category || 'Sonstiges'] || '🛒';
    },

    localCartGroups() {
      const order = [
        'Obst & Gemüse', 'Bäckerei', 'Fleisch & Fisch', 'Kühlregal',
        'Vorrat & Konserven', 'Getränke', 'Tiefkühl',
        'Drogerie & Haushalt', 'Sonstiges',
      ];
      const groups = new Map();
      for (const item of this.cart.items || []) {
        const category = item.category || 'Sonstiges';
        if (!groups.has(category)) groups.set(category, []);
        groups.get(category).push(item);
      }
      return [...groups.entries()]
        .map(([category, items]) => ({ category, items }))
        .sort((a, b) => {
          const ai = order.indexOf(a.category), bi = order.indexOf(b.category);
          return (ai < 0 ? order.length : ai) - (bi < 0 ? order.length : bi)
            || a.category.localeCompare(b.category, 'de');
        });
    },

    async toggleCartItem(id, checked) {
      if (this.cart.external) {
        await this.api(
          'POST',
          `/api/einkauf/list/${id}/${checked ? 'check' : 'uncheck'}`,
          {}
        );
        await this.loadCart();
        return;
      }
      await this.api('PATCH', '/api/cart/' + id, { checked });
      this._invalidateCartLoad();
      // Lokal sofort updaten damit UI snappy ist; full reload nur bei größeren
      // Änderungen
      const it = this.cart.items.find(x => x.id === id);
      if (it) it.checked = checked;
    },

    async deleteCartItem(id) {
      if (this.cart.external) {
        await this.api('DELETE', '/api/einkauf/list/' + id);
        await this.loadCart();
        return;
      }
      await this.api('DELETE', '/api/cart/' + id);
      this._invalidateCartLoad();
      this.cart.items = this.cart.items.filter(x => x.id !== id);
    },

    async clearCart() {
      if (!confirm('Die gesamte Einkaufsliste leeren?')) return;
      if (this.cart.external) {
        await this.api('DELETE', '/api/einkauf/list/clear');
        this.showToast('Einkaufsliste geleert');
        await this.loadCart();
        return;
      }
      const r = await this.api('POST', '/api/cart/clear', { only_checked: false });
      if (r && r.ok) {
        this._invalidateCartLoad();
        this.cart.items = [];
        this.showToast('Gelöscht');
      }
    },

    async clearCheckedFromCart() {
      if (this.cart.external) {
        await this.api('DELETE', '/api/einkauf/list/clear-checked');
        this.showToast('Erledigte Artikel entfernt');
        await this.loadCart();
        return;
      }
      const r = await this.api('POST', '/api/cart/clear', { only_checked: true });
      if (r && r.ok) {
        this.showToast(`${r.deleted} erledigt-Posten gelöscht`);
        this.loadCart();
      }
    },

    cartTab(tab) {
      this.cart.tab = tab;
      if (tab === 'recurring') this.loadRecurring();
      if (tab === 'list') this.loadCart();
    },

    async loadRecurring() {
      this.cart.recLoading = true;
      this.cart.connectionError = '';
      try {
        const result = await this.api(
            'GET',
            '/api/cart/recurring',
            undefined,
            { silent: true }
          );
        this.cart.recurring = result?.items || [];
      } catch (error) {
        this.cart.recurring = [];
        this.cart.connectionError =
          error?.detail || 'Wiederkehrende Einkäufe konnten nicht geladen werden.';
      } finally {
        this.cart.recLoading = false;
      }
    },

    recEdit(rule) {
      this.cart.recForm = {
        id: rule.id,
        name: rule.name,
        category: rule.category || '',
        default_unit: rule.default_unit || '',
        interval_days: rule.interval_days || 7,
        active: !!rule.active,
      };
    },

    recReset() {
      this.cart.recForm = {
        id: null,
        name: '',
        category: '',
        default_unit: '',
        interval_days: 7,
        active: true,
      };
    },

    async recSave() {
      if (this.cart.recSaving) return;
      const form = this.cart.recForm;
      const name = (form.name || '').trim();
      if (!name) {
        this.showToast('Artikelname fehlt', 'error');
        return;
      }
      const intervalDays = Number(form.interval_days);
      if (!Number.isInteger(intervalDays) || intervalDays < 1 || intervalDays > 3650) {
        this.showToast('Intervall muss zwischen 1 und 3650 Tagen liegen', 'error');
        return;
      }

      const body = {
        name,
        category: (form.category || '').trim() || null,
        default_unit: (form.default_unit || '').trim() || null,
        interval_days: intervalDays,
        active: !!form.active,
      };
      this.cart.recSaving = true;
      try {
        if (form.id) {
          await this.api('PATCH', '/api/cart/recurring/' + form.id, body);
          this.showToast('Wiederkehrender Artikel aktualisiert');
        } else {
          await this.api('POST', '/api/cart/recurring', body);
          this.showToast('Wiederkehrender Artikel angelegt');
        }
        this.recReset();
        await this.loadRecurring();
      } finally {
        this.cart.recSaving = false;
      }
    },

    async recDelete(rule) {
      if (
        this.cart.recDeleting ||
        !confirm(`Wiederkehrenden Artikel „${rule.name}“ löschen?`)
      ) return;
      this.cart.recDeleting = rule.id;
      try {
        await this.api('DELETE', '/api/cart/recurring/' + rule.id);
        this.showToast('Wiederkehrender Artikel gelöscht');
        if (this.cart.recForm.id === rule.id) this.recReset();
        await this.loadRecurring();
      } finally {
        this.cart.recDeleting = null;
      }
    },

    async recRunNow() {
      if (this.cart.recRunning) return;
      this.cart.recRunning = true;
      try {
        const result = await this.api('POST', '/api/cart/recurring/run', {});
        const count = result?.added?.length || 0;
        this.showToast(
          count === 1
            ? '1 fälliger Artikel zur Einkaufsliste hinzugefügt'
            : `${count} fällige Artikel zur Einkaufsliste hinzugefügt`
        );
        await Promise.all([this.loadRecurring(), this.loadCart()]);
      } finally {
        this.cart.recRunning = false;
      }
    },

    recurringDueLabel(rule) {
      if (!rule.active) return 'pausiert';
      const days = Number(rule.due_in_days || 0);
      if (days <= 0) return 'jetzt fällig';
      return days === 1 ? 'morgen fällig' : `in ${days} Tagen fällig`;
    },
    };
  };
})();
