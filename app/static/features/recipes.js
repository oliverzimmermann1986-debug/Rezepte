// recipes: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["recipes"] = function () {
    return {

    recipeStatus(recipe = {}) {
      if (['pending', 'running'].includes(recipe.ingredients_status)) {
        return { label: '⏳ Zutaten werden ermittelt', tone: 'warning' };
      }
      if (recipe.ingredients_status === 'error') {
        return { label: '⚠ Auswertung fehlgeschlagen', tone: 'danger' };
      }
      if (recipe.ingredients_status === 'skipped') {
        return { label: '⚠ Beschreibung fehlt', tone: 'warning' };
      }
      if (recipe.ingredients_status === 'ok' && !recipe.needs_manual_care
          && recipe.ingredients_count > 0 && recipe.steps_count > 0) {
        return { label: '✓ Kochfertig', tone: 'success' };
      }
      return { label: '⚠ Manuell pflegen', tone: 'warning' };
    },

    recipeThumbnailUrl(recipe, width = null) {
      if (!recipe?.id) return '';
      const params = new URLSearchParams();
      if (width) params.set('w', width);
      const revision = this.recipeImageRevisions[recipe.id] || recipe.image_generated_at;
      if (revision) params.set('v', revision);
      const query = params.toString();
      return `/api/recipes/${recipe.id}/thumb${query ? '?' + query : ''}`;
    },

    invalidateRecipeThumbnail(recipeId) {
      this.recipeImageRevisions = { ...this.recipeImageRevisions,
        [recipeId]: `${Date.now()}-${++this._imageRevisionSequence}` };
    },

    async toggleHouseholdRecipe() {
      const recipe = this.recipeDetail.data;
      if (!recipe || !this.canWrite() || this.recipeDetail.savingLibrary) return;
      const context = this._detailContext();
      this.recipeDetail.savingLibrary = true;
      try {
        const result = await this.api(recipe.in_library ? 'DELETE' : 'POST', `/api/recipes/${recipe.id}/save`, {});
        if (this._detailOwns(context)) {
          recipe.in_library = !!result.in_library;
          if (!recipe.in_library) { recipe.is_favorite = false; recipe.rating = 0; }
        }
        await this.loadRecipes();
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.savingLibrary = false;
      }
    },

    async generateRecipeImage() {
      if (!this.canUseAdminTools()) return;
      const recipe = this.recipeDetail.data;
      if (!recipe || this.recipeDetail.imageGenerating) return;
      if (!this.confirmAIProcessing('Ein Rezeptbild erzeugen', 'Rezepttitel, Zutaten und Zubereitungshinweise', 'OpenAI erzeugt daraus ein neues Bild. Das bisherige Bild wird vorher gesichert.')) return;
      const context = this._detailContext();
      this.recipeDetail.imageGenerating = true;
      try {
        await this.api('POST', `/api/recipes/${recipe.id}/generate-image`, this.aiProcessingPayload());
        if (this._detailOwns(context)) recipe.image_generation_status = 'pending';
        this.showToast('Bildgenerierung gestartet; das bisherige Bild wird vorher gesichert');
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.imageGenerating = false;
      }
    },

    async createOwnRecipeVariant() {
      const source = this.recipeDetail.data;
      if (!source?.id || !this.canWrite() || this.recipeDetail.duplicating === source.id) return;
      const entered = prompt('Name der eigenen Variante', `${source.name || 'Rezept'} – Variante`);
      if (entered === null) return;
      const newName = entered.trim();
      if (!newName || [...newName].length > 200) {
        this.showToast('Bitte einen Namen mit 1 bis 200 Zeichen eingeben.', 'err');
        return;
      }
      if (!this.canWrite()) return;
      const context = this._detailContext();
      this.recipeDetail.duplicating = source.id;
      try {
        const result = await this.api('POST', `/api/recipes/${source.id}/duplicate`, { new_name: newName });
        if (!result?.recipe_id) return;
        this.loadRecipes();
        if (this._detailOwns(context)) {
          this.recipeDetail.duplicating = false;
          await this.openRecipe(result.recipe_id);
          this.showToast('Eigene Variante erstellt', 'ok');
        }
      } catch (error) {
        this.showToast(error?.message || 'Die eigene Variante konnte nicht erstellt werden.', 'err');
      } finally {
        if (this.recipeDetail.duplicating === source.id) this.recipeDetail.duplicating = false;
      }
    },

    async restoreRecipeImageBackup(item) {
      if (!item?.id || this.recipeDetail.imageRestoring
          || !confirm('Dieses gesicherte Bild wieder als aktives Rezeptbild setzen?')) return;
      const context = this._detailContext();
      if (!context.id) return;
      this.recipeDetail.imageRestoring = item.id;
      try {
        await this.api('POST', `/api/recipes/image-backups/${item.id}/restore`, {});
        this.invalidateRecipeThumbnail(context.id);
        this.showToast('Altes Rezeptbild wiederhergestellt');
        if (this._detailOwns(context)) await this.openRecipe(context.id);
        await this.loadRecipes();
      } finally {
        if (this._detailOwns(context) && this.recipeDetail.imageRestoring === item.id) {
          this.recipeDetail.imageRestoring = null;
        }
      }
    },

    // ── Stoppuhr pro Schritt ─────────────────────────────────────────
    startStepTimer(step) {
      if (!step || !step.timer_seconds) return;
      // Falls vorher schon ein Interval drauf war: aufräumen
      this.stopStepTimer(step, { silent: true });
      const id = step.id;
      const state = {
        status: 'running',     // 'idle' | 'running' | 'done'
        remaining: step.timer_seconds,
        endsAt: Date.now() + Number(step.timer_seconds) * 1000,
        intervalId: null,
        running: true,         // CSS hint
        notified: false,
      };
      this.timers[id] = state;
      const tick = () => {
        state.remaining = Math.max(0, Math.ceil((state.endsAt - Date.now()) / 1000));
        if (state.remaining <= 0) {
          state.remaining = 0;
          state.status = 'done';
          state.running = false;
          clearInterval(state.intervalId);
          state.intervalId = null;
          if (!state.notified) {
            state.notified = true;
            this._playTimerDoneSound();
            this.showToast('⏰ Timer fertig: ' + (step.instruction || '').slice(0, 60), 'ok');
          }
        }
        // Alpine reactivity: timers selber neu zuweisen erzwingt Re-Render
        this.timers = { ...this.timers, [id]: { ...state } };
      };
      state.intervalId = setInterval(tick, 1000);
      tick();
    },

    stopStepTimer(step, opts = {}) {
      if (!step) return;
      const id = step.id;
      const t = this.timers[id];
      if (t && t.intervalId) clearInterval(t.intervalId);
      const next = { ...this.timers };
      delete next[id];
      this.timers = next;
      if (!opts.silent && t && t.status !== 'done') {
        this.showToast('Timer gestoppt', 'info');
      }
    },

    _playTimerDoneSound() {
      // Web-Audio-API, kein externes Asset nötig. 3 Pieptöne, 800Hz.
      try {
        if (!this._audioCtx) {
          const AC = window.AudioContext || window.webkitAudioContext;
          if (!AC) return;
          this._audioCtx = new AC();
        }
        const ctx = this._audioCtx;
        const now = ctx.currentTime;
        for (let i = 0; i < 3; i++) {
          const t = now + i * 0.25;
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.value = 800;
          osc.type = 'sine';
          gain.gain.setValueAtTime(0, t);
          gain.gain.linearRampToValueAtTime(0.3, t + 0.01);
          gain.gain.linearRampToValueAtTime(0, t + 0.18);
          osc.connect(gain).connect(ctx.destination);
          osc.start(t);
          osc.stop(t + 0.2);
        }
      } catch (e) { /* silent: Audio ist nice-to-have */ }
    },

    _buildRecipeQuery() {
      const f = this.recipes.filters;
      const params = new URLSearchParams();
      if (f.library && f.library !== 'all') params.set('library', f.library);
      if (f.search) params.set('search', f.search);
      if (f.type) params.set('type', f.type);
      if (f.category) params.set('category', f.category);
      if (f.ingredients_status) params.set('ingredients_status', f.ingredients_status);
      if (f.verified !== '' && f.verified !== undefined && f.verified !== null) {
        params.set('verified', f.verified ? 'true' : 'false');
      }
      if (f.favorite_only) params.set('favorite_only', 'true');
      if (f.min_rating > 0) params.set('min_rating', f.min_rating);
      f.tag_ids.forEach(id => params.append('tag_id', id));
      f.ingredients.forEach(name => params.append('ingredient', name));
      f.excludedIngredients.forEach(name => params.append('exclude_ingredient', name));
      params.set('limit', f.limit);
      params.set('offset', f.offset);
      return params.toString();
    },

    // ── Recipe-Liste + Facets ─────────────────────────────────────────
    async loadRecipes() {
      // Alte Filter-/Suchanfrage abbrechen: langsame Antworten dürfen keinen
      // neueren UI-Zustand überschreiben.
      this.recipes._listController?.abort();
      this.recipes._moreController?.abort();
      this.recipes._facetsController?.abort();
      this.recipes.loadingMore = false;
      const controller = new AbortController();
      this.recipes._listController = controller;
      const sequence = ++this.recipes._loadSequence;
      this.recipes.filters.offset = 0;
      this.recipes.loading = true;
      this.recipes.error = '';
      this.recipes.moreError = '';
      try {
        const r = await this.api(
          'GET', '/api/recipes?' + this._buildRecipeQuery(), undefined,
          { signal: controller.signal, silent: true }
        );
        if (!r || sequence !== this.recipes._loadSequence) return;
        this.recipes.items = r.items || [];
        this.recipes.total = r.total || 0;
        this.recipes.searchMeta = r.search_meta || { corrected: false, original: '', query: '', suggestion: '' };
        this.recipes.extractionRunning = !!r.extraction_running;
        if (r.sync) this.recipes.sync = { ...this.recipes.sync, ...r.sync };
        if (this.recipes.sync.running) this._scheduleSyncPoll();
        if (this.recipes.extractionRunning) this._scheduleExtractionPoll();
        this.loadFacets();
      } catch (error) {
        if (!controller.signal.aborted && sequence === this.recipes._loadSequence) {
          this.recipes.error = error?.detail || 'Die Rezepte konnten nicht geladen werden.';
        }
      } finally {
        if (sequence === this.recipes._loadSequence) this.recipes.loading = false;
      }
    },

    async loadFacets() {
      this.recipes._facetsController?.abort();
      const controller = new AbortController();
      this.recipes._facetsController = controller;
      const sequence = this.recipes._loadSequence;
      try {
        const r = await this.api(
          'GET', '/api/recipes/facets?' + this._buildRecipeQuery(), undefined,
          { signal: controller.signal, silent: true }
        );
        if (r && !controller.signal.aborted && sequence === this.recipes._loadSequence
            && controller === this.recipes._facetsController) this.recipes.facets = r;
      } catch (_) {
        // The library remains usable if optional filter metadata is unavailable.
      }
    },

    applySearchSuggestion() {
      const suggestion = this.recipes.searchMeta?.suggestion || this.recipes.searchMeta?.query;
      if (!suggestion) return;
      this.recipes.filters.search = suggestion;
      this.recipes.filters.offset = 0;
      this.loadRecipes();
    },

    openRecipeVersions() {
      const id = this.recipeDetail?.data?.id;
      if (!id) return;
      this.admin.versionRecipeId = String(id);
      this.closeRecipeDetail();
      this.navTo('admin');
      this.selectAdminTab('versions');
    },

    resetFilters() {
      this.recipes.filters = {
        search: '', type: '', category: '', tag_ids: [],
        ingredients: [], excludedIngredients: [],
        ingredients_status: '', verified: '', favorite_only: false, min_rating: 0,
        limit: 60, offset: 0,
      };
      this.loadRecipes();
    },

    toggleTagFilter(id) {
      const arr = this.recipes.filters.tag_ids;
      const i = arr.indexOf(id);
      if (i >= 0) arr.splice(i, 1); else arr.push(id);
      this.recipes.filters.offset = 0;
      this.loadRecipes();
    },

    // Zählt aktive Filter — für den "Filter"-Button-Badge auf Mobile, sodass
    // der User sieht ob Filter gesetzt sind ohne den Drawer öffnen zu müssen.
    activeFilterCount() {
      const f = this.recipes.filters;
      let n = 0;
      if (f.search) n++;
      if (f.type) n++;
      if (f.category) n++;
      if (f.ingredients_status) n++;
      if (f.verified !== '' && f.verified !== undefined && f.verified !== null) n++;
      if (f.favorite_only) n++;
      if (f.min_rating > 0) n++;
      n += (f.tag_ids || []).length;
      n += (f.ingredients || []).length;
      n += (f.excludedIngredients || []).length;
      return n;
    },

    setIngredientFilter(canonicalName, mode) {
      const included = this.recipes.filters.ingredients;
      const excluded = this.recipes.filters.excludedIngredients;
      const selected = mode === 'include' ? included : excluded;
      const wasSelected = selected.includes(canonicalName);
      this.recipes.filters.ingredients = included.filter(name => name !== canonicalName);
      this.recipes.filters.excludedIngredients = excluded.filter(name => name !== canonicalName);
      if (!wasSelected) {
        (mode === 'include' ? this.recipes.filters.ingredients
          : this.recipes.filters.excludedIngredients).push(canonicalName);
      }
      this.recipes.filters.offset = 0;
      this.loadRecipes();
    },

    visibleIngredientFilters() {
      const selected = [...this.recipes.filters.ingredients, ...this.recipes.filters.excludedIngredients];
      return selected.map(canonical => this.recipes.facets.ingredients.find(
        item => item.canonical_name === canonical,
      ) || { canonical_name: canonical, display_name: canonical, n: 0 })
        .concat(this.filteredIngredientFacets());
    },

    ingredientFilterSummary() {
      const included = this.recipes.filters.ingredients.length;
      const excluded = this.recipes.filters.excludedIngredients.length;
      if (!included && !excluded) return '';
      const parts = [];
      if (included) parts.push(`${included} drin`);
      if (excluded) parts.push(`${excluded} raus`);
      return `(${parts.join(' · ')})`;
    },

    // Zutaten-Chip-Liste filtern: Suche im Display-Name, Limit 60
    // (sonst rendert bei vielen Rezepten 200+ Chips → unbenutzbar).
    // Bereits ausgewählte werden eh oben separat angezeigt, hier ausschließen.
    filteredIngredientFacets() {
      const all = this.recipes.facets.ingredients || [];
      const selected = new Set([
        ...this.recipes.filters.ingredients,
        ...this.recipes.filters.excludedIngredients,
      ]);
      const q = (this.recipes.ingredientSearch || '').toLowerCase().trim();
      let filtered = all.filter(i => !selected.has(i.canonical_name));
      if (q) {
        filtered = filtered.filter(i =>
          (i.display_name || '').toLowerCase().includes(q) ||
          (i.canonical_name || '').toLowerCase().includes(q)
        );
      }
      const MAX = 60;
      this.recipes._ingFacetsLimited = filtered.length > MAX;
      return filtered.slice(0, MAX);
    },

    async syncRecipes() {
      if (!this.canUseAdminTools()) return;
      const r = await this.api('POST', '/api/recipes/sync');
      if (!r) return;
      this.recipes.sync = { ...this.recipes.sync, ...r, running: !!r.running };
      this.showToast(r.already_running ? 'Synchronisierung läuft bereits' : 'Synchronisierung gestartet');
      this._scheduleSyncPoll();
    },
    syncResultLabel() {
      const r = this.recipes.sync?.result;
      if (!r) return '';
      return `Zuletzt: ${r.scanned || 0} geprüft · ${r.added || 0} neu · ${r.updated || 0} aktualisiert`;
    },
    _scheduleSyncPoll() {
      if (!this._syncPoller) {
        this._syncPoller = window.RezepteRuntime.createPoller(async () => {
          if (document.hidden) return false;
          const previousRun = this.recipes.sync?.run_id;
          const wasRunning = !!this.recipes.sync?.running;
          const state = await this.api('GET', '/api/recipes/sync/status', undefined, { silent: true });
          if (!state) return false;
          this.recipes.sync = { ...this.recipes.sync, ...state };
          if (wasRunning && !state.running) {
            this._syncPoller.stop();
            if (state.error) this.showToast('Synchronisierung fehlgeschlagen', 'err');
            else this.showToast(this.syncResultLabel() || 'Synchronisierung abgeschlossen');
            await this.loadRecipes();
            return true;
          }
          return previousRun !== state.run_id || wasRunning !== !!state.running;
        }, {
          minDelay: 1000,
          maxDelay: 5000,
          isActive: () => !document.hidden && !!this.recipes.sync?.running,
        });
      }
      this._syncPoller.start();
    },

    // ── Extraction-Polling ────────────────────────────────────────────
    _scheduleExtractionPoll() {
      if (!this.recipes.extractionRunning) return;
      if (!this._extractionPoller) {
        this._extractionPoller = window.RezepteRuntime.createPoller(async () => {
          const s = await this.api('GET', '/api/recipes/extraction/status', undefined, { silent: true });
          if (!s) return false;
          const wasRunning = this.recipes.extractionRunning;
          const oldPending = this.recipes.extractionPending;
          this.recipes.extractionRunning = !!s.running;
          this.recipes.extractionStats = s.stats || {};
          this.recipes.extractionPending = s.stats?.pending || 0;
          if (!s.running) {
            this._extractionPoller.stop();
            if (wasRunning && this.page === 'recipes') this.loadRecipes();
          }
          return oldPending !== this.recipes.extractionPending || wasRunning !== !!s.running;
        }, {
          minDelay: 3000,
          maxDelay: 15000,
          isActive: () => !document.hidden && !!this.recipes.extractionRunning,
        });
      }
      this._extractionPoller.start();
    },

    // ── Detail-Modal ──────────────────────────────────────────────────
    // Platzhalter-Emoji für Rezepte ohne Bild — nach Kategorie/Typ.
    // Reihenfolge: spezifische Kategorie zuerst, dann Typ-Fallback.
    recipeEmoji(r) {
      const hay = ((r.category || '') + ' ' + (r.type || '') + ' ' + (r.name || '')).toLowerCase();
      const map = [
        ['curry', '🍛'], ['suppe', '🍲'], ['eintopf', '🍲'], ['salat', '🥗'],
        ['bowl', '🥗'], ['pasta', '🍝'], ['nudel', '🍝'], ['spaghetti', '🍝'],
        ['pizza', '🍕'], ['burger', '🍔'], ['wrap', '🌯'], ['taco', '🌮'],
        ['reis', '🍚'], ['risotto', '🍚'], ['auflauf', '🧀'], ['gratin', '🧀'],
        ['pfannkuchen', '🥞'], ['pancake', '🥞'], ['waffel', '🧇'],
        ['brot', '🥖'], ['bagel', '🥯'], ['sandwich', '🥪'], ['toast', '🍞'],
        ['fisch', '🐟'], ['lachs', '🐟'], ['thunfisch', '🐟'], ['garnele', '🦐'],
        ['fleisch', '🥩'], ['steak', '🥩'], ['hähnchen', '🍗'], ['huhn', '🍗'],
        ['hühnchen', '🍗'], ['ei', '🍳'], ['frühstück', '🍳'],
        ['dessert', '🍰'], ['kuchen', '🍰'], ['torte', '🎂'], ['keks', '🍪'],
        ['eis', '🍨'], ['smoothie', '🥤'], ['getränk', '🥤'], ['cocktail', '🍹'],
        ['kartoffel', '🥔'], ['gemüse', '🥦'], ['vegan', '🥦'],
      ];
      for (const [kw, emo] of map) { if (hay.includes(kw)) return emo; }
      return '🍽️';
    },

    // Deterministischer Farbverlauf aus dem Namen — gleiches Rezept bekommt
    // immer denselben Verlauf. Hash → Hue, zwei nah beieinanderliegende Töne.
    recipePlaceholderGradient(name) {
      let h = 0;
      const s = name || 'x';
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
      const h2 = (h + 35) % 360;
      return `linear-gradient(135deg, hsl(${h} 45% 38%), hsl(${h2} 50% 28%))`;
    },

    recipeStatusLabel(recipe) {
      if (recipe?.needs_manual_care) return 'Manuelle Pflege erforderlich';
      const labels = {
        ok: 'Zutaten bereit', pending: 'Zutaten werden vorbereitet',
        running: 'Zutaten werden erkannt', error: 'Zutaten konnten nicht erkannt werden',
        skipped: 'Zutaten noch nicht erfasst',
      };
      return labels[recipe?.ingredients_status] || (recipe?.ingredients_count
        ? 'Zutaten vorhanden' : 'Zutaten noch nicht erfasst');
    },
    prefetchRecipeDetail(id) {
      if (this._detailPrefetch.has(id)) return;  // already fetched
      const promise = this.api(
        'GET',
        '/api/recipes/' + id,
        undefined,
        {silent: true},
      ).catch(() => null);
      this._detailPrefetch.set(id, promise);
      // Cache nach 30s expirieren damit stale Daten nicht ewig leben
      setTimeout(() => {
        if (this._detailPrefetch.get(id) === promise) {
          this._detailPrefetch.delete(id);
        }
      }, 30000);
    },

    _detailContext() {
      return {
        id: Number(this.recipeDetail.activeId || this.recipeDetail.data?.id || 0),
        epoch: Number(this.recipeDetail.requestEpoch || 0),
      };
    },

    _detailOwns(context) {
      return Boolean(
        context?.id
        && this.recipeDetail.show
        && Number(this.recipeDetail.activeId) === Number(context.id)
        && Number(this.recipeDetail.requestEpoch) === Number(context.epoch)
      );
    },

    async openRecipe(id) {
      id = Number(id);
      this.recipeDetail.controller?.abort();
      this._releaseWakeLock();
      this.$refs?.recipeActions?.removeAttribute('open');
      const controller = new AbortController();
      const epoch = ++this.recipeDetail.requestEpoch;
      this.recipeDetail.controller = controller;
      this.recipeDetail.activeId = id;
      this.recipeDetail.show = true;
      this.recipeDetail.data = null;
      this.recipeDetail.loading = true;
      this.recipeDetail.error = '';
      this.recipeDetail.newTag = '';
      this.recipeDetail.multiplier = 1;
      this.recipeDetail.cookMode = false;
      this.recipeDetail.tab = 'info';
      this.recipeDetail.editingIngredients = false;
      this.recipeDetail.editIngs = [];
      this.recipeDetail.savingIngredients = false;
      this.recipeDetail.editingSteps = false;
      this.recipeDetail.editSteps = [];
      this.recipeDetail.savingSteps = false;
      this.recipeDetail.savingLibrary = false;
      this.recipeDetail.extracting = false;
      this.recipeDetail.verifying = false;
      this.recipeDetail.rescraping = false;
      this.recipeDetail.imageGenerating = false;
      this.recipeDetail.imageRestoring = null;
      // Prefetched-Promise nutzen falls da, sonst fresh fetch
      const context = {id, epoch};
      const cached = this._detailPrefetch.get(id);
      try {
        let r = cached ? await cached : null;
        if (!r && this._detailOwns(context)) {
          r = await this.api('GET', '/api/recipes/' + id, undefined,
            {signal: controller.signal, silent: true});
        }
        if (!this._detailOwns(context)) return;
        if (!r) throw new Error('Das Rezept konnte nicht geladen werden.');
        this.recipeDetail.data = r;
      } catch (error) {
        if (this._detailOwns(context) && !controller.signal.aborted) {
          this.recipeDetail.error = error?.message || 'Das Rezept konnte nicht geladen werden.';
        }
      } finally {
        if (this._detailPrefetch.get(id) === cached) this._detailPrefetch.delete(id);
        if (this._detailOwns(context)) this.recipeDetail.loading = false;
      }
    },

    async toggleCookMode() {
      const next = !this.recipeDetail.cookMode;
      this.recipeDetail.cookMode = next;
      if (next) {
        await this._acquireWakeLock();
      } else {
        await this._releaseWakeLock();
      }
    },

    async _acquireWakeLock() {
      // Wake-Lock-API ist nicht überall verfügbar (Safari iOS erst seit 16.4,
      // Firefox erst seit 126). Bei Nicht-Verfügbarkeit graceful weiter — die
      // CSS-only-Cook-Mode-Optik funktioniert eh.
      if (!('wakeLock' in navigator)) return;
      const epoch = ++this._wakeLockEpoch;
      try {
        const lock = await navigator.wakeLock.request('screen');
        if (
          epoch !== this._wakeLockEpoch
          || !this.recipeDetail.show
          || !this.recipeDetail.cookMode
        ) {
          try { await lock.release(); } catch (_) {}
          return;
        }
        this._wakeLock = lock;
        this.recipeDetail.wakeLockActive = true;
        // Browser kann Lock implizit beenden (z.B. Tab im Hintergrund) —
        // Listener informiert uns damit der UI-Indikator stimmt.
        lock.addEventListener('release', () => {
          if (this._wakeLock === lock) {
            this._wakeLock = null;
            this.recipeDetail.wakeLockActive = false;
          }
        });
      } catch (e) {
        // Verweigerung (User-Gesture fehlt o.ä.) — kein Showstopper
        this.recipeDetail.wakeLockActive = false;
      }
    },

    async _releaseWakeLock() {
      this._wakeLockEpoch += 1;
      this.recipeDetail.wakeLockActive = false;
      const lock = this._wakeLock;
      this._wakeLock = null;
      if (lock) {
        try { await lock.release(); } catch (e) {}
      }
    },

    closeRecipeDetail() {
      // Step-Timer bleiben bewusst aktiv, wenn das Rezept geschlossen wird.
      // In der Küche muss ein 20-Minuten-Timer weiterlaufen, während der User
      // ein anderes Rezept oder die Einkaufsliste öffnet. Die Timer basieren
      // auf endsAt und korrigieren daher auch Hintergrund-/Sleep-Pausen.
      // Wake-Lock + Cook-Mode resetten — sonst hält der Lock weiter und der
      // nächste open würde mit angeschaltetem Cook-Mode starten
      this._releaseWakeLock();
      this.recipeDetail.cookMode = false;
      this.recipeDetail.controller?.abort();
      this.recipeDetail.controller = null;
      this.recipeDetail.requestEpoch += 1;
      this.recipeDetail.activeId = null;
      this.recipeDetail.show = false;
      this.recipeDetail.data = null;
      this.recipeDetail.loading = false;
      this.recipeDetail.error = '';
      this.$refs?.recipeActions?.removeAttribute('open');
      this.recipeDetail.imageGenerating = false;
      this.recipeDetail.imageRestoring = null;
      // Edit-State + ephemerale Loading-States ZWINGEND clearen
      this.recipeDetail.editingIngredients = false;
      this.recipeDetail.editIngs = [];
    },

    async addTagToRecipe() {
      const name = (this.recipeDetail.newTag || '').trim();
      if (!name || !this.recipeDetail.data) return;
      const context = this._detailContext();
      // Nur User-Tags durchreichen — Backend recipe_tags_set ersetzt
      // ohnehin nur auto=0; Auto-Tags bleiben.
      const userTags = (this.recipeDetail.data.tags || [])
        .filter(t => !t.auto)
        .map(t => t.name);
      if (userTags.includes(name)) { this.recipeDetail.newTag = ''; return; }
      userTags.push(name);
      const r = await this.api('PUT', `/api/recipes/${context.id}/tags`,
                                { tags: userTags });
      if (r && r.ok && this._detailOwns(context)) {
        // Re-fetch komplette Tag-Liste (User + Auto)
        const fresh = await this.api('GET', `/api/recipes/${context.id}`);
        if (fresh && this._detailOwns(context)) this.recipeDetail.data.tags = fresh.tags;
        this.recipeDetail.newTag = '';
        this.loadFacets();
      }
    },

    async removeTagFromRecipe(tagName) {
      if (!this.recipeDetail.data) return;
      const context = this._detailContext();
      // Auto-Tags lassen sich nicht entfernen (× ist im UI eh nicht da).
      // Defensiv: falls doch aufgerufen, hier abfangen.
      const tag = (this.recipeDetail.data.tags || []).find(t => t.name === tagName);
      if (!tag || tag.auto) return;
      const userTags = (this.recipeDetail.data.tags || [])
        .filter(t => !t.auto && t.name !== tagName)
        .map(t => t.name);
      const r = await this.api('PUT', `/api/recipes/${context.id}/tags`,
                                { tags: userTags });
      if (r && r.ok && this._detailOwns(context)) {
        const fresh = await this.api('GET', `/api/recipes/${context.id}`);
        if (fresh && this._detailOwns(context)) this.recipeDetail.data.tags = fresh.tags;
        this.loadFacets();
      }
    },

    async cookRecipe() {
      if (!this.recipeDetail.data || this.recipeDetail.cooking) return;
      this.recipeDetail.cooking = true;
      try {
        const mult = Number(this.recipeDetail.multiplier) || 1;
        const r = await this.api('POST', `/api/cart/cook/${this.recipeDetail.data.id}`,
                                  { multiplier: mult });
        if (r && r.ok) {
          const factor = mult !== 1 ? ` (× ${this._formatMultiplier(mult)})` : '';
          const msg = `+ ${r.added} neu, ${r.merged} summiert${factor}`;
          this.showToast('🛒 ' + msg);
          this.loadCart();
        }
      } finally {
        this.recipeDetail.cooking = false;
      }
    },

    _formatMultiplier(m) {
      // 0.5 → "0,5", 2 → "2", 1.5 → "1,5"
      if (Number.isInteger(m)) return String(m);
      return (Math.round(m * 100) / 100).toString().replace('.', ',');
    },

    safeExternalUrl(value) {
      try {
        const parsed = new URL(String(value || ''));
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
      } catch (_) {
        return null;
      }
    },

    async extractIngredients() {
      if (!this.canUseAdminTools()) return;
      if (!this.recipeDetail.data || this.recipeDetail.extracting) return;
      if (!this.confirmAIProcessing('Zutaten und Schritte ermitteln')) return;
      const context = this._detailContext();
      this.recipeDetail.extracting = true;
      try {
        const r = await this.api('POST', `/api/recipes/${context.id}/extract`, this.aiProcessingPayload());
        if (r && r.ok && this._detailOwns(context)) {
          this.showToast(`✓ ${r.count || 0} Zutaten extrahiert`);
          // Frisch laden um Zutatenliste im Modal zu aktualisieren
          const fresh = await this.api('GET', '/api/recipes/' + context.id);
          if (fresh && this._detailOwns(context)) this.recipeDetail.data = fresh;
          this.loadFacets();
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.extracting = false;
      }
    },

    // ── Zutaten-Edit-Mode ────────────────────────────────────────────
    // Kopiert die aktuellen Zutaten in einen lokalen Working-Buffer und
    // wechselt das UI in Edit-Mode. Save schickt PUT, Cancel discardet.
    async startEditIngredients() {
      const current = this.recipeDetail.data?.ingredients || [];
      // Deep-copy damit Edits nicht direkt durch Alpine in den View
      // durchschlagen (würde Cancel inkonsistent machen)
      this.recipeDetail.editIngs = current.map(i => ({
        name: i.name || '',
        amount: i.amount,
        unit: i.unit || '',
        raw: i.raw || null,
      }));
      this.recipeDetail.editingIngredients = true;
      // Bekannte Zutaten für Autocomplete (Dubletten-Vermeidung) —
      // einmal pro Session laden, danach aus dem Speicher.
      if (!this.knownIngredients.length) {
        const r = await this.api('GET', '/api/recipes/ingredients/known');
        if (r?.ingredients) this.knownIngredients = r.ingredients;
      }
      // datalist per DOM füllen — Alpine-x-for IN <datalist> rendert auf
      // iOS/Safari nicht zuverlässig. value = reiner Name (label mit Count
      // würde sonst als Wert übernommen).
      this.$nextTick(() => {
        const dl = document.getElementById('known-ingredients');
        if (!dl) return;
        const options = this.knownIngredients.map((ingredient) => {
          const option = document.createElement('option');
          option.value = String(ingredient.display_name || '');
          return option;
        });
        dl.replaceChildren(...options);
      });
    },

    // true wenn der Name (case-insensitive, getrimmt) in einer ANDEREN
    // Edit-Zeile schon vorkommt — markiert Dubletten direkt beim Tippen.
    isDuplicateIngredient(idx) {
      const c = this._canonicalLite(this.recipeDetail.editIngs[idx]?.name);
      if (!c) return false;
      return this.recipeDetail.editIngs.some((ing, i) =>
        i !== idx && this._canonicalLite(ing.name) === c);
    },

    // Schlanke Spiegelung von canonical_name (Server): lowercase, führende
    // Adjektive weg, einfache Plural-Heuristik mit Whitelist. Deckt
    // Zwiebel/Zwiebeln, Groß/Klein, "frische Tomaten"→tomate. Synonyme
    // (Bacon→Speck) macht weiterhin der Server beim Speichern.
    _canonicalLite(name) {
      if (!name) return '';
      let t = String(name).replace(/[^\wäöüÄÖÜß\s-]/g, ' ').trim().toLowerCase();
      if (!t) return '';
      const adj = new Set(['frische','frischer','frisches','frisch','große','großer','großes','groß',
        'klein','kleine','kleiner','kleines','reife','reifer','reifes','reif','getrocknete','getrockneter',
        'getrocknetes','getrocknet','rote','roter','rotes','rot','grüne','grüner','grünes','grün',
        'gelbe','gelber','gelbes','gelb','weiße','weißer','weißes','weiß','bio']);
      let parts = t.split(/\s+/);
      while (parts.length && adj.has(parts[0])) parts.shift();
      t = parts.join(' ');
      const wl = new Set(['tomate','zwiebel','kartoffel','karotte','möhre','paprika','gurke','aubergine',
        'champignon','ei','nudel','frikadelle','kichererbse','linse','bohne','erbse','minze','olive',
        'scheibe','zehe','tasse','dose']);
      if (t.endsWith('en') && t.length >= 5) {
        if (wl.has(t.slice(0,-1))) return t.slice(0,-1);
        if (wl.has(t.slice(0,-2))) return t.slice(0,-2);
      }
      if (t.endsWith('er') && t.length >= 4 && wl.has(t.slice(0,-2))) return t.slice(0,-2);
      if (t.endsWith('n') && t.length >= 4 && wl.has(t.slice(0,-1))) return t.slice(0,-1);
      return t;
    },

    // Globaler Abgleich: existiert die getippte Zutat schon im Katalog unter
    // ANDERER Schreibweise? Returnt den bekannten Anzeigenamen oder null.
    globalIngredientMatch(idx) {
      const typed = (this.recipeDetail.editIngs[idx]?.name || '').trim();
      if (!typed) return null;
      const c = this._canonicalLite(typed);
      if (!c) return null;
      const hit = this.knownIngredients.find(ki =>
        ki.canonical_name === c || this._canonicalLite(ki.display_name) === c);
      if (hit && hit.display_name && hit.display_name.toLowerCase() !== typed.toLowerCase()) {
        return hit.display_name;
      }
      return null;
    },

    adoptIngredientName(idx, name) {
      if (this.recipeDetail.editIngs[idx]) this.recipeDetail.editIngs[idx].name = name;
    },

    addIngredientRow() {
      this.recipeDetail.editIngs.push({
        name: '', amount: null, unit: '', raw: null,
      });
    },

    removeIngredientRow(idx) {
      this.recipeDetail.editIngs.splice(idx, 1);
    },

    cancelEditIngredients() {
      this.recipeDetail.editingIngredients = false;
      this.recipeDetail.editIngs = [];
    },

    async saveIngredients() {
      if (this.recipeDetail.savingIngredients) return;
      const context = this._detailContext();
      if (!context.id) return;
      // aber wir filtern hier schon damit der Toast-Count stimmt.
      const cleaned = this.recipeDetail.editIngs
        .filter(i => (i.name || '').trim())
        .map(i => ({
          name: i.name.trim(),
          amount: (i.amount === '' || i.amount == null) ? null : Number(i.amount),
          unit: (i.unit || '').trim() || null,
          raw: i.raw || null,
        }));
      this.recipeDetail.savingIngredients = true;
      try {
        const r = await this.api('PUT', `/api/recipes/${context.id}/ingredients`,
                                  { ingredients: cleaned });
        if (r && r.ok && this._detailOwns(context)) {
          // Server returnt die kanonisch verarbeitete Liste — UI darauf
          // aktualisieren statt den lokalen Working-Buffer zu nutzen
          this.recipeDetail.data.ingredients = r.ingredients;
          // Tags könnten sich geändert haben (Diät-Tags Recompute) — frisch laden
          const fresh = await this.api('GET', '/api/recipes/' + context.id);
          if (fresh && this._detailOwns(context)) this.recipeDetail.data = fresh;
          this.recipeDetail.editingIngredients = false;
          this.recipeDetail.editIngs = [];
          this.showToast(`✓ ${cleaned.length} Zutaten gespeichert`);
          this.loadFacets();
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.savingIngredients = false;
      }
    },

    // Nährwerte für das aktuelle Rezept berechnen (KI-Single-Call).
    // ⚡ Button im Detail-Modal — funktioniert sowohl für Erst-Berechnung
    // als auch für Recompute (z.B. nach manuellem Zutaten-Edit).
    async computeNutrition() {
      if (!this.canUseAdminTools()) return;
      const context = this._detailContext();
      if (!context.id || this.recipeDetail.computingNutrition) return;
      const ingCount = this.recipeDetail.data?.ingredients?.length || 0;
      if (ingCount < 3) {
        this.showToast('Mindestens 3 Zutaten nötig', 'err');
        return;
      }
      if (!this.confirmAIProcessing('Nährwerte schätzen', 'Die Zutaten mit Mengen und die Portionszahl dieses Rezepts')) return;
      this.recipeDetail.computingNutrition = true;
      try {
        const r = await this.api('POST', `/api/recipes/${context.id}/nutrition`, this.aiProcessingPayload());
        if (r && r.ok && this._detailOwns(context)) {
          // In-place die data-Felder updaten damit Modal sofort die Werte zeigt
          this.recipeDetail.data.calories_per_serving = r.calories;
          this.recipeDetail.data.protein_g = r.protein_g;
          this.recipeDetail.data.carbs_g = r.carbs_g;
          this.recipeDetail.data.fat_g = r.fat_g;
          this.showToast(`✓ ~${r.calories} kcal/Portion`);
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.computingNutrition = false;
      }
    },

    // Signierten Share-Link erstellen + in Clipboard kopieren.
    // 30 Tage gültig. Empfänger braucht keinen Login, sieht nur das Rezept.
    async copyRecipeShareLink() {
      const id = this.recipeDetail.data?.id;
      if (!id || this.recipeDetail.sharing) return;
      this.recipeDetail.sharing = true;
      try {
        const r = await this.api('POST', `/api/recipes/${id}/share`,
                                  { expires_days: 30 });
        if (!r || !r.url) return;
        // Clipboard-API kann fehlschlagen (kein HTTPS, kein User-Gesture etc).
        // Fallback: prompt() damit User manuell kopieren kann.
        try {
          await navigator.clipboard.writeText(r.url);
          this.showToast(`✓ Link kopiert (${r.expires_days}d gültig)`);
        } catch (e) {
          // eslint-disable-next-line no-alert
          window.prompt('Share-Link (Strg+C zum Kopieren):', r.url);
        }
      } finally {
        this.recipeDetail.sharing = false;
      }
    },

    // 'Manuell geprüft, ok'-Toggle. Verifizierte Rezepte verschwinden aus
    // den Audit-Daten-Lücken (kein Bild / wenige Zutaten / etc). Audit-Trail:
    // Username + Timestamp werden mitgespeichert. Unchecken setzt beides
    // zurück auf NULL.
    recipePdfFilename(recipe) {
      const slug = String(recipe?.name || `rezept-${recipe?.id || ''}`)
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '')
        .slice(0, 60);
      return `${slug || `rezept-${recipe?.id || 'export'}`}.pdf`;
    },

    startEditSteps() {
      const current = this.recipeDetail.data?.steps || [];
      this.recipeDetail.editSteps = current.map(step => ({
        instruction: step.instruction || '',
        timer_seconds: step.timer_seconds ?? null,
      }));
      if (!this.recipeDetail.editSteps.length) {
        this.recipeDetail.editSteps.push({ instruction: '', timer_seconds: null });
      }
      this.recipeDetail.editingSteps = true;
    },

    addStepRow() {
      this.recipeDetail.editSteps.push({ instruction: '', timer_seconds: null });
    },

    removeStepRow(idx) {
      this.recipeDetail.editSteps.splice(idx, 1);
    },

    cancelEditSteps() {
      this.recipeDetail.editingSteps = false;
      this.recipeDetail.editSteps = [];
    },

    async saveSteps() {
      if (this.recipeDetail.savingSteps) return;
      const context = this._detailContext();
      if (!context.id) return;
      const steps = this.recipeDetail.editSteps
        .filter(step => (step.instruction || '').trim())
        .map(step => ({
          instruction: step.instruction.trim(),
          timer_seconds: step.timer_seconds === '' || step.timer_seconds == null
            ? null
            : Math.max(0, Math.round(Number(step.timer_seconds))),
        }));
      this.recipeDetail.savingSteps = true;
      try {
        const result = await this.api('PUT', `/api/recipes/${context.id}/steps`, { steps });
        if (result?.ok && this._detailOwns(context)) {
          const fresh = await this.api('GET', `/api/recipes/${context.id}`);
          if (fresh && this._detailOwns(context)) this.recipeDetail.data = fresh;
          this.recipeDetail.editingSteps = false;
          this.recipeDetail.editSteps = [];
          this.showToast(`✓ ${steps.length} Schritte gespeichert`);
          await this.loadRecipes();
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.savingSteps = false;
      }
    },

    async fetchPdf(url) {
      return this.fetchWithTimeout(url, {
        credentials: 'same-origin',
        cache: 'no-store',
      }, 60000, async response => {
        if (response.status === 401) {
          window.location = '/login';
          return null;
        }
        if (!response.ok) {
          let detail = `PDF konnte nicht erstellt werden (${response.status})`;
          try {
            const body = await response.json();
            detail = this.apiErrorMessage(body.detail, detail);
          } catch (error) { if (error?.name === 'AbortError') throw error; }
          throw new Error(detail);
        }
        return response.blob();
      });
    },

    savePdf(blob, filename) {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },

    async sharePdf({ blob, filename, title, text, mailSubject, mailBody }) {
      const file = new File([blob], filename, { type: 'application/pdf' });
      const shareData = { title, text, files: [file] };
      let canShareFile = false;
      try {
        canShareFile = !!navigator.share && (
          !navigator.canShare || navigator.canShare(shareData)
        );
      } catch (_) {}

      if (canShareFile) {
        try {
          await navigator.share(shareData);
          return 'shared';
        } catch (error) {
          if (error?.name === 'AbortError') return 'cancelled';
          // Safari kann nach einem längeren PDF-Fetch die User-Activation
          // verlieren. In diesem Fall muss der Download/Mail-Fallback greifen.
        }
      }

      this.savePdf(blob, filename);
      const subject = encodeURIComponent(mailSubject || title);
      const body = encodeURIComponent(mailBody || text);
      window.location.href = `mailto:?subject=${subject}&body=${body}`;
      this.showToast('PDF geladen - bitte im Mail-Entwurf anhängen');
      return 'fallback';
    },

    async downloadRecipePdf() {
      const recipe = this.recipeDetail.data;
      if (!recipe?.id || this.recipeDetail.pdfBusy) return;
      this.recipeDetail.pdfBusy = true;
      try {
        const blob = await this.fetchPdf(`/recipe/${recipe.id}/pdf`);
        if (!blob) return;
        this.savePdf(blob, this.recipePdfFilename(recipe));
        this.showToast('Rezept-PDF heruntergeladen');
      } catch (error) {
        this.showToast(error?.message || 'PDF-Erstellung fehlgeschlagen', 'error');
      } finally {
        this.recipeDetail.pdfBusy = false;
      }
    },

    async shareRecipePdf(recipe = this.recipeDetail.data) {
      if (!recipe?.id || this.recipeDetail.pdfBusy) return;
      this.recipeDetail.pdfBusy = true;
      try {
        const blob = await this.fetchPdf(`/recipe/${recipe.id}/pdf`);
        if (!blob) return;
        await this.sharePdf({
          blob,
          filename: this.recipePdfFilename(recipe),
          title: recipe.name,
          text: `Rezept: ${recipe.name}`,
          mailSubject: `Rezept: ${recipe.name}`,
          mailBody: `Hallo,\n\nanbei das Rezept „${recipe.name}“.\n\n`
            + 'Das PDF wurde bereits heruntergeladen und kann an diese Mail angehängt werden.',
        });
      } catch (error) {
        if (error?.name !== 'AbortError') {
          this.showToast(error?.message || 'Teilen fehlgeschlagen', 'error');
        }
      } finally {
        this.recipeDetail.pdfBusy = false;
      }
    },

    async toggleVerified(verified) {
      const context = this._detailContext();
      if (!context.id || this.recipeDetail.verifying) return;
      this.recipeDetail.verifying = true;
      try {
        const r = await this.api('POST',
          `/api/recipes/${context.id}/verify?verified=${verified ? 'true' : 'false'}`);
        if (r && r.ok && this._detailOwns(context)) {
          // In-place updaten damit UI sofort den Username + Timestamp zeigt
          this.recipeDetail.data.user_verified = verified ? 1 : 0;
          this.recipeDetail.data.verified_by = verified ? r.by : null;
          this.recipeDetail.data.verified_at = verified ? (Date.now() / 1000) : null;
          this.showToast(verified ? '✓ Als geprüft markiert' : '⊘ Verifikation entfernt');
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.verifying = false;
      }
    },

    // Re-Scrape aus dem Detail-Modal — gleicher Endpoint wie aus Audit
    async rescrapeFromDetailModal() {
      if (!this.canUseAdminTools()) return;
      const context = this._detailContext();
      if (!context.id || this.recipeDetail.rescraping) return;
      if (!this.confirmAIProcessing('Quelle neu abrufen und analysieren', 'Neu geladene Rezepttexte und Medien der Quelle', 'Bei geänderten Inhalten werden Zutaten und Schritte erneut ermittelt.')) return;
      this.recipeDetail.rescraping = true;
      try {
        // Bei leeren Rezepten muss auch bei unveränderter Caption die aktuelle
        // Zutatenanalyse erneut eingeplant werden.
        const needsReanalysis = !(this.recipeDetail.data?.ingredients?.length);
        const endpoint = `/api/recipes/${context.id}/rescrape${needsReanalysis ? '?reanalyze=true' : ''}`;
        const r = await this.api('POST', endpoint, this.aiProcessingPayload());
        if (r && r.ok && this._detailOwns(context)) {
          if (r.any_change || r.ingredients_queued) {
            const parts = [];
            if (r.description_updated) parts.push('Beschreibung');
            if (r.thumbnail_updated) parts.push('Bild');
            if (r.ingredients_queued && !parts.length) parts.push('Zutatenanalyse');
            this.showToast(
              `✓ ${parts.join(' + ')} aktualisiert${r.ingredients_queued ? ' · Zutatenanalyse gestartet' : ''}`
            );
            // Re-Fetch damit das neue Thumb + Description sichtbar werden
            const fresh = await this.api('GET', '/api/recipes/' + context.id);
            if (fresh && this._detailOwns(context)) this.recipeDetail.data = fresh;
          } else {
            this.showToast('⊘ Schon aktuell — keine Änderung');
          }
        } else if (r) {
          this.showToast('Re-Scrape: ' + (r.error || 'fehler'), 'err');
        }
      } finally {
        if (this._detailOwns(context)) this.recipeDetail.rescraping = false;
      }
    },

    // ─── Favorit + Bewertung + Share ────────────────────────────────────
    async toggleFavorite(recipeId) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/favorite`);
      if (r?.ok) {
        const item = this.recipes.items.find(i => i.id === recipeId);
        if (item) { item.is_favorite = r.is_favorite; item.in_library = true; }
        if (this.recipeDetail?.data?.id === recipeId) {
          this.recipeDetail.data.is_favorite = r.is_favorite;
          this.recipeDetail.data.in_library = true;
        }
        this.showToast(r.is_favorite ? '⭐ Favorit gesetzt' : 'Favorit entfernt');
      }
    },
    async setRating(recipeId, value) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/rating?value=${value}`);
      if (r?.ok) {
        const item = this.recipes.items.find(i => i.id === recipeId);
        if (item) { item.rating = r.rating; item.in_library = true; }
        if (this.recipeDetail?.data?.id === recipeId) {
          this.recipeDetail.data.rating = r.rating;
          this.recipeDetail.data.in_library = true;
        }
        this.showToast(value === 0 ? 'Bewertung entfernt' : `${'★'.repeat(value)} Bewertet`);
      }
    },

    // ─── Infinite-Scroll: lade nächste Seite an aktuelle items ─────────
    async loadMoreRecipes({ retry = false } = {}) {
      if (this.recipes.loading || this.recipes.loadingMore || this.recipes.error
          || (this.recipes.moreError && !retry) || this.recipes.items.length >= this.recipes.total) return;
      this.recipes._moreController?.abort();
      const controller = new AbortController();
      this.recipes._moreController = controller;
      const sequence = this.recipes._loadSequence;
      const nextOffset = this.recipes.items.length;
      this.recipes.loadingMore = true;
      this.recipes.moreError = '';
      try {
        const query = this._buildRecipeQuery().replace(/offset=\d+/, 'offset=' + nextOffset);
        const r = await this.api(
          'GET', '/api/recipes?' + query, undefined,
          { signal: controller.signal, silent: true }
        );
        if (r?.items && sequence === this.recipes._loadSequence && !controller.signal.aborted) {
          const existingIds = new Set(this.recipes.items.map(item => item.id));
          this.recipes.items.push(...r.items.filter(item => !existingIds.has(item.id)));
          this.recipes.total = r.total;
        }
      } catch (error) {
        if (!controller.signal.aborted && sequence === this.recipes._loadSequence
            && this.recipes._moreController === controller) {
          this.recipes.moreError = error?.detail || 'Weitere Rezepte konnten nicht geladen werden.';
        }
      } finally {
        if (this.recipes._moreController === controller) {
          this.recipes.loadingMore = false;
          this.recipes._moreController = null;
        }
      }
    },
    initScrollObserver() {
      if (this._scrollObserver) return;
      const sentinel = this.$refs.scrollSentinel;
      if (!sentinel) return;
      this._scrollObserver = new IntersectionObserver(entries => {
        if (entries[0]?.isIntersecting) {
          this.loadMoreRecipes();
        }
      }, { rootMargin: '200px' });  // 200px vor Sichtbarkeit triggern
      this._scrollObserver.observe(sentinel);
    },

    // Eigenes Bild hochladen — fallback wenn Frame-Extract+Re-Scrape nichts taugen.
    // Akzeptiert JPEG/PNG/WebP, max 10MB.
    async uploadThumbnail(recipeId, file) {
      console.log('[uploadThumbnail] called', recipeId, file);
      if (!file) { console.log('[uploadThumbnail] no file selected'); return; }
      if (file.size > 10 * 1024 * 1024) {
        this.showToast('Datei zu groß (max 10MB)', 'err'); return;
      }
      const context = this._detailContext();
      this.showToast('Lade hoch…');
      const fd = new FormData();
      fd.append('file', file);
      try {
        const { response: resp, result: r } = await this.fetchWithTimeout(`/api/recipes/${recipeId}/upload-thumbnail`, {
          method: 'POST',
          body: fd,
          credentials: 'same-origin',
        }, 60000, async response => {
          let result = null;
          if (response.status !== 401) {
            try { result = await response.json(); }
            catch (error) {
              if (error?.name === 'AbortError' || response.ok) throw error;
              result = { detail: response.statusText };
            }
          }
          return { response, result };
        });
        console.log('[uploadThumbnail] response status', resp.status);
        if (resp.status === 401) { window.location = '/login'; return; }
        if (!resp.ok) {
          this.showToast('Upload: ' + (r?.detail || 'Fehler ' + resp.status), 'err');
          return;
        }
        console.log('[uploadThumbnail] response body', r);
        if (r?.ok) {
          this.invalidateRecipeThumbnail(recipeId);
          this.showToast(`✓ Bild gesetzt (${(r.size_bytes/1024).toFixed(0)} KB)`);
          if (context.id === recipeId && this._detailOwns(context)) {
            this.recipeDetail.data.thumb_filename = r.thumbnail;
          }
          if (typeof this.loadAudit === 'function') await this.loadAudit();
          if (typeof this.loadRecipes === 'function') await this.loadRecipes();
        } else {
          this.showToast('Upload: unerwartete Server-Antwort', 'err');
        }
      } catch (e) {
        console.error('[uploadThumbnail] fail', e);
        this.showToast('Upload fehlgeschlagen: ' + e.message, 'err');
      }
    },

    // Aus dem Detail-Modal heraus löschen
    async deleteRecipeFromDetail() {
      const data = this.recipeDetail?.data;
      if (!data?.id) return;
      const context = this._detailContext();
      const choice = confirm(
        `Rezept „${data.name}“ in den Papierkorb verschieben?\n\n` +
        `Rezept und Dateien bleiben dort wiederherstellbar. Nach 30 Tagen werden sie endgültig gelöscht.`
      );
      if (!choice) return;
      const r = await this.api('DELETE',
        `/api/recipes/${data.id}?delete_files=true`);
      if (r?.ok) {
        this.showToast(`✓ "${data.name}" gelöscht`);
        if (this._detailOwns(context)) this.closeRecipeDetail();
        // Listen aktualisieren falls offen
        if (typeof this.loadAudit === 'function') await this.loadAudit();
        if (typeof this.loadRecipes === 'function') await this.loadRecipes();
      }
    },
    };
  };
})();
