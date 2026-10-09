// Scrapper Manager - Frontend Logic
function scrapperApp() {
  return {
    ...window.RezepteFeatures["recipes"](),
    ...window.RezepteFeatures["cart"](),
    ...window.RezepteFeatures["meal-plan"](),
    ...window.RezepteFeatures["admin"](),
    ...window.RezepteFeatures["imports"](),
    ...window.RezepteFeatures["settings"](),
    ...window.RezepteFeatures["audit"](),
    ...window.RezepteFeatures["account"](),
    page: 'recipes',
    session: { username: '', role: 'user', is_admin: false, full_access: false, can_import: false, loaded: false },
    account: { imports: [], data: null, profile: null, sessions: [], identities: [], providers: [], loading: false, error: '', notice: '', busy: false, currentPassword: '', newPassword: '', confirmPassword: '', deletePassword: '', invitation: null, joinToken: '', _loadGeneration: 0, _loadController: null },
    users: { items: [], loading: false, busy: false, error: '', notice: '', search: '', draft: null, _loadGeneration: 0 },
    systemInfo: { version: '', capabilities: [], loaded: false, backendOutdated: false },
    admin: {
      tab: 'home',
      overview: null,
      importCenter: null,
      versions: [],
      versionsLoading: false,
      versionDetail: null,
      versionRecipeId: '',
      synonyms: [],
      synonymForm: { term: '', synonymsText: '' },
      pdf: {
        running: false, result: null, recipe_id: '', process_all: true,
        jobId: null, preflight: null, pollTimer: null, legacyMode: false,
        auto_rotate: true, remove_blank_pages: true, auto_crop: true,
        deskew_scans: true, ocr_scans: true, improve_contrast: true,
        sharpen_scans: true, scan_dpi: 300,
        ocr_language: 'deu+eng', keep_original: true, limit: 500,
        extract_recipe_data: true, overwrite_recipe_data: false,
      },
      pageEditor: {
        loading: false, saving: false, filename: '', pages: [],
        loadedRecipeId: null, requestEpoch: 0, previewKey: Date.now(),
      },
      maintenanceRuns: [],
      maintenanceBusy: '',
      maintenanceResult: null,
      imageBackfill: null,
      accessDenied: false,
    },

    // Zentrale Navigations-Helper — page-switch plus die zugehörigen
    // Loader. Vorher waren die Loader direkt im @click jeder nav-item,
    // was beim Refactor (z.B. neue Bottom-Sheet) duplication erzeugte.
    navTo(targetPage, { updateUrl = true, preserveRecipeFilters = false } = {}) {
      // Alte Favoriten-Deep-Links bleiben kompatibel, landen aber in der
      // Rezeptbibliothek mit aktivem Favoritenfilter statt auf einer Extra-Seite.
      if (targetPage === 'favorites') {
        targetPage = 'recipes';
        preserveRecipeFilters = true;
        this.recipes.filters.favorite_only = true;
      }
      const allowed = new Set(['recipes','plan','cart','admin','account']);
      if (!allowed.has(targetPage)) targetPage = 'recipes';
      if (targetPage === 'admin' && !this.session.is_admin) {
        targetPage = 'recipes';
        if (this.session.loaded) this.showToast('Administratorrechte erforderlich', 'err');
      }
      this.page = targetPage;
      if (targetPage !== 'account') this.clearAccountPasswords();
      if (targetPage !== 'admin') this.users.draft = null;
      if (this.browser?.show) this.browser.show = false;
      if (this.recipeDetail?.show) this.closeRecipeDetail();

      if (targetPage === 'recipes' && !preserveRecipeFilters) {
        this.recipes.filters.favorite_only = false;
      }

      if (targetPage === 'admin') this._startAdminRuntime();
      else this._stopAdminRuntime();

      switch (targetPage) {
        case 'admin':   this.selectAdminTab(this.admin.tab || 'home', { updateUrl: false }); break;
        case 'recipes': this.recipes.filters.offset = 0; this.loadRecipes(); break;
        case 'plan':    this.loadMealPlan(); break;
        case 'cart':    this.loadCart(); break;
        case 'account': this.loadAccount(); break;
      }

      if (updateUrl && window.history?.replaceState) {
        const target = targetPage === 'admin'
          ? `/admin${this.admin.tab === 'pdf' ? '/pdf' : ''}`
          : targetPage === 'account' ? '/account' : `/?tab=${encodeURIComponent(targetPage)}`;
        window.history.replaceState({}, '', target);
      }
    },
    pageLabel() {
      return ({
        recipes: 'Alle Rezepte', plan: 'Wochenplan', cart: 'Einkaufsliste', admin: 'Admin', account: 'Konto',
      })[this.page] || 'Rezepte';
    },
    config: {},
    hddStatus: null,
    hddBusy: false,
    hddLastOutput: '',
    filterBusy: false,
    maintenance: null,
    maintBusy: false,
    maintenanceOutput: '',
    pending: [],
    history: [],
    historyReanalyzing: false,
    historyReanalyzeStatus: null,
    historyReanalyzePollTimer: null,
    reanalyzingHistoryUrl: null,
    historyAutoMove: false,
    junkItems: null,
    junkLoading: false,
    jobs: [],
    status: { scraper: null, pending_count: 0 },
    lastScraper: null,
    stats: null,
    statsLoading: false,
    currentLog: '',
    currentLogJob: null,
    toast: { show: false, message: '', type: 'ok' },
    recipeTypes: ['Hauptgericht','Vorspeise','Nachspeise','Snack','Frühstück','Getränk','Beilage'],
    weddingCategories: ['Deko','Foto','Basteln','Einladung','Standesamt','Outfit','Catering','Sonstiges'],
    _statusTimer: null,
    recipeImageRevisions: {},
    _imageRevisionSequence: 0,

    // ── Recipe-Browser + Einkaufskorb (feat/recipe-browser-and-cart) ─────
    recipes: {
      items: [], total: 0, loading: false, loadingMore: false, error: '', moreError: '',
      filters: {
        search: '', type: '', category: '', tag_ids: [], library: 'all',
        ingredients: [], excludedIngredients: [],
        ingredients_status: '', verified: '', favorite_only: false,
        min_rating: 0, limit: 60, offset: 0,
      },
      searchMeta: { corrected: false, original: '', query: '', suggestion: '' },
      filterDrawerOpen: false,  // nur auf Mobile sichtbar: Filter als Drawer statt Sidebar
      ingredientSearch: '',     // Such-Input im Zutaten-Filter-Block
      _ingFacetsLimited: false, // True wenn die Chip-Liste auf MAX geclipped wurde
      facets: { types: [], categories: [], tags: [], ingredients: [] },
      extractionRunning: false, extractionPending: 0,
      extractionStats: {}, _pollTimer: null,
      sync: { running: false, queued: false, result: null, error: null, last_success_at: null },
      _listController: null, _moreController: null, _facetsController: null, _loadSequence: 0,
    },
    cart: {
      addBusy: false,
      items: [],
      add: { name: '', amount: null, unit: '', category: '' },
      suggestions: [],
      external: false,
      connectionError: '',
      list: [],
      total: 0,
      quickAdd: '',
      loading: false,
      _loadController: null, _loadSequence: 0,
      _suggestionsController: null, _suggestionsSequence: 0,
      tab: 'list',
      recurring: [],
      recLoading: false,
      recForm: {
        id: null,
        name: '',
        category: '',
        default_unit: '',
        interval_days: 7,
        active: true,
      },
      recSaving: false,
      recRunning: false,
      recDeleting: null,
    },
    mealPlan: {
      weekStart: '',
      weekEnd: '',
      previousWeek: '',
      nextWeek: '',
      isCurrentWeek: false,
      days: [],
      shoppingPreview: [],
      summary: { planned_meals: 0, planned_days: 0, shopping_items: 0 },
      loading: false,
      error: '',
      saving: false,
      pdfBusy: false,
      addingFor: '',
      recipeOptions: [],
      optionsLoaded: false,
      _optionsPromise: null,
      _loadController: null, _loadSequence: 0, requestedWeekStart: '',
      draft: { recipe_id: '', planned_servings: 2 },
    },
    trash: {
      items: [], totalCount: 0, loading: false, emptying: false,
    },
    audit: {
      data: {
        total_recipes: 0,
        exact_duplicates: [], url_duplicates: [], folder_duplicates: [], similar_clusters: [],
        bad_names: [], sync_errors: [], ai_category_findings: [], ai_name_findings: [],
        ai_folder_findings: [], ai_suggestions: [], empty_recipes: [], empty_rescrape_ids: [], failed_downloads: [],
        data_gaps: { no_image: [], no_steps: [], no_url: [], few_ingredients: [], no_description: [], unverified: [], fs_missing: [], no_nutrition: [] },
      },
      summary: {
        exact_count: 0, exact_groups: 0, url_count: 0, folder_count: 0,
        similar_count: 0, similar_clusters: 0, bad_count: 0, sync_error_count: 0,
        ai_category_count: 0, ai_name_count: 0, ai_folder_count: 0,
        empty_recipe_count: 0, empty_rescrape_count: 0, failed_download_count: 0, with_ai_suggestions: 0,
        no_image_count: 0, no_steps_count: 0, no_url_count: 0,
        few_ingredients_count: 0, no_description_count: 0, unverified_count: 0,
        fs_missing_count: 0, no_nutrition_count: 0,
      },
      loading: false,
      withAi: false,
      // KI-Sanity-Background-Job: Progress beim Polling
      aiSanity: { running: false, processed: 0, total: 0, findings: 0, pollHandle: null },
      bulkApplying: false,    // Loading-state für apply-all
      activeTab: 'gaps',      // Audit-Tab-State: gaps / fs / ai / duplicates
      computingNutritionBulk: false,  // Loading für Bulk-Nährwerte
      healingFs: false,                // Loading für FS-Path-Auto-Heal
      verifyingBulk: false,            // Loading für Bulk-Verify
      rescrapingId: null,               // ID des Rezepts das gerade re-scraped wird
      rescrapingBulk: false,            // Loading für Bulk-Re-Scrape
      rescrapeProgress: 0,              // Counter für Bulk-Re-Scrape-UI
      rescrapeTotal: 0,
      extractingId: null,               // ID des Rezepts das gerade Frame-extract macht
      extractingBulk: false,            // Bulk-Frame-Extract läuft
      extractProgress: 0,
      extractTotal: 0,
      deletingUnresolvable: false,      // Loading für Bulk-Delete-toter-Einträge
    },
    // Stammdaten-Page: Tags + canonical Zutaten-Namen-Verwaltung
    master: {
      tab: 'tags',
      tags: [],
      canonicals: [],
      canLoaded: false,     // canonicals lazy, erst beim Tab-Switch geladen
      tagFilter: '',
      canFilter: '',
      loading: false,
    },
    // FS-Konflikt-Vergleichs-Modal: zeigt DB-Rezept und nicht-indexierten
    // FS-Folder Side-by-Side, damit User entscheidet welcher behalten wird.
    fsCompare: {
      show: false,
      syncError: null,    // Original-sync_errors row
      dbRecipe: null,     // Vom GET /api/recipes/{id} (Konflikt-Partner)
      fsPreview: null,    // Vom GET /api/audit/folder-preview (Konflikt-Folder)
      loadingDb: false,
      loadingFs: false,
      actionBusy: false,
      requestEpoch: 0,
      controller: null,
    },
    knownIngredients: [],   // distinct Zutaten-Namen für Edit-Autocomplete
    recipeDetail: {
      loading: false, error: '',
      show: false, data: null, newTag: '',
      cooking: false, extracting: false,
      imageGenerating: false, imageRestoring: null,
      multiplier: 1,    // Portionen-Skalierung beim Kochen
      cookMode: false,  // Koch-Modus: nur Schritte, große Schrift, Wake-Lock
      tab: 'info',      // Detail-Reiter: info | zutaten | steps (kompaktes Mobile-Layout)
      wakeLockActive: false,  // UI-Indikator ob Wake-Lock greift
      // Zutaten-Edit-Modus: lokaler working-copy bis 'Speichern' geklickt wird
      editingIngredients: false,
      editIngs: [],
      savingIngredients: false,
      editingSteps: false,
      editSteps: [],
      savingSteps: false,
      savingLibrary: false,
      pdfBusy: false,
      computingNutrition: false,    // Loading-state für ⚡ Berechnen-Button
      sharing: false,                // Loading-state für 🔗 Share-Button
      verifying: false,              // Loading für 'manuell geprüft'-Toggle
      rescraping: false,             // Loading für Re-Scrape im Modal
      imageGenerating: false,
      imageRestoring: null,
      activeId: null,
      requestEpoch: 0,
      controller: null,
    },
    _wakeLock: null,
    _wakeLockEpoch: 0,
    // Per-Schritt-Timer (key = step.id, value = {status, remaining, intervalId})
    // Bewusst auf scrapperApp-Top-Level damit Alpine reactivity trackt.
    timers: {},
    _audioCtx: null,
    init() {
      const params = new URLSearchParams(window.location.search);
      const isAdminRoute = window.location.pathname.startsWith('/admin');
      const routePage = document.body?.dataset?.initialPage || 'recipes';
      const requestedPage = isAdminRoute ? 'admin' : (params.get('tab') || routePage);
      const legacyFavorites = requestedPage === 'favorites';
      const validTab = ['recipes','plan','cart','admin','account'].includes(requestedPage) ? requestedPage : 'recipes';
      this.page = validTab;
      if (validTab === 'account') this.account.joinToken = params.get('invite') || '';
      if (legacyFavorites) this.recipes.filters.favorite_only = true;
      if (validTab === 'admin') {
        const routeAdminTab = window.location.pathname === '/admin/pdf' ? 'pdf' : '';
        const requestedAdmin = routeAdminTab ||
          params.get('section') ||
          params.get('admin') ||
          document.body?.dataset?.initialAdminTab;
        if (requestedAdmin) this.admin.tab = requestedAdmin;
      }

      this.loadSystemInfo();
      this.loadSession();
      this.navTo(this.page, { updateUrl: false, preserveRecipeFilters: legacyFavorites });
      this.$nextTick(() => window.RezepteRuntime?.initAccessibleDialogs());
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) this._pauseBackgroundWork();
        else {
          this._resumeBackgroundWork();
          if (
            this.recipeDetail.show
            && this.recipeDetail.cookMode
            && !this._wakeLock
          ) {
            this._acquireWakeLock();
          }
        }
      });
      window.addEventListener('popstate', () => {
        const params = new URLSearchParams(window.location.search);
        const next = window.location.pathname.startsWith('/admin')
          ? 'admin' : (params.get('tab') || (window.location.pathname === '/account' ? 'account' : 'recipes'));
        if (next === 'account') this.account.joinToken = params.get('invite') || '';
        if (next === 'admin') {
          const section = window.location.pathname === '/admin/pdf' ? 'pdf' : params.get('section');
          if (section) this.admin.tab = section;
        }
        this.navTo(next, { updateUrl: false });
      });
      this.$nextTick(() => this.initPullToRefresh());
    },

    async loadSystemInfo() {
      try {
        const info = await this.api('GET', '/api/system/info', undefined, { silent: true });
        this.systemInfo = { ...(info || {}), loaded: true, backendOutdated: false };
      } catch (e) {
        // Ein 404 bedeutet fast immer: neue statische Dateien, aber alter, noch
        // nicht neu gestarteter Python-Prozess. Das wird im PDF-Reiter konkret
        // behandelt und blockiert die normale Rezeptnutzung nicht.
        this.systemInfo = { version: '', capabilities: [], loaded: true, backendOutdated: e?.status === 404 };
      }
    },

    async loadSession() {
      try {
        const r = await this.api('GET', '/api/session');
        this.session = {
          ...this.session,
          ...(r || {}),
          is_admin: r?.is_admin === true || r?.role === 'admin',
          full_access: r?.full_access === true,
          can_import: ['full_user', 'admin'].includes(r?.role),
          loaded: true,
        };
        this.admin.accessDenied = !this.session.is_admin;
        if (this.session.is_admin && window.location.pathname.startsWith('/admin')) {
          this.navTo('admin', { updateUrl: false });
        } else if (!this.session.is_admin && this.page === 'admin') {
          this.navTo('recipes', { updateUrl: false });
        }
      } catch (_) {
        this.session = {
          ...this.session,
          role: 'unknown',
          is_admin: false,
          full_access: false,
          can_import: false,
          loaded: false,
        };
        this.admin.accessDenied = true;
        if (this.page === 'admin') {
          this.navTo('recipes', { updateUrl: false });
          this.showToast('Sitzung konnte nicht geladen werden', 'err');
        }
      }
    },

    _startEventStream() {
      if (!this.session.loaded || this.session.full_access !== true || this._eventSource) return;
      try {
        const es = new EventSource('/api/events');
        this._eventSource = es;
        es.addEventListener('status', (e) => {
          try { this.status = JSON.parse(e.data); } catch(_) {}
        });
        let errors = 0;
        es.addEventListener('error', () => {
          errors++;
          // Nach 3 fehlgeschlagenen Reconnects → fallback Polling.
          if (errors >= 3) {
            console.warn('SSE-Stream nicht stabil, fallback auf Polling');
            es.close();
            this._eventSource = null;
            this._startPollingFallback();
          }
        });
      } catch (e) {
        console.warn('EventSource not supported, falling back to polling', e);
        this._startPollingFallback();
      }
    },
    _startPollingFallback() {
      if (!this.session.loaded || this.session.full_access !== true || this.page !== 'admin' || document.hidden) return;
      if (!this._statusPoller) {
        this._statusPoller = window.RezepteRuntime.createPoller(
          async () => { const before = JSON.stringify(this.status); await this.refreshStatus(); return before !== JSON.stringify(this.status); },
          { minDelay: 3000, maxDelay: 20000, isActive: () => this.page === 'admin' && !document.hidden }
        );
      }
      if (!this._progressPoller) {
        this._progressPoller = window.RezepteRuntime.createPoller(
          async () => { const before = JSON.stringify(this.reanalyzeProgress); await this.refreshProgress(); return before !== JSON.stringify(this.reanalyzeProgress); },
          { minDelay: 2500, maxDelay: 15000, isActive: () => this.page === 'admin' && !document.hidden }
        );
      }
      this._statusPoller.start();
      this._progressPoller.start();
    },

    _adminRuntimeActive: false,
    _startAdminRuntime() {
      if (this._adminRuntimeActive || document.hidden || this.page !== 'admin') return;
      this._adminRuntimeActive = true;
      this.loadRecentJobs();
      this.loadStats();
      this.loadHddStatus();
      if (!this._jobsPoller) {
        this._jobsPoller = window.RezepteRuntime.createPoller(
          () => this.loadRecentJobs(),
          { minDelay: 15000, maxDelay: 60000, isActive: () => this.page === 'admin' && !document.hidden }
        );
        this._statsPoller = window.RezepteRuntime.createPoller(
          () => this.loadStats(),
          { minDelay: 60000, maxDelay: 180000, isActive: () => this.page === 'admin' && !document.hidden }
        );
        this._hddPoller = window.RezepteRuntime.createPoller(
          () => this.loadHddStatus(),
          { minDelay: 30000, maxDelay: 120000, isActive: () => this.page === 'admin' && !document.hidden }
        );
      }
      this._jobsPoller.start({ immediate: false });
      this._statsPoller.start({ immediate: false });
      this._hddPoller.start({ immediate: false });
      this._startEventStream();
    },
    _stopAdminRuntime() {
      this._adminRuntimeActive = false;
      for (const p of ['_jobsPoller','_statsPoller','_hddPoller','_statusPoller','_progressPoller']) {
        this[p]?.stop();
      }
      if (this._eventSource) { this._eventSource.close(); this._eventSource = null; }
    },

    // App im Hintergrund (PWA minimiert / Tab inaktiv): Netzwerkaktivität
    // pausieren. Beim Zurückkehren startet nur der aktuell sichtbare Bereich.
    _bgPaused: false,
    _pauseBackgroundWork() {
      if (this._bgPaused) return;
      this._bgPaused = true;
      this._stopAdminRuntime();
      this.recipes._listController?.abort();
      this.recipes._facetsController?.abort();
    },
    _resumeBackgroundWork() {
      if (!this._bgPaused) return;
      this._bgPaused = false;
      if (this.page === 'admin') this._startAdminRuntime();
      if (this.page === 'recipes') {
        this.loadRecipes();
        if (this.recipes.sync.running) this._scheduleSyncPoll();
        if (this.recipes.extractionRunning) this._scheduleExtractionPoll();
      }
    },

    // ------------- Helpers -------------
    apiErrorMessage(detail, fallback = 'Unbekannter Fehler') {
      if (Array.isArray(detail)) {
        const messages = detail
          .map(item => item?.msg || item?.message || '')
          .filter(Boolean);
        return messages.length ? messages.join('; ') : fallback;
      }
      if (detail && typeof detail === 'object') {
        return detail.message || detail.msg || fallback;
      }
      return String(detail || fallback);
    },

    async fetchWithTimeout(url, options = {}, timeoutMs = 30000, readResponse = null) {
      const controller = new AbortController();
      const external = options.signal;
      const forwardAbort = () => controller.abort();
      if (external?.aborted) controller.abort();
      else if (external) external.addEventListener('abort', forwardAbort, { once: true });
      const timer = setTimeout(() => controller.abort(), Math.max(1000, timeoutMs));
      try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        // fetch resolves at the headers. Keep cancellation and the deadline
        // active until the caller has also consumed JSON or a download body.
        return readResponse ? await readResponse(response) : response;
      } catch (error) {
        if (error?.name === 'AbortError' && !external?.aborted) {
          throw new Error('Zeitüberschreitung bei der Serveranfrage');
        }
        throw error;
      } finally {
        clearTimeout(timer);
        if (external) external.removeEventListener('abort', forwardAbort);
      }
    },

    async api(method, url, body, options = {}) {
      const opts = { method, headers: {'Content-Type': 'application/json'} };
      if (body !== undefined) opts.body = JSON.stringify(body);
      if (options.signal) opts.signal = options.signal;
      try {
        const ownSecurityAction = this.canManageOwnAccount() && (
          (method === 'POST' && ['/api/account/password', '/api/auth/logout-all'].includes(url)) ||
          (method === 'DELETE' && (url === '/api/account/profile' || /^\/api\/account\/sessions\/[^/?]+$/.test(url) || /^\/api\/account\/identities\/(apple|google)$/.test(url)))
        );
        if (this.session.role === 'guest' && !['GET', 'HEAD', 'OPTIONS'].includes(method) && !ownSecurityAction) {
          throw new Error('Gäste können ansehen, aber nichts verändern oder erstellen');
        }
        return await this.fetchWithTimeout(url, opts, options.timeoutMs || 30000, async r => {
          if (r.status === 401) { window.location = '/login'; return null; }
          if (!r.ok) {
            let detail = `${r.status}`;
            try {
              const j = await r.json();
              detail = j.detail || detail;
            } catch (error) {
              if (error?.name === 'AbortError') throw error;
            }
            detail = this.apiErrorMessage(detail, `${r.status}`);
            if (!options.silent) this.showToast(`Fehler: ${detail}`, 'error');
            const error = new Error(detail);
            error.status = r.status;
            error.detail = detail;
            error.url = url;
            throw error;
          }
          return r.status === 204 ? null : await r.json();
        });
      } catch (error) {
        if (error?.name === 'AbortError') return null;
        throw error;
      }
    },
    showToast(message, type = 'ok') {
      this.toast = { show: true, message, type };
      setTimeout(() => this.toast.show = false, 3500);
      // Haptic-Feedback: kurzer Tick bei OK, längerer Tap-Pattern bei Fehler.
      // Wirkt nur auf Mobile mit Vibrate-API + User-Geste in der History.
      if ('vibrate' in navigator) {
        try {
          navigator.vibrate(type === 'ok' ? 15 : [25, 35, 25]);
        } catch (_) {}
      }
    },

    // Haptic-Helper für Quick-Actions ohne Toast (Tap-Bestätigung).
    // Wird z.B. bei Cart-Swipes, Verify-Checkbox, Cook-Mode-Toggle aufgerufen.
    haptic(pattern = 10) {
      if ('vibrate' in navigator) {
        try { navigator.vibrate(pattern); } catch (_) {}
      }
    },

    // Pull-to-Refresh: touch-tracking auf dem document. Wenn man am
    // Listen-Anfang nach unten zieht (>80px) und loslässt, wird die
    // aktuelle Page neu geladen. Indicator-Element 'ptr-indicator'
    // wird visuell mitgezogen.
    initPullToRefresh() {
      // Nur Mobile (Touch-Geräte). Auf Desktop nutzt der User F5.
      if (!('ontouchstart' in window)) return;
      let startY = 0, currentY = 0, pulling = false;
      const TRIGGER = 80;  // px die gezogen werden müssen
      const ind = document.getElementById('ptr-indicator');
      if (!ind) return;

      document.addEventListener('touchstart', (e) => {
        // Nur am Scroll-Anfang anfangen — wenn User schon weiter unten ist,
        // soll das normale Scrollen weiterlaufen
        if (window.scrollY > 5) return;
        // Nicht in Modals (recipeDetail, fsCompare etc) auslösen
        if (document.querySelector('.modal-backdrop[style*="display: flex"]')) return;
        startY = e.touches[0].clientY;
        pulling = true;
      }, { passive: true });

      document.addEventListener('touchmove', (e) => {
        if (!pulling) return;
        currentY = e.touches[0].clientY;
        const delta = currentY - startY;
        if (delta > 0 && window.scrollY === 0) {
          // Indicator-Position: max bei 1.2× TRIGGER, dann „prall"
          const progress = Math.min(delta / (TRIGGER * 1.5), 1);
          ind.style.transform = `translate(-50%, ${delta * 0.5}px)`;
          ind.style.opacity = String(progress);
          ind.classList.toggle('ready', delta >= TRIGGER);
        }
      }, { passive: true });

      document.addEventListener('touchend', () => {
        if (!pulling) return;
        const delta = currentY - startY;
        pulling = false;
        ind.style.transform = '';
        ind.style.opacity = '';
        ind.classList.remove('ready');
        if (delta >= TRIGGER && window.scrollY === 0) {
          this.haptic(20);
          this.reloadCurrentPage();
        }
      });
    },

    // Welche Methode ist „die richtige für die aktive Seite". Wird vom
    // Pull-to-Refresh aufgerufen. Andere Pages → no-op.
    reloadCurrentPage() {
      const map = {
        recipes: () => this.loadRecipes(),
        plan: () => this.loadMealPlan(),
        cart: () => this.loadCart(),
        admin: () => this.selectAdminTab(this.admin.tab, { updateUrl: false }),
      };
      const fn = map[this.page];
      if (fn) {
        fn();
        this.showToast('⟳ Neu geladen');
      }
    },

    // Gestenzustand der Einkaufsliste; die Methoden liegen in features/cart.js.
    _swipe: { startX: 0, startY: 0, currentX: 0, locked: null, id: null, el: null },
    formatBytes(b) {
      if (b === null || b === undefined || b === 0) return '—';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0;
      while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
      return b.toFixed(b < 10 ? 1 : 0) + ' ' + units[i];
    },
    formatTs(ts) {
      if (!ts) return '—';
      const d = typeof ts === 'number' ? new Date(ts*1000) : new Date(ts);
      return d.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
    },
    formatConfidence(c) {
      if (c === undefined || c === null) return '—';
      return Math.round(c * 100) + '%';
    },
    shortUrl(u) {
      if (!u) return '';
      return u.length > 60 ? u.slice(0, 57) + '…' : u;
    },
    statusClass(s) {
      if (s === 'ok') return 'ok';
      if (s === 'error') return 'err';
      if (s === 'running') return 'running';
      return '';
    },
    summarize(j) {
      if (!j.summary) return '';
      const s = j.summary;
      if (j.kind === 'scraper') {
        return `${s.auto||0} auto, ${s.pending||0} pending${s.errors ? ', '+s.errors+' err' : ''}`;
      }
      if (j.kind === 'reanalyze') {
        return `${s.auto_saved||0} eingeordnet, ${s.still_pending||0} pending, ${s.errors||0} err (${s.processed||0}/${s.total||0})`;
      }
      return '';
    },

    // ------------- Pending -------------
    pendingSort: 'newest',
    selectedPending: [],
    failedDownloads: [],
    retryingUrl: null,
    discardingUrl: null,
    bulkBusy: false,
    manualImportUrl: '',
    manualImportVisibility: 'private',
    manualImportNotice: '',
    householdImportOpen: false,
    manualImporting: false,

    // ------------- Tests -------------
    testing: {
      openai: false,
      paths: false, ytdlp: false,
      webhook: -1,   // Index des gerade getesteten Webhook (-1 = keiner)
    },
    testResults: {},

    reanalyzeProgress: null,
    _progressTimer: null,

    // ------------- Verzeichnis-Browser -------------
    browser: {
      show: false,
      mode: 'local',
      currentPath: '/',
      entries: [],
      suggestedRoots: [],
      parent: null,
      isRoot: false,
      loading: false,
      callback: null,         // (path) => void
      title: 'Verzeichnis wählen',
    },

        // ------------- Pending Reanalyze -------------
    reanalyzing: {},
    reanalyzingAll: false,

    // ------------- Schedule Helpers -------------
    SCHEDULE_PRESETS_SCRAPER: [
      { label: 'Alle 15 Min', value: '*:0/15' },
      { label: 'Alle 30 Min', value: '*:0/30' },
      { label: 'Stündlich',   value: 'hourly' },
      { label: 'Alle 2 Std',  value: '*:0/120' },
      { label: 'Alle 6 Std',  value: '00,06,12,18:00' },
      { label: 'Täglich 08:00', value: '*-*-* 08:00:00' },
    ],

    // ------------- History bearbeiten -------------
    editingItem: null,

    // ════════════════════════════════════════════════════════════════════
    // Rezept-Browser + Einkaufskorb
    // ════════════════════════════════════════════════════════════════════

    // ── Helpers ───────────────────────────────────────────────────────
    formatAmount(n) {
      if (n === null || n === undefined) return '';
      if (Number.isInteger(n)) return String(n);
      // max 2 Nachkomma, trailing zeros weg, mit deutschem Komma
      const s = (Math.round(n * 100) / 100).toString();
      return s.replace('.', ',');
    },

    formatDuration(secs) {
      if (secs === null || secs === undefined || secs < 0) return '';
      const s = Math.floor(secs);
      if (s < 60) return s + 's';
      const m = Math.floor(s / 60);
      const r = s % 60;
      if (m < 60) return r === 0 ? m + ':00' : m + ':' + String(r).padStart(2, '0');
      const h = Math.floor(m / 60);
      const mm = m % 60;
      return h + ':' + String(mm).padStart(2, '0') + ':' + String(r).padStart(2, '0');
    },

    // ── Caption-Renderer ────────────────────────────────────────────
    // Zerlegt einen Beschreibungstext in Segmente:
    //   {type: 'text', value: '...'}     — normaler Text, Newlines bleiben
    //   {type: 'hashtag', value: '#tag'} — #vegan, #pasta etc.
    //   {type: 'mention', value: '@user'}— @chefkoch
    //   {type: 'url', value: 'http://…'} — anklickbare Links
    //
    // Single-pass-Regex: alternations matchen in der Reihenfolge ihrer
    // Spezifität. URLs zuerst, dann Hashtag/Mention (sonst würde z.B.
    // ein '@' inside einer URL als Mention erkannt).
    formatCaption(text) {
      if (!text) return [];
      // \p{L} matched alle Unicode-Letters (inkl. ä,ö,ü,ß) — moderne Browser.
      const RE = /(https?:\/\/\S+|#[\p{L}\p{N}_\-]+|@[\p{L}\p{N}_.\-]+)/gu;
      const out = [];
      let last = 0;
      let m;
      while ((m = RE.exec(text)) !== null) {
        if (m.index > last) {
          out.push({ type: 'text', value: text.slice(last, m.index) });
        }
        const tok = m[0];
        let type;
        if (tok.startsWith('#')) type = 'hashtag';
        else if (tok.startsWith('@')) type = 'mention';
        else type = 'url';
        out.push({ type, value: tok });
        last = m.index + tok.length;
      }
      if (last < text.length) {
        out.push({ type: 'text', value: text.slice(last) });
      }
      return out;
    },

    // Klick auf Hashtag im Caption-Render: wenn das System einen passenden
    // Tag in den Facets findet, Filter setzen und zur Rezepte-Page springen.
    // Sonst Toast — der Hashtag ist nur in der Caption, kein Filter-Tag.
    clickCaptionHashtag(hashtagText) {
      const name = hashtagText.replace(/^#/, '').toLowerCase().trim();
      if (!name) return;
      const found = (this.recipes.facets.tags || []).find(
        t => (t.name || '').toLowerCase() === name
      );
      if (!found) {
        this.showToast(`Kein Tag "${name}" — Hashtag nur in Caption`);
        return;
      }
      // Filter setzen + zur Liste — und das Modal schließen, sonst sieht
      // der User nichts vom angewendeten Filter.
      this.closeRecipeDetail();
      this.recipes.filters.tag_ids = [found.id];
      this.recipes.filters.offset = 0;
      this.page = 'recipes';
      this.loadRecipes();
      this.showToast(`Filter: Tag "${found.name}"`);
    },

    // Getter für das x-show im Template (würde Methodaufruf jedes Render
    // triggern; einfaches Property reicht)
    get ingredientFacetsLimited() {
      return this.recipes._ingFacetsLimited;
    },
    // Cache wird beim Modal-Close NICHT geleert — der nächste openRecipe
    // dürfte dieselbe ID sein wenn der User schnell wieder klickt.
    _detailPrefetch: new Map(),

    // IntersectionObserver für Infinite-Scroll. Wird beim ersten Render des
    // Sentinel-Elements initialisiert (x-init). Re-erstellt sich selbst nicht —
    // einmaliges Setup reicht, Observer beobachtet das gleiche Element.
    _scrollObserver: null,
  };
}
