// meal-plan: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["meal-plan"] = function () {
    return {

    // ── Wochenplan ────────────────────────────────────────────────────
    async loadMealPlan(weekStart = '') {
      this.mealPlan._loadController?.abort();
      const controller = new AbortController();
      this.mealPlan._loadController = controller;
      const sequence = ++this.mealPlan._loadSequence;
      this.mealPlan.requestedWeekStart = weekStart;
      this.mealPlan.loading = true;
      this.mealPlan.error = '';
      try {
        const query = weekStart ? `?week_start=${encodeURIComponent(weekStart)}` : '';
        const result = await this.api(
          'GET',
          `/api/meal-plan${query}`,
          undefined,
          { silent: true, signal: controller.signal },
        );
        if (!result || controller.signal.aborted || sequence !== this.mealPlan._loadSequence) return;
        this.mealPlan = {
          ...this.mealPlan,
          weekStart: result.week_start,
          weekEnd: result.week_end,
          previousWeek: result.previous_week,
          nextWeek: result.next_week,
          isCurrentWeek: !!result.is_current_week,
          days: result.days || [],
          shoppingPreview: result.shopping_preview || [],
          summary: result.summary || {
            planned_meals: 0,
            planned_days: 0,
            shopping_items: 0,
          },
          loading: false,
          error: '',
        };
      } catch (error) {
        if (!controller.signal.aborted && sequence === this.mealPlan._loadSequence) {
          this.mealPlan.error =
            error?.detail || 'Der Wochenplan konnte nicht geladen werden.';
        }
      } finally {
        if (sequence === this.mealPlan._loadSequence) this.mealPlan.loading = false;
      }
    },

    async loadMealPlanRecipeOptions() {
      if (this.mealPlan.optionsLoaded) return;
      if (this.mealPlan._optionsPromise) return this.mealPlan._optionsPromise;
      this.mealPlan._optionsPromise = (async () => {
        const pageSize = 500;
        let offset = 0;
        const options = [];
        while (true) {
          const result = await this.api(
            'GET',
            `/api/recipes?limit=${pageSize}&offset=${offset}`,
            undefined,
            { silent: true },
          );
          if (!result || !Array.isArray(result.items)) {
            throw new Error('Rezepte konnten nicht geladen werden');
          }
          const page = result.items;
          options.push(...page);
          const total = Number(result.total) || options.length;
          if (!page.length || options.length >= total || page.length < pageSize) break;
          offset += page.length;
        }
        this.mealPlan.recipeOptions = options
          .sort((a, b) => (a.name || '').localeCompare(b.name || '', 'de'));
        this.mealPlan.optionsLoaded = true;
      })();
      try {
        return await this.mealPlan._optionsPromise;
      } finally {
        this.mealPlan._optionsPromise = null;
      }
    },

    async openMealPlanAdd(day) {
      this.mealPlan.addingFor = day.date;
      this.mealPlan.draft = { recipe_id: '', planned_servings: 2 };
      try {
        await this.loadMealPlanRecipeOptions();
      } catch (error) {
        this.showToast(
          error?.detail || 'Rezepte konnten nicht geladen werden',
          'error',
        );
      }
      this.$nextTick(() => this.$refs.mealPlanRecipeSelect?.focus());
    },

    closeMealPlanAdd() {
      this.mealPlan.addingFor = '';
      this.mealPlan.draft = { recipe_id: '', planned_servings: 2 };
    },

    mealPlanRecipeChanged() {
      const selected = this.mealPlan.recipeOptions.find(
        item => Number(item.id) === Number(this.mealPlan.draft.recipe_id),
      );
      this.mealPlan.draft.planned_servings = Number(selected?.servings) || 2;
    },

    async addMealPlanItem() {
      const recipeId = Number(this.mealPlan.draft.recipe_id);
      const servings = Number(this.mealPlan.draft.planned_servings);
      if (!recipeId) {
        this.showToast('Bitte ein Rezept auswählen', 'error');
        return;
      }
      this.mealPlan.saving = true;
      try {
        await this.api('POST', '/api/meal-plan/items', {
          planned_for: this.mealPlan.addingFor,
          recipe_id: recipeId,
          planned_servings: servings || 2,
        });
        this.closeMealPlanAdd();
        await this.loadMealPlan(this.mealPlan.weekStart);
        this.showToast('Rezept eingeplant');
      } finally {
        this.mealPlan.saving = false;
      }
    },

    async updateMealPlanServings(item) {
      const servings = Math.max(1, Math.min(24, Number(item.planned_servings) || 1));
      item.planned_servings = servings;
      this.mealPlan.saving = true;
      try {
        await this.api('PATCH', `/api/meal-plan/items/${item.id}`, {
          planned_servings: servings,
        });
        await this.loadMealPlan(this.mealPlan.weekStart);
      } finally {
        this.mealPlan.saving = false;
      }
    },

    async removeMealPlanItem(item) {
      if (!confirm(`„${item.recipe_name}“ aus dem Wochenplan entfernen?`)) return;
      this.mealPlan.saving = true;
      try {
        await this.api('DELETE', `/api/meal-plan/items/${item.id}`);
        await this.loadMealPlan(this.mealPlan.weekStart);
        this.showToast('Aus dem Wochenplan entfernt');
      } finally {
        this.mealPlan.saving = false;
      }
    },

    changeMealPlanWeek(weekStart) {
      if (weekStart === undefined || this.mealPlan.loading) return;
      this.closeMealPlanAdd();
      this.loadMealPlan(weekStart);
    },

    mealPlanRangeLabel() {
      if (!this.mealPlan.weekStart || !this.mealPlan.weekEnd) return '';
      const formatter = new Intl.DateTimeFormat('de-DE', {
        day: '2-digit',
        month: 'short',
      });
      const start = formatter.format(new Date(`${this.mealPlan.weekStart}T12:00:00`));
      const end = formatter.format(new Date(`${this.mealPlan.weekEnd}T12:00:00`));
      return `${start} – ${end}`;
    },

    mealPlanDayDate(value) {
      if (!value) return '';
      return new Intl.DateTimeFormat('de-DE', {
        day: '2-digit',
        month: '2-digit',
      }).format(new Date(`${value}T12:00:00`));
    },

    mealPlanAmount(item) {
      const amount = item?.amount;
      if (amount === null || amount === undefined) return item?.unit || '';
      const value = Number(amount);
      const formatted = Number.isInteger(value)
        ? String(value)
        : value.toLocaleString('de-DE', { maximumFractionDigits: 2 });
      return [formatted, item?.unit].filter(Boolean).join(' ');
    },

    mealPlanPdfUrl() {
      const query = this.mealPlan.weekStart
        ? `?week_start=${encodeURIComponent(this.mealPlan.weekStart)}`
        : '';
      return `/api/meal-plan/pdf${query}`;
    },

    mealPlanPdfFilename() {
      return `wochenplan-${this.mealPlan.weekStart || 'aktuell'}.pdf`;
    },

    async downloadMealPlanPdf() {
      if (this.mealPlan.pdfBusy) return;
      this.mealPlan.pdfBusy = true;
      try {
        const blob = await this.fetchPdf(this.mealPlanPdfUrl());
        if (!blob) return;
        this.savePdf(blob, this.mealPlanPdfFilename());
        this.showToast('Wochenplan-PDF heruntergeladen');
      } catch (error) {
        this.showToast(error?.message || 'PDF-Erstellung fehlgeschlagen', 'error');
      } finally {
        this.mealPlan.pdfBusy = false;
      }
    },

    async shareMealPlanPdf() {
      if (this.mealPlan.pdfBusy) return;
      this.mealPlan.pdfBusy = true;
      try {
        const blob = await this.fetchPdf(this.mealPlanPdfUrl());
        if (!blob) return;
        const range = this.mealPlanRangeLabel();
        await this.sharePdf({
          blob,
          filename: this.mealPlanPdfFilename(),
          title: `Wochenplan ${range}`,
          text: 'Unser Wochenplan mit gemeinsamer Einkaufsliste',
          mailSubject: `Wochenplan ${range}`,
          mailBody: 'Hallo,\n\nanbei unser Wochenplan mit gemeinsamer Einkaufsliste.\n\n'
            + 'Das PDF wurde bereits heruntergeladen und kann an diese Mail angehängt werden.',
        });
      } catch (error) {
        if (error?.name !== 'AbortError') {
          this.showToast(error?.message || 'Teilen fehlgeschlagen', 'error');
        }
      } finally {
        this.mealPlan.pdfBusy = false;
      }
    },

    async createMealPlanCart() {
      if (!this.mealPlan.summary.shopping_items) {
        this.showToast('Diese Woche enthält noch keine einkaufbaren Zutaten', 'error');
        return;
      }
      await this.loadCart();
      const currentCount = this.cart.external ? this.cart.total : this.cart.items.length;
      const prompt = this.cart.external
        ? 'Die Wochenliste wird zur gemeinsamen Einkaufsliste hinzugefügt. Fortfahren?'
        : currentCount
          ? `Die aktuelle Einkaufsliste mit ${currentCount} Artikel(n) wird durch diese Woche ersetzt. Fortfahren?`
          : 'Einkaufsliste aus dieser Woche erstellen?';
      if (!confirm(prompt)) return;
      this.mealPlan.saving = true;
      try {
        const result = await this.api('POST', '/api/meal-plan/cart', {
          week_start: this.mealPlan.weekStart,
        });
        await this.loadCart();
        this.showToast(
          `${result?.added || 0} Zutaten in die Einkaufsliste übernommen`,
        );
        this.navTo('cart');
      } finally {
        this.mealPlan.saving = false;
      }
    },
    };
  };
})();
