// Scrapper Manager - Frontend Logic
function scrapperApp() {
  return {
    page: 'recipes',
    session: { username: '', is_admin: true, full_access: true, loaded: false },
    admin: {
      tab: 'import',
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
        jobId: null, preflight: null, pollTimer: null,
        auto_rotate: true, remove_blank_pages: true, auto_crop: true,
        deskew_scans: true, ocr_scans: true, improve_contrast: true,
        sharpen_scans: true, scan_dpi: 300,
        ocr_language: 'deu+eng', keep_original: true, limit: 500,
      },
      pageEditor: { loading: false, saving: false, filename: '', pages: [], previewKey: Date.now() },
      maintenanceRuns: [],
      maintenanceBusy: '',
      maintenanceResult: null,
      accessDenied: false,
    },

    // Zentrale Navigations-Helper — page-switch plus die zugehörigen
    // Loader. Vorher waren die Loader direkt im @click jeder nav-item,
    // was beim Refactor (z.B. neue Bottom-Sheet) duplication erzeugte.
    navTo(targetPage, { updateUrl = true } = {}) {
      const allowed = new Set(['recipes','favorites','cart','admin']);
      if (!allowed.has(targetPage)) targetPage = 'recipes';
      this.page = targetPage;
      if (this.browser?.show) this.browser.show = false;
      if (this.recipeDetail?.show) this.recipeDetail.show = false;

      if (targetPage === 'favorites') {
        this.recipes.filters.favorite_only = true;
      } else if (targetPage === 'recipes') {
        this.recipes.filters.favorite_only = false;
      }

      switch (targetPage) {
        case 'admin':     this.selectAdminTab(this.admin.tab || 'import', { updateUrl: false }); break;
        case 'recipes':
        case 'favorites': this.recipes.filters.offset = 0; this.loadRecipes(); this.loadFacets(); break;
        case 'cart':      this.loadCart(); if (!this.config?.einkauf) this.loadConfig(); break;
      }

      if (updateUrl && window.history?.replaceState) {
        const target = targetPage === 'admin'
          ? `/admin${this.admin.tab === 'pdf' ? '/pdf' : ''}`
          : `/?tab=${encodeURIComponent(targetPage)}`;
        window.history.replaceState({}, '', target);
      }
    },
    pageLabel() {
      return ({
        recipes: 'Alle Rezepte', favorites: 'Favoriten', cart: 'Einkaufsliste',
        admin: 'Admin',
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

    // ── Recipe-Browser + Einkaufskorb (feat/recipe-browser-and-cart) ─────
    recipes: {
      items: [], total: 0, loading: false,
      filters: { search: '', type: '', category: '', tag_ids: [], ingredients: [], ingredients_status: '', verified: '', favorite_only: false, min_rating: 0, limit: 60, offset: 0 },
      searchMeta: { corrected: false, original: '', query: '', suggestion: '' },
      filterDrawerOpen: false,  // nur auf Mobile sichtbar: Filter als Drawer statt Sidebar
      ingredientSearch: '',     // Such-Input im Zutaten-Filter-Block
      _ingFacetsLimited: false, // True wenn die Chip-Liste auf MAX geclipped wurde
      facets: { types: [], categories: [], tags: [], ingredients: [] },
      extractionRunning: false, extractionPending: 0,
      extractionStats: {}, _pollTimer: null,
    },
    cart: {
      items: [],
      add: { name: '', amount: null, unit: '' },
    },
    pushingToEinkauf: false,    // Loading-state für Push-Button
    trash: {
      items: [], totalCount: 0, loading: false, emptying: false,
    },
    audit: {
      data: {
        total_recipes: 0,
        exact_duplicates: [], url_duplicates: [], folder_duplicates: [], similar_clusters: [],
        bad_names: [], sync_errors: [], ai_category_findings: [], ai_name_findings: [],
        ai_folder_findings: [], ai_suggestions: [], empty_recipes: [], failed_downloads: [],
        data_gaps: { no_image: [], no_steps: [], no_url: [], few_ingredients: [], no_description: [], unverified: [], fs_missing: [], no_nutrition: [] },
      },
      summary: {
        exact_count: 0, exact_groups: 0, url_count: 0, folder_count: 0,
        similar_count: 0, similar_clusters: 0, bad_count: 0, sync_error_count: 0,
        ai_category_count: 0, ai_name_count: 0, ai_folder_count: 0,
        empty_recipe_count: 0, failed_download_count: 0, with_ai_suggestions: 0,
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
    },
    // Benutzer-Verwaltung (Multi-User-Auth)
    users: {
      list: [],
      loading: false,
      showAdd: false,
      creating: false,
      addForm: { username: '', password: '' },
    },
    knownIngredients: [],   // distinct Zutaten-Namen für Edit-Autocomplete
    recipeDetail: {
      show: false, data: null, newTag: '',
      cooking: false, extracting: false,
      multiplier: 1,    // Portionen-Skalierung beim Kochen
      cookMode: false,  // Koch-Modus: nur Schritte, große Schrift, Wake-Lock
      tab: 'info',      // Detail-Reiter: info | zutaten | steps (kompaktes Mobile-Layout)
      wakeLockActive: false,  // UI-Indikator ob Wake-Lock greift
      // Zutaten-Edit-Modus: lokaler working-copy bis 'Speichern' geklickt wird
      editingIngredients: false,
      editIngs: [],
      savingIngredients: false,
      computingNutrition: false,    // Loading-state für ⚡ Berechnen-Button
      sharing: false,                // Loading-state für 🔗 Share-Button
      verifying: false,              // Loading für 'manuell geprüft'-Toggle
      rescraping: false,             // Loading für Re-Scrape im Modal
    },
    _wakeLock: null,
    // Per-Schritt-Timer (key = step.id, value = {status, remaining, intervalId})
    // Bewusst auf scrapperApp-Top-Level damit Alpine reactivity trackt.
    timers: {},
    _audioCtx: null,
    init() {
      const params = new URLSearchParams(window.location.search);
      const routePage = document.body?.dataset?.initialPage ||
        (window.location.pathname.startsWith('/admin') ? 'admin' : '');
      const requestedPage = routePage || params.get('tab');
      const validTab = ['recipes','favorites','cart','admin'].includes(requestedPage) ? requestedPage : 'recipes';
      this.page = validTab;
      if (validTab === 'admin') {
        const routeAdminTab = document.body?.dataset?.initialAdminTab ||
          (window.location.pathname === '/admin/pdf' ? 'pdf' : '');
        const requestedAdmin = routeAdminTab || params.get('admin');
        if (requestedAdmin) this.admin.tab = requestedAdmin;
      }

      this.loadSession();
      this.loadRecentJobs();
      this.loadStats();
      this.loadHddStatus();
      this.navTo(this.page, { updateUrl: false });
      this._jobsTimer = setInterval(() => this.loadRecentJobs(), 15000);
      this._statsTimer = setInterval(() => this.loadStats(), 60000);
      this._hddTimer = setInterval(() => this.loadHddStatus(), 30000);
      this._startEventStream();
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) this._pauseBackgroundWork();
        else this._resumeBackgroundWork();
      });
      window.addEventListener('popstate', () => {
        const params = new URLSearchParams(window.location.search);
        const next = window.location.pathname.startsWith('/admin')
          ? 'admin' : (params.get('tab') || 'recipes');
        if (next === 'admin') {
          const section = window.location.pathname === '/admin/pdf' ? 'pdf' : params.get('section');
          if (section) this.admin.tab = section;
        }
        this.navTo(next, { updateUrl: false });
      });
      this.$nextTick(() => this.initPullToRefresh());
    },

    async loadSession() {
      try {
        const r = await this.api('GET', '/api/session');
        this.session = { ...this.session, ...(r || {}), is_admin: true, full_access: true, loaded: true };
        this.admin.accessDenied = false;
      } catch (_) {
        this.session = { ...this.session, is_admin: true, full_access: true, loaded: true };
        if (this.page === 'admin') this.showToast('Sitzung konnte nicht geladen werden', 'err');
      }
    },

    async selectAdminTab(tab, { updateUrl = true } = {}) {
      const allowed = new Set(['import','quality','versions','pdf','search','maintenance','master','settings','trash']);
      this.admin.tab = allowed.has(tab) ? tab : 'import';
      this.page = 'admin';
      if (updateUrl && window.history?.replaceState) {
        const target = this.admin.tab === 'pdf' ? '/admin/pdf' : `/admin?section=${encodeURIComponent(this.admin.tab)}`;
        window.history.replaceState({}, '', target);
      }
      this.loadAdminOverview();
      switch (this.admin.tab) {
        case 'import':
          this.loadAdminImport(); this.loadPending(); this.loadFailedDownloads(); this.loadJobs(); break;
        case 'quality': this.loadAudit(); break;
        case 'versions': this.loadAdminVersions(); break;
        case 'pdf': if (!this.config?.pdf) this.loadConfig(); this.loadPdfPreflight(); break;
        case 'search': this.loadAdminSynonyms(); break;
        case 'maintenance': this.loadMaintenanceRuns(); break;
        case 'master': this.loadMaster(); break;
        case 'settings': this.loadConfig(); this.loadUsers(); break;
        case 'trash': this.loadTrash(); break;
      }
    },

    async loadAdminOverview() {
      try { this.admin.overview = await this.api('GET', '/api/admin/overview'); }
      catch (_) { /* nav remains usable */ }
    },

    async loadAdminImport() {
      try { this.admin.importCenter = await this.api('GET', '/api/admin/import-center?limit=100'); }
      catch (e) { this.showToast('Importzentrale konnte nicht geladen werden', 'err'); }
    },

    async loadAdminVersions() {
      this.admin.versionsLoading = true;
      try {
        const q = this.admin.versionRecipeId ? `?recipe_id=${encodeURIComponent(this.admin.versionRecipeId)}` : '';
        const r = await this.api('GET', '/api/admin/versions' + q);
        this.admin.versions = r.items || [];
      } catch (_) { this.showToast('Versionen konnten nicht geladen werden', 'err'); }
      finally { this.admin.versionsLoading = false; }
    },

    async viewAdminVersion(item) {
      try {
        this.admin.versionDetail = await this.api('GET', `/api/admin/versions/${item.id}`);
      } catch (_) { this.showToast('Versionsdetails konnten nicht geladen werden', 'err'); }
    },

    async restoreAdminVersion(item) {
      if (!confirm(`Version ${item.version_no} von „${item.recipe_name || item.recipe_id}“ wiederherstellen? Der aktuelle Stand wird vorher ebenfalls versioniert.`)) return;
      const r = await this.api('POST', `/api/admin/versions/${item.id}/restore`, {});
      if (r?.ok) {
        this.showToast('Version wiederhergestellt');
        await this.loadAdminVersions();
        if (this.recipeDetail?.data?.id === item.recipe_id) await this.openRecipe(item.recipe_id);
      }
    },

    async loadAdminSynonyms() {
      try {
        const r = await this.api('GET', '/api/admin/search/synonyms');
        this.admin.synonyms = r.items || [];
      } catch (_) { this.showToast('Synonyme konnten nicht geladen werden', 'err'); }
    },

    async saveAdminSynonym() {
      const term = (this.admin.synonymForm.term || '').trim();
      const synonyms = (this.admin.synonymForm.synonymsText || '').split(/[,;\n]/).map(v => v.trim()).filter(Boolean);
      if (term.length < 2) return this.showToast('Suchbegriff fehlt', 'err');
      const r = await this.api('POST', '/api/admin/search/synonyms', { term, synonyms });
      if (r?.ok) {
        this.admin.synonyms = r.items || [];
        this.admin.synonymForm = { term: '', synonymsText: '' };
        this.showToast('Synonymgruppe gespeichert');
      }
    },

    async editAdminSynonym(item) {
      this.admin.synonymForm = { term: item.term, synonymsText: (item.synonyms || []).join(', ') };
    },

    async deleteAdminSynonym(item) {
      if (!confirm(`Synonymgruppe „${item.term}“ löschen?`)) return;
      await this.api('DELETE', `/api/admin/search/synonyms/${item.id}`);
      await this.loadAdminSynonyms();
    },

    async rebuildAdminSearch() {
      this.admin.maintenanceBusy = 'search';
      try {
        const r = await this.api('POST', '/api/admin/search/rebuild', {});
        this.showToast(`${r.indexed || 0} Rezepte neu indiziert`);
      } finally { this.admin.maintenanceBusy = ''; }
    },

    async loadPdfPages() {
      const id = Number(this.admin.pdf.recipe_id || 0);
      if (!id) return this.showToast('Bitte zuerst eine Rezept-ID eingeben', 'err');
      this.admin.pageEditor.loading = true;
      try {
        const r = await this.api('GET', `/api/admin/pdf/${id}/pages`);
        this.admin.pageEditor.filename = r.filename || '';
        this.admin.pageEditor.pages = (r.pages || []).map(page => ({ ...page, rotation_delta: 0, deleted: false }));
        this.admin.pageEditor.previewKey = Date.now();
      } catch (_) { this.showToast('PDF-Seiten konnten nicht geladen werden', 'err'); }
      finally { this.admin.pageEditor.loading = false; }
    },

    movePdfPage(index, delta) {
      const target = index + delta;
      const pages = this.admin.pageEditor.pages;
      if (target < 0 || target >= pages.length) return;
      const [item] = pages.splice(index, 1);
      pages.splice(target, 0, item);
      this.admin.pageEditor.pages = [...pages];
    },

    rotatePdfPage(page, delta) {
      page.rotation_delta = ((Number(page.rotation_delta || 0) + delta) % 360 + 360) % 360;
      this.admin.pageEditor.pages = [...this.admin.pageEditor.pages];
    },

    async applyPdfPageEdits() {
      const id = Number(this.admin.pdf.recipe_id || 0);
      const active = this.admin.pageEditor.pages.filter(page => !page.deleted);
      if (!id || !active.length) return this.showToast('Mindestens eine Seite muss erhalten bleiben', 'err');
      if (!confirm(`${active.length} PDF-Seite(n) in der angezeigten Reihenfolge speichern? Das Original wird vorher gesichert.`)) return;
      this.admin.pageEditor.saving = true;
      try {
        const rotations = {};
        active.forEach(page => { if (page.rotation_delta) rotations[String(page.page)] = page.rotation_delta; });
        const r = await this.api('POST', `/api/admin/pdf/${id}/pages/apply`, {
          order: active.map(page => page.page), rotations, keep_original: true,
        });
        if (r?.ok) {
          this.showToast('PDF-Seiten gespeichert');
          await this.loadPdfPages();
          await this.loadAdminOverview();
          if (typeof this.loadRecipes === 'function') await this.loadRecipes();
        }
      } catch (_) { this.showToast('PDF-Seiten konnten nicht gespeichert werden', 'err'); }
      finally { this.admin.pageEditor.saving = false; }
    },

    async loadPdfPreflight() {
      try {
        this.admin.pdf.preflight = await this.api('GET', '/api/admin/pdf/preflight');
        if (!this.admin.pdf.running) {
          const active = await this.api('GET', '/api/admin/pdf/jobs/active');
          if (active?.active && active?.job?.id) {
            this.admin.pdf.running = true;
            this.admin.pdf.jobId = Number(active.job.id);
            this.admin.pdf.result = { ...(active.job.result || {}), run_id: active.job.id };
            this.pollAdminPdfJob(this.admin.pdf.jobId);
          }
        }
      } catch (e) {
        this.admin.pdf.preflight = { ok: false, issues: [{ message: e?.message || 'Systemprüfung nicht verfügbar' }], warnings: [] };
      }
    },

    async pollAdminPdfJob(runId) {
      if (!runId || this.admin.pdf.jobId !== runId) return;
      try {
        const job = await this.api('GET', `/api/admin/pdf/jobs/${runId}`);
        this.admin.pdf.result = { ...(job?.result || {}), run_id: runId, job_status: job?.status };
        if (job?.status === 'running') {
          this.admin.pdf.pollTimer = setTimeout(() => this.pollAdminPdfJob(runId), 1500);
          return;
        }
        this.admin.pdf.running = false;
        this.admin.pdf.jobId = null;
        const errors = Number(job?.result?.errors || 0);
        if (job?.status === 'ok' && errors === 0) {
          this.showToast(job?.kind === 'pdf_dry_run' ? 'PDF-Analyse abgeschlossen' : 'PDF-Aufbereitung abgeschlossen');
        } else {
          const first = (job?.result?.files || []).find(f => f.error)?.error || job?.result?.error || `${errors} Datei(en) fehlgeschlagen`;
          this.showToast(`PDF-Lauf beendet: ${first}`, 'err');
        }
        await this.loadMaintenanceRuns();
        await this.loadAdminOverview();
        if (typeof this.loadRecipes === 'function') await this.loadRecipes();
      } catch (e) {
        this.admin.pdf.running = false;
        this.admin.pdf.jobId = null;
        this.showToast(`PDF-Status konnte nicht geladen werden: ${e?.message || 'unbekannter Fehler'}`, 'err');
      }
    },

    async runAdminPdf(dryRun = true) {
      if (this.admin.pdf.running) return;
      this.admin.pdf.running = true;
      this.admin.pdf.result = null;
      clearTimeout(this.admin.pdf.pollTimer);
      try {
        await this.loadPdfPreflight();
        const pf = this.admin.pdf.preflight;
        if (pf && pf.ok === false) {
          throw new Error((pf.issues || []).map(v => v.message).join('; ') || 'PDF-Systemprüfung fehlgeschlagen');
        }
        const p = this.admin.pdf;
        const payload = {
          recipe_id: p.recipe_id ? Number(p.recipe_id) : null,
          process_all: !p.recipe_id && p.process_all,
          dry_run: dryRun,
          background: true,
          limit: Number(p.limit || 50),
          auto_rotate: !!p.auto_rotate,
          remove_blank_pages: !!p.remove_blank_pages,
          auto_crop: !!p.auto_crop,
          deskew_scans: !!p.deskew_scans,
          ocr_scans: !!p.ocr_scans,
          improve_contrast: !!p.improve_contrast,
          sharpen_scans: !!p.sharpen_scans,
          scan_dpi: Number(p.scan_dpi || 300),
          ocr_language: p.ocr_language || 'deu+eng',
          keep_original: !!p.keep_original,
        };
        const accepted = await this.api('POST', '/api/admin/pdf/process', payload);
        if (accepted?.accepted && accepted?.run_id) {
          this.admin.pdf.jobId = Number(accepted.run_id);
          this.admin.pdf.result = { ...(accepted.result || {}), run_id: accepted.run_id };
          this.showToast('PDF-Lauf gestartet – er läuft auch bei geschlossenem Tab weiter');
          this.pollAdminPdfJob(this.admin.pdf.jobId);
          return;
        }
        // Kompatibilität zu älteren Serverständen / synchronen Einzeltests.
        this.admin.pdf.result = accepted;
        this.admin.pdf.running = false;
        const errors = Number(accepted?.errors || 0);
        this.showToast(errors ? `PDF-Lauf mit ${errors} Fehler(n) beendet` : (dryRun ? 'PDF-Analyse abgeschlossen' : 'PDF-Aufbereitung abgeschlossen'), errors ? 'err' : 'ok');
      } catch (e) {
        this.admin.pdf.running = false;
        this.admin.pdf.jobId = null;
        this.showToast(`PDF-Verarbeitung: ${e?.message || 'unbekannter Fehler'}`, 'err');
      }
    },

    async runMaintenance(kind) {
      this.admin.maintenanceBusy = kind;
      this.admin.maintenanceResult = null;
      try {
        this.admin.maintenanceResult = await this.api('POST', `/api/admin/maintenance/run/${kind}`, {});
        this.showToast('Wartung abgeschlossen');
        await this.loadMaintenanceRuns();
        await this.loadAdminOverview();
      } catch (_) { this.showToast('Wartung fehlgeschlagen', 'err'); }
      finally { this.admin.maintenanceBusy = ''; }
    },

    async loadMaintenanceRuns() {
      try {
        const r = await this.api('GET', '/api/admin/maintenance/runs');
        this.admin.maintenanceRuns = r.items || [];
      } catch (_) { this.admin.maintenanceRuns = []; }
    },

    _startEventStream() {
      try {
        const es = new EventSource('/api/events');
        this._eventSource = es;
        es.addEventListener('status', (e) => {
          try { this.status = JSON.parse(e.data); } catch(_) {}
        });
        es.addEventListener('scraper_progress', (e) => {
          try { this.scraperProgress = JSON.parse(e.data); } catch(_) {}
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
      this.refreshStatus();
      this.refreshProgress();
      this._statusTimer = setInterval(() => this.refreshStatus(), 4000);
      this._progressTimer = setInterval(() => this.refreshProgress(), 3000);
    },

    // App im Hintergrund (PWA minimiert / Tab inaktiv): alles Periodische
    // stoppen — iOS drosselt Timer ohnehin, aber SSE hält sonst die
    // Verbindung + Funkmodul wach.
    _bgPaused: false,
    _pauseBackgroundWork() {
      if (this._bgPaused) return;
      this._bgPaused = true;
      for (const t of ['_jobsTimer', '_statsTimer', '_hddTimer', '_statusTimer', '_progressTimer']) {
        if (this[t]) { clearInterval(this[t]); this[t] = null; }
      }
      if (this._eventSource) { this._eventSource.close(); this._eventSource = null; }
    },
    _resumeBackgroundWork() {
      if (!this._bgPaused) return;
      this._bgPaused = false;
      // Sofort frische Daten, dann Timer + SSE wie beim Init
      this.loadRecentJobs(); this.loadStats(); this.loadHddStatus();
      this._jobsTimer = setInterval(() => this.loadRecentJobs(), 15000);
      this._statsTimer = setInterval(() => this.loadStats(), 60000);
      this._hddTimer = setInterval(() => this.loadHddStatus(), 30000);
      this._startEventStream();
    },

    // ------------- Helpers -------------
    async api(method, url, body) {
      const opts = { method, headers: {'Content-Type': 'application/json'} };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const r = await fetch(url, opts);
      if (r.status === 401) { window.location = '/login'; return null; }
      if (!r.ok) {
        let detail = `${r.status}`;
        try { const j = await r.json(); detail = j.detail || detail; } catch(e){}
        this.showToast(`Fehler: ${detail}`, 'error');
        throw new Error(detail);
      }
      return r.status === 204 ? null : await r.json();
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
        favorites: () => this.loadRecipes(),
        cart: () => this.loadCart(),
        admin: () => this.selectAdminTab(this.admin.tab, { updateUrl: false }),
      };
      const fn = map[this.page];
      if (fn) {
        fn();
        this.showToast('⟳ Neu geladen');
      }
    },

    // ────────────────────────────────────────────────────────────────────
    // Cart-Swipe-Actions (Mobile)
    // Swipe links (>= 60px) → toggle 'erledigt' Status
    // Swipe rechts (>= 60px) → löschen mit Confirm
    // Während des Swipes wird die Karte mit transform: translateX bewegt
    // und der entsprechende Action-Hintergrund (grün/rot) sichtbar.
    // ────────────────────────────────────────────────────────────────────
    _swipe: { startX: 0, startY: 0, currentX: 0, locked: null, id: null, el: null },
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
    formatDuration(sec) {
      if (sec === null || sec === undefined) return '—';
      sec = Math.round(sec);
      if (sec < 60) return sec + 's';
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      if (m < 60) return m + 'm ' + s + 's';
      const h = Math.floor(m / 60);
      return h + 'h ' + (m % 60) + 'm';
    },
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

    // ------------- Status -------------
    async refreshStatus() {
      try {
        this.status = await this.api('GET', '/api/jobs/status/current');
      } catch(e){}
    },
    async loadRecentJobs() {
      try {
        const all = await this.api('GET', '/api/jobs/list?limit=20');
        this.lastScraper = all.find(j => j.kind === 'scraper' && j.status === 'ok');
      } catch(e) {}
    },
    async loadStats() {
      this.statsLoading = true;
      try {
        const [jobs, conf] = await Promise.all([
          this.api('GET', '/api/stats/jobs-per-day?days=14'),
          this.api('GET', '/api/stats/confidence-histogram?buckets=10'),
        ]);
        this.stats = { jobs, conf };
      } catch(e) {} finally {
        this.statsLoading = false;
      }
    },

    // ------------- Externe HDD via Shelly -------------
    async loadHddStatus() {
      this.hddBusy = true;
      try {
        this.hddStatus = await this.api('GET', '/api/hdd/status');
      } catch(e) {
        this.hddStatus = null;
      } finally {
        this.hddBusy = false;
      }
    },
    async hddPowerOn() {
      if (!confirm('Externe HDD einschalten und mounten?\n\n1. Shelly Plug an\n2. ' + (this.hddStatus.spinup_delay_sec || 12) + 's warten (HDD-Spinup)\n3. mount ' + this.hddStatus.mount_point))
        return;
      this.hddBusy = true;
      this.hddLastOutput = '';
      try {
        const r = await this.api('POST', '/api/hdd/power-on');
        this.hddLastOutput = JSON.stringify(r, null, 2);
        if (r.ok) {
          this.showToast(r.skipped ? 'Schon gemounted' : 'HDD ist online + gemounted ✓', 'ok');
        } else {
          this.showToast('Fehler: ' + (r.error || 'unbekannt'), 'error');
        }
        await this.loadHddStatus();
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      } finally {
        this.hddBusy = false;
      }
    },
    async hddPowerOff() {
      if (!confirm('Externe HDD unmounten und ausschalten?\n\n1. umount ' + this.hddStatus.mount_point + '\n2. ' + (this.hddStatus.unmount_delay_sec || 2) + 's warten (FS flush)\n3. Shelly Plug aus'))
        return;
      this.hddBusy = true;
      this.hddLastOutput = '';
      try {
        const r = await this.api('POST', '/api/hdd/power-off');
        this.hddLastOutput = JSON.stringify(r, null, 2);
        if (r.ok) {
          this.showToast('HDD ist offline ✓', 'ok');
        } else {
          this.showToast('Fehler: ' + (r.error || 'unbekannt'), 'error');
        }
        await this.loadHddStatus();
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      } finally {
        this.hddBusy = false;
      }
    },
    async hddShellyToggle() {
      this.hddBusy = true;
      try {
        const r = await this.api('POST', '/api/hdd/shelly-toggle');
        this.showToast(r.ok ? ('Shelly jetzt ' + (r.shelly_on ? 'AN' : 'AUS')) : 'Fehler', r.ok ? 'ok' : 'error');
        await this.loadHddStatus();
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      } finally {
        this.hddBusy = false;
      }
    },
    renderJobsChart() {
      const s = this.stats && this.stats.jobs;
      if (!s || !s.days || s.days.length === 0) return '<div class="muted" style="padding:20px 0; text-align:center;">keine Daten</div>';
      const w = 600, h = 140, pad = 24;
      const days = s.days;
      const ALLOWED = ['scraper', 'reanalyze'];
      const kinds = Object.keys(s.series).filter(k => ALLOWED.includes(k));
      const palette = { scraper: '#f97316', reanalyze: '#a855f7' };
      // Max-Wert für Y-Skala
      let maxVal = 1;
      kinds.forEach(k => s.series[k].forEach(v => { if (v > maxVal) maxVal = v; }));
      const barW = (w - pad*2) / days.length;
      let svg = `<svg viewBox="0 0 ${w} ${h}" style="width:100%; height:140px;">`;
      // X-Axis labels (jeder 3. Tag)
      days.forEach((day, i) => {
        if (i % 3 === 0 || i === days.length-1) {
          const x = pad + i*barW + barW/2;
          const label = day.slice(5);  // MM-DD
          svg += `<text x="${x}" y="${h-4}" fill="#64748b" font-size="9" text-anchor="middle">${label}</text>`;
        }
      });
      // Y-Gridlines
      [0.25, 0.5, 0.75, 1.0].forEach(p => {
        const y = h - pad - (h - pad*2) * p;
        svg += `<line x1="${pad}" y1="${y}" x2="${w-pad}" y2="${y}" stroke="#1e293b" stroke-width="1"/>`;
      });
      // Stacked Bars
      days.forEach((day, i) => {
        let stackY = h - pad;
        kinds.forEach(kind => {
          const v = s.series[kind][i] || 0;
          if (v === 0) return;
          const barH = (h - pad*2) * (v / maxVal);
          stackY -= barH;
          const color = palette[kind] || '#94a3b8';
          svg += `<rect x="${pad + i*barW + 1}" y="${stackY}" width="${Math.max(barW-2, 1)}" height="${barH}" fill="${color}" opacity="0.85"><title>${day} · ${kind}: ${v}</title></rect>`;
        });
      });
      // Max-Label
      svg += `<text x="${pad-4}" y="${pad+4}" fill="#64748b" font-size="9" text-anchor="end">${maxVal}</text>`;
      svg += `<text x="${pad-4}" y="${h-pad+3}" fill="#64748b" font-size="9" text-anchor="end">0</text>`;
      svg += '</svg>';
      return svg;
    },
    renderConfChart() {
      const s = this.stats && this.stats.conf;
      if (!s || !s.counts || s.counts.length === 0) return '';
      const w = 600, h = 140, pad = 24;
      const maxCount = Math.max(...s.counts, 1);
      const barW = (w - pad*2) / s.counts.length;
      let svg = `<svg viewBox="0 0 ${w} ${h}" style="width:100%; height:140px;">`;
      // Y-Gridlines
      [0.5, 1.0].forEach(p => {
        const y = h - pad - (h - pad*2) * p;
        svg += `<line x1="${pad}" y1="${y}" x2="${w-pad}" y2="${y}" stroke="#1e293b" stroke-width="1"/>`;
      });
      // Bars
      s.counts.forEach((v, i) => {
        const barH = (h - pad*2) * (v / maxCount);
        const x = pad + i*barW + 1;
        const y = h - pad - barH;
        const label = s.buckets[i];
        // Color-Gradient: rot → orange → grün je nach Confidence
        const conf = (i + 0.5) / s.counts.length;
        const color = conf < 0.3 ? '#ef4444' : conf < 0.6 ? '#f97316' : conf < 0.85 ? '#eab308' : '#22c55e';
        svg += `<rect x="${x}" y="${y}" width="${Math.max(barW-2, 1)}" height="${barH}" fill="${color}" opacity="0.85"><title>${label}: ${v}</title></rect>`;
        // X-Label (jeden 2.)
        if (i % 2 === 0) {
          svg += `<text x="${x + barW/2}" y="${h-4}" fill="#64748b" font-size="9" text-anchor="middle">${label.split('-')[0]}</text>`;
        }
      });
      svg += `<text x="${pad-4}" y="${pad+4}" fill="#64748b" font-size="9" text-anchor="end">${maxCount}</text>`;
      svg += '</svg>';
      return svg;
    },
    async refreshProgress() {
      // Nur abfragen wenn Job läuft, sonst Last-Info behalten
      
      if (this.status && this.status.scraper) {
        try { this.scraperProgress = await this.api('GET', '/api/jobs/scraper/progress'); } catch(e) {}
      } else if (this.scraperProgress && this.scraperProgress.running) {
        try { this.scraperProgress = await this.api('GET', '/api/jobs/scraper/progress'); } catch(e) {}
      }
      if (this.status && this.status.reanalyze) {
        try { this.reanalyzeProgress = await this.api('GET', '/api/pending/reanalyze/progress'); } catch(e) {}
      } else if (this.reanalyzeProgress && this.reanalyzeProgress.running) {
        try { this.reanalyzeProgress = await this.api('GET', '/api/pending/reanalyze/progress'); } catch(e) {}
        // Nach Ende: pending neu laden
        if (this.reanalyzeProgress && !this.reanalyzeProgress.running) {
          await this.loadPending();
        }
      }
    },

    // ------------- Jobs -------------
    async runScraper() {
      await this.api('POST', '/api/jobs/scraper/run');
      this.showToast('Scraper gestartet');
      this.refreshStatus();
    },
    async cancelScraper() {
      if (!confirm('Scraper abbrechen? Die gerade laufende URL wird noch fertig verarbeitet, danach wird gestoppt.')) return;
      try {
        await this.api('POST', '/api/jobs/scraper/cancel', {});
        this.showToast('Cancel-Signal gesendet (laufende URL wird noch fertig)', 'ok');
        this.refreshStatus();
      } catch(e) {}
    },
    async loadJobs() {
      this.jobs = await this.api('GET', '/api/jobs/list?limit=50');
    },
    async cleanupFailedJobs() {
      const n = this.jobs.filter(j => j.status === 'error').length;
      if (!confirm(`${n} Failed-Jobs aus der Liste entfernen?\n\nDas löscht nur die Log-Einträge - History und Pending bleiben unverändert.`)) return;
      try {
        const r = await this.api('POST', '/api/jobs/cleanup-failed');
        this.showToast(`${r.deleted || 0} Failed-Jobs gelöscht`, 'ok');
        await this.loadJobs();
      } catch(e) {
        this.showToast('Cleanup fail: ' + e, 'error');
      }
    },
    async loadJobLog(id) {
      this.currentLogJob = id;
      this.currentLog = 'lädt…';
      const r = await this.api('GET', `/api/jobs/${id}/log?tail=800`);
      this.currentLog = r.log || '(leer)';
      // Nach dem Laden ans Ende scrollen (neueste Zeilen)
      this.$nextTick && this.$nextTick(() => {
        const pre = document.querySelector('.modal-log pre.log-view');
        if (pre) pre.scrollTop = pre.scrollHeight;
      });
    },
    async copyLog() {
      try {
        await navigator.clipboard.writeText(this.currentLog || '');
        this.showToast('Log in Zwischenablage kopiert', 'ok');
      } catch (e) {
        this.showToast('Kopieren fehlgeschlagen: ' + e.message, 'error');
      }
    },

    // ------------- Pending -------------
    pendingSort: 'newest',
    selectedPending: [],
    failedDownloads: [],
    retryingUrl: null,
    bulkBusy: false,

    async loadPending() {
      let items = await this.api('GET', '/api/pending');
      // Client-seitig sortieren (Server liefert nach created_at DESC)
      const sortFn = {
        'newest':         (a, b) => (b.created_at || 0) - (a.created_at || 0),
        'oldest':         (a, b) => (a.created_at || 0) - (b.created_at || 0),
        'confidence_asc': (a, b) => ((a.ai_suggestion && a.ai_suggestion.confidence) || 0)
                                  - ((b.ai_suggestion && b.ai_suggestion.confidence) || 0),
        'confidence_desc':(a, b) => ((b.ai_suggestion && b.ai_suggestion.confidence) || 0)
                                  - ((a.ai_suggestion && a.ai_suggestion.confidence) || 0),
      }[this.pendingSort] || ((a, b) => 0);
      items.sort(sortFn);
      this.pending = items;
      this.pending.forEach(p => {
        p._name = (p.ai_suggestion && p.ai_suggestion.name) && p.ai_suggestion.name !== 'Unbekannt'
                  ? p.ai_suggestion.name : '';
        p._type = (p.ai_suggestion && p.ai_suggestion.type) && p.ai_suggestion.type !== 'Unbekannt'
                  ? p.ai_suggestion.type : '';
        p._category = (p.ai_suggestion && p.ai_suggestion.category) && p.ai_suggestion.category !== 'Unbekannt'
                      ? p.ai_suggestion.category : '';
      });
      // Auswahl-State auf existierende URLs eindampfen (Items könnten weg sein)
      const urls = new Set(this.pending.map(p => p.url));
      this.selectedPending = this.selectedPending.filter(u => urls.has(u));
    },

    // ---------------- Bulk-Selection ----------------
    togglePendingSelection(url) {
      const i = this.selectedPending.indexOf(url);
      if (i >= 0) this.selectedPending.splice(i, 1);
      else this.selectedPending.push(url);
    },
    selectAllPending() {
      this.selectedPending = this.pending.map(p => p.url);
    },
    async bulkSkipPending() {
      const n = this.selectedPending.length;
      if (n === 0) return;
      if (!confirm(`${n} Pending-Items wirklich überspringen? Sie landen als '(skipped)' in der History.`)) return;
      this.bulkBusy = true;
      try {
        const r = await this.api('POST', '/api/pending/bulk-skip', { urls: this.selectedPending });
        this.showToast(`${r.skipped} Items übersprungen` + (r.errors && r.errors.length ? ` (${r.errors.length} Fehler)` : ''), 'ok');
        this.selectedPending = [];
        await this.loadPending();
      } catch(e) {
        this.showToast('Fehler: ' + e.message, 'error');
      } finally {
        this.bulkBusy = false;
      }
    },

    // ---------------- Alters-Format ----------------
    formatAge(ts) {
      if (!ts) return '';
      const sec = Math.floor(Date.now() / 1000 - ts);
      if (sec < 60) return 'gerade eben';
      if (sec < 3600) return `vor ${Math.floor(sec / 60)} min`;
      if (sec < 86400) return `vor ${Math.floor(sec / 3600)} h`;
      if (sec < 86400 * 30) return `vor ${Math.floor(sec / 86400)} Tag${sec >= 86400*2 ? 'en' : ''}`;
      const months = Math.floor(sec / (86400 * 30));
      return `vor ${months} Monat${months > 1 ? 'en' : ''}`;
    },
    ageBadgeClass(ts) {
      if (!ts) return '';
      const days = (Date.now() / 1000 - ts) / 86400;
      if (days < 1) return 'age-fresh';
      if (days < 7) return 'age-recent';
      if (days < 25) return 'age-old';
      return 'age-stale';   // > 25 Tage: bald auto-skipped (30-Tage-Limit)
    },
    async resolveItem(item, action) {
      const payload = {
        url: item.url,
        action,
        name: (item._name || (item.ai_suggestion && item.ai_suggestion.name) || '').trim(),
        type: (item._type || '').trim() || undefined,
        category: (item._category || '').trim() || undefined,
      };
      if (action === 'save') {
        if (!payload.name) { this.showToast('Name fehlt', 'error'); return; }
        if (item.content_type === 'recipe' && !payload.type) {
          this.showToast('Typ wählen', 'error'); return;
        }
        if (item.content_type === 'wedding' && !payload.category) {
          this.showToast('Kategorie wählen', 'error'); return;
        }
      }
      const r = await this.api('POST', '/api/pending', payload);
      if ((r && r.ok)) {
        this.showToast(action === 'skip' ? 'Übersprungen' : 'Gespeichert ✓');
        await this.loadPending();
        this.refreshStatus();
      } else {
        this.showToast((r && r.error) || 'Fehler', 'error');
      }
    },

    // ------------- History -------------
    async loadHistory() {
      this.history = await this.api('GET', '/api/history?limit=300');
    },
    async reanalyzeHistoryOne(item, fromJunk = false) {
      this.reanalyzingHistoryUrl = item.url;
      try {
        const r = await this.api('POST', '/api/history/reanalyze',
                                  { url: item.url, dry_run: false,
                                    auto_move: this.historyAutoMove });
        if (!r.ok) {
          this.showToast('Reanalyze fail: ' + (r.error || 'unbekannt'), 'error');
          return;
        }
        const action = r.action;
        if (action === 'moved') {
          this.showToast(`Verschoben: "${r.old.name}" → "${r.new.name}" (${r.new.target_dir})`, 'ok');
          await this.loadHistory();
          if (fromJunk) await this.loadJunkItems();
        } else if (action === 'updated') {
          this.showToast(`Aktualisiert: "${r.old.name}" → "${r.new.name}" (conf ${Math.round(r.new.confidence * 100)}%)`, 'ok');
          await this.loadHistory();
          if (fromJunk) await this.loadJunkItems();
        } else if (action === 'unchanged') {
          this.showToast('Klassifikation unverändert', 'ok');
        } else if (action === 'low_confidence') {
          this.showToast(`Niedrige Confidence (${Math.round(r.new.confidence * 100)}%), nichts geändert`, 'error');
        } else {
          this.showToast('Fehler: ' + (r.error || action), 'error');
        }
      } catch(e) {
        this.showToast('Reanalyze fail: ' + e, 'error');
      } finally {
        this.reanalyzingHistoryUrl = null;
      }
    },
    async reanalyzeHistoryAll(dry_run) {
      const moveHint = this.historyAutoMove
        ? '\n\n⚠️ Auto-Move ist aktiv - Files werden in neue Ordner verschoben!'
        : '\n\nNur DB wird aktualisiert, Files bleiben wo sie sind.';
      const msg = dry_run
        ? 'Dry-Run starten? Liest alle History-URLs neu via yt-dlp und schickt durch den AI-Provider. Zeigt nur was sich ändern WÜRDE, kein DB-/FS-Write.' + moveHint
        : 'Alle History-URLs neu analysieren? Aktualisiert die Klassifikation, wenn der neue Provider sicher ist.' + moveHint
          + '\n\nDas kann je nach History-Größe ein paar Minuten dauern.';
      if (!confirm(msg)) return;
      try {
        this.historyReanalyzing = true;
        const r = await this.api('POST', '/api/history/reanalyze-all',
                                  { dry_run, limit: 1000,
                                    auto_move: this.historyAutoMove });
        if (!r.ok) {
          this.showToast('Start fail: ' + (r.error || 'unbekannt'), 'error');
          this.historyReanalyzing = false;
          return;
        }
        this.historyReanalyzeStatus = { running: true, job_id: r.job_id, elapsed_sec: 0 };
        // Polling für Status
        if (this.historyReanalyzePollTimer) clearInterval(this.historyReanalyzePollTimer);
        this.historyReanalyzePollTimer = setInterval(() => this.pollHistoryReanalyze(), 3000);
      } catch(e) {
        this.showToast('Start fail: ' + e, 'error');
        this.historyReanalyzing = false;
      }
    },
    async loadJunkItems() {
      this.junkLoading = true;
      try {
        this.junkItems = await this.api('GET', '/api/history/junk');
      } catch(e) {
        this.showToast('Junk-Liste fail: ' + e, 'error');
      } finally {
        this.junkLoading = false;
      }
    },
    async reanalyzeJunkOnly() {
      if (!this.junkItems || !this.junkItems.items || this.junkItems.items.length === 0) return;
      const n = this.junkItems.items.length;
      const moveHint = this.historyAutoMove
        ? ' Auto-Move ist aktiv - Files werden in neue Ordner verschoben.'
        : ' Nur DB-Updates, Files bleiben.';
      if (!confirm(`${n} Junk-Items einzeln re-analysieren?${moveHint}`)) return;

      this.historyReanalyzing = true;
      let updated = 0, moved = 0, unchanged = 0, lowConf = 0, failed = 0;
      for (const j of this.junkItems.items) {
        try {
          const r = await this.api('POST', '/api/history/reanalyze',
                                    { url: j.url, dry_run: false,
                                      auto_move: this.historyAutoMove });
          if (r.action === 'moved') moved++;
          else if (r.action === 'updated') updated++;
          else if (r.action === 'unchanged') unchanged++;
          else if (r.action === 'low_confidence') lowConf++;
          else failed++;
        } catch(e) {
          failed++;
        }
      }
      this.historyReanalyzing = false;
      this.showToast(`Junk-Cleanup fertig: ${moved} moved, ${updated} updated, ${unchanged} unverändert, ${lowConf} unsicher, ${failed} fail`, 'ok');
      await this.loadHistory();
      await this.loadJunkItems();
    },
    async cleanupAllJunk() {
      // One-Click: Junk finden + sofort aufräumen mit der aktuellen Auto-Move-Einstellung.
      const moveWarn = this.historyAutoMove
        ? '\n\n⚠️ Auto-Move ist AN - Files werden physisch in andere Ordner verschoben.'
        : '\n\nAuto-Move ist AUS - nur die DB-Namen werden aktualisiert, Files bleiben wo sie sind.';
      if (!confirm('Junk-Cleanup starten?' + moveWarn
                   + '\n\nDas läuft in 2 Schritten:\n'
                   + '1. Verdächtige Items finden (Unbekannt, Auto-Fallback-Namen, etc.)\n'
                   + '2. Jedes mit OpenAI neu klassifizieren (yt-dlp + KI pro Item)\n\n'
                   + 'Je nach Anzahl 1-10 Minuten.')) return;

      this.historyReanalyzing = true;
      this.junkLoading = true;
      try {
        // Schritt 1: Junk-Liste holen
        this.junkItems = await this.api('GET', '/api/history/junk');
        const n = this.junkItems.items.length;
        if (n === 0) {
          this.showToast('Kein Junk gefunden - History sieht sauber aus.', 'ok');
          return;
        }
        this.showToast(`${n} verdächtige Items gefunden - starte Reanalyze…`, 'ok');

        // Schritt 2: Alle nacheinander re-analysieren
        let updated = 0, moved = 0, unchanged = 0, lowConf = 0, failed = 0;
        const details = [];
        for (const j of this.junkItems.items) {
          try {
            const r = await this.api('POST', '/api/history/reanalyze',
                                      { url: j.url, dry_run: false,
                                        auto_move: this.historyAutoMove });
            if (r.action === 'moved') {
              moved++;
              details.push({ from: r.old.name, to: r.new && r.new.name });
            } else if (r.action === 'updated') {
              updated++;
              details.push({ from: r.old.name, to: r.new && r.new.name });
            } else if (r.action === 'unchanged') unchanged++;
            else if (r.action === 'low_confidence') lowConf++;
            else failed++;
          } catch(e) {
            failed++;
          }
        }

        this.showToast(
          `Cleanup fertig: ${moved} verschoben · ${updated} umbenannt · ${unchanged} ok · ${lowConf} unsicher · ${failed} fail`,
          'ok',
        );

        // Summary-Card mit Details anzeigen (nutzt das bestehende Status-Panel)
        this.historyReanalyzeStatus = {
          running: false,
          summary: {
            total: n, updated, moved, unchanged, low_confidence: lowConf,
            failed, dry_run: false, auto_move: this.historyAutoMove,
            details: details.slice(0, 50),
          },
        };

        await this.loadHistory();
        await this.loadJunkItems();
      } catch(e) {
        this.showToast('Cleanup fail: ' + e, 'error');
      } finally {
        this.historyReanalyzing = false;
        this.junkLoading = false;
      }
    },
    async pollHistoryReanalyze() {
      // Wir nutzen den /api/pending/reanalyze/progress Endpoint -
      // der trackt 'reanalyze'-kind Jobs (gleicher Kind, wir teilen den Slot)
      try {
        const r = await this.api('GET', '/api/pending/reanalyze/progress');
        if (r.running) {
          this.historyReanalyzeStatus = { running: true, ...r };
        } else {
          // Fertig
          this.historyReanalyzeStatus = { running: false, last: r.last,
                                          summary: (r.last && r.last.summary) || null };
          this.historyReanalyzing = false;
          clearInterval(this.historyReanalyzePollTimer);
          this.historyReanalyzePollTimer = null;
          // History neu laden damit die Updates sichtbar sind
          await this.loadHistory();
        }
      } catch(e) {
        // Poll-Fehler ignorieren, beim nächsten Tick neuer Versuch
      }
    },

    // ------------- Config -------------
    // ════════════════════════════════════════════════════════════════════
    // Benutzer-Verwaltung (alle aktiven Konten mit Vollzugriff)
    // ════════════════════════════════════════════════════════════════════
    async loadUsers() {
      this.users.loading = true;
      try {
        const r = await this.api('GET', '/api/users');
        if (r) this.users.list = r.users || [];
      } finally {
        this.users.loading = false;
      }
    },

    async createUser() {
      const f = this.users.addForm;
      if (!f.username || !f.password) {
        this.showToast('Username und Passwort pflichtig', 'err');
        return;
      }
      this.users.creating = true;
      try {
        const r = await this.api('POST', '/api/users', {
          username: f.username.trim(),
          password: f.password,
        });
        if (r && r.ok) {
          this.showToast(`✓ Benutzer '${r.username}' angelegt`);
          this.users.showAdd = false;
          this.users.addForm = { username: '', password: '' };
          await this.loadUsers();
        }
      } finally {
        this.users.creating = false;
      }
    },

    async changeUserPassword(u) {
      const pw = window.prompt(`Neues Passwort für „${u.username}":\n\n(mindestens 8 Zeichen)`);
      if (!pw) return;
      if (pw.length < 8) { this.showToast('Mindestens 8 Zeichen', 'err'); return; }
      const r = await this.api('PATCH', `/api/users/${u.id}`, { password: pw });
      if (r && r.ok) this.showToast('✓ Passwort geändert');
    },


    async setUserDisabled(u, disabled) {
      const r = await this.api('PATCH', `/api/users/${u.id}`, { disabled });
      if (r && r.ok) {
        this.showToast(disabled ? `✓ '${u.username}' deaktiviert` : `✓ '${u.username}' aktiviert`);
        await this.loadUsers();
      } else {
        await this.loadUsers();
      }
    },

    async deleteUser(u) {
      if (!confirm(`Benutzer „${u.username}" wirklich löschen?\n\nDer Datensatz wird endgültig entfernt.`)) return;
      const r = await this.api('DELETE', `/api/users/${u.id}`);
      if (r && r.ok) {
        this.showToast(`✓ Benutzer '${r.deleted}' gelöscht`);
        await this.loadUsers();
      }
    },

    async loadConfig() {
      const cfg = await this.api('GET', '/api/config');
      // Defaults absichern damit Alpine-Bindings nicht meckern
      cfg.web ||= {};
      cfg.paths ||= {};
      cfg.mail ||= {};
      cfg.mail.recipe ||= {};
      cfg.mail.wedding ||= {};
      cfg.ai ||= {};
      cfg.ai.openai ||= { api_key: '', model: 'gpt-4o-mini', base_url: '', timeout: 30 };
      if (cfg.ai.auto_translate === undefined) cfg.ai.auto_translate = true;
      cfg.pdf ||= {};
      if (cfg.pdf.auto_rotate === undefined) cfg.pdf.auto_rotate = true;
      if (cfg.pdf.use_tesseract_osd === undefined) cfg.pdf.use_tesseract_osd = true;
      if (cfg.pdf.min_text_chars === undefined) cfg.pdf.min_text_chars = 20;
      if (cfg.pdf.text_dominance === undefined) cfg.pdf.text_dominance = 0.60;
      if (cfg.pdf.osd_min_confidence === undefined) cfg.pdf.osd_min_confidence = 1.0;
      if (cfg.pdf.max_osd_pages === undefined) cfg.pdf.max_osd_pages = 100;
      if (cfg.pdf.use_ocr_vote === undefined) cfg.pdf.use_ocr_vote = true;
      if (cfg.pdf.remove_blank_pages === undefined) cfg.pdf.remove_blank_pages = true;
      if (cfg.pdf.auto_crop === undefined) cfg.pdf.auto_crop = true;
      if (cfg.pdf.deskew_scans === undefined) cfg.pdf.deskew_scans = true;
      if (cfg.pdf.ocr_scans === undefined) cfg.pdf.ocr_scans = true;
      if (cfg.pdf.improve_contrast === undefined) cfg.pdf.improve_contrast = true;
      if (cfg.pdf.sharpen_scans === undefined) cfg.pdf.sharpen_scans = true;
      if (cfg.pdf.scan_dpi === undefined) cfg.pdf.scan_dpi = 300;
      if (cfg.pdf.ocr_language === undefined) cfg.pdf.ocr_language = 'deu+eng';
      if (cfg.pdf.keep_original === undefined) cfg.pdf.keep_original = true;
      cfg.ytdlp ||= {};
      cfg.webhooks ||= [];
      cfg.einkauf ||= { api_url: '', auto_consolidate: true };
      this.config = cfg;
      // Pro-Pair-Args ins UI laden
      this.recipeTypes = cfg.recipe_types || this.recipeTypes;
      this.weddingCategories = cfg.wedding_categories || this.weddingCategories;
      this.loadSchedule();
      this.loadMaintenance();  // Wartungs-Stats parallel
    },
    async loadMaintenance() {
      this.maintBusy = true;
      try {
        const [logs, backups] = await Promise.all([
          this.api('GET', '/api/config/logs/stats'),
          this.api('GET', '/api/config/backups/list'),
        ]);
        this.maintenance = { logs, backups: backups.tiers || {} };
      } catch(e) {
        // Endpoint evtl. nicht da (alte Version): kein crash
      } finally {
        this.maintBusy = false;
      }
    },
    async runLogCleanup() {
      if (!confirm('Logs älter als '
                   + (this.config.paths.log_retention_days || 30)
                   + ' Tage jetzt löschen?')) return;
      this.maintBusy = true;
      this.maintenanceOutput = '';
      try {
        const r = await this.api('POST', '/api/config/logs/cleanup');
        this.maintenanceOutput = (r.stdout || '') + (r.stderr ? '\n[STDERR]\n' + r.stderr : '');
        this.showToast(r.ok ? 'Log-Cleanup ok' : 'Fehler im Cleanup', r.ok ? 'ok' : 'error');
        await this.loadMaintenance();
      } catch(e) {
        this.showToast('Cleanup-Fehler: ' + e, 'error');
      } finally {
        this.maintBusy = false;
      }
    },
    async saveConfig() {
      // Pfad-Werte trimmen damit nicht versehentlich Leerzeichen reinrutschen
      // (führt sonst zu 'path does not exist' beim healthz/deep)
      if (this.config.paths) {
        ['recipe_dir', 'wedding_dir', 'temp_dir', 'logs_dir'].forEach(k => {
          if (typeof this.config.paths[k] === 'string') {
            this.config.paths[k] = this.config.paths[k].trim();
          }
        });
      }
      if (this.config.ytdlp) {
        ['binary', 'cookies_file'].forEach(k => {
          if (typeof this.config.ytdlp[k] === 'string') {
            this.config.ytdlp[k] = this.config.ytdlp[k].trim();
          }
        });
      }
      await this.api('PUT', '/api/config', this.config);
      await this.api('POST', '/api/config/reload', null);
      this.showToast('Konfiguration gespeichert');
    },

    // ------------- Tests -------------
    testing: {
      mail_recipe: false, mail_wedding: false,
      openai: false,
      paths: false, ytdlp: false,
      schedule_preview: false, schedule_save: false,
      webhook: -1,   // Index des gerade getesteten Webhook (-1 = keiner)
    },
    testResults: {},
    schedule: { scraper: { oncalendar: '', next_run: null } },
    scheduleEdit: { scraper: '' },
    schedulePreview: null,

    scraperProgress: null,
    reanalyzeProgress: null,
    _progressTimer: null,

    isTesting(key) { return this.testing[key] === true; },
    testResult(key) { return this.testResults[key] || null; },
    testResultMsg(key) {
      var r = this.testResults[key];
      if (!r) return '';
      return r.message || r.error || '';
    },
    pathsTestEntries() {
      var r = this.testResults.paths;
      if (!r || !r.paths) return [];
      return Object.keys(r.paths).map(function(k) {
        return { key: k, info: r.paths[k] };
      });
    },

    async runTest(key, endpoint, body = null) {
      this.testing[key] = true;
      this.testResults[key] = null;
      try {
        const r = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: body ? JSON.stringify(body) : null,
        });
        const data = await r.json();
        this.testResults[key] = data;
        if (data.ok) {
          this.showToast(data.message || 'Test OK ✓', 'ok');
        } else {
          this.showToast('Test fehlgeschlagen: ' + (data.error || 'unbekannt'), 'error');
        }
      } catch (e) {
        this.testResults[key] = { ok: false, error: String(e) };
        this.showToast('Test-Fehler: ' + e, 'error');
      } finally {
        this.testing[key] = false;
      }
    },
    testMail(account) {
      this.runTest('mail_' + account, '/api/test/mail', { account });
    },
    async testOpenAI() {
      // Defensiv: testing-state immer auf false zurücksetzen damit der Button
      // nicht 'stuck' bleibt nach einem alten Fehler
      this.testing.openai = false;

      // Aktuelle UI-Werte mitschicken, damit der User nicht erst speichern muss.
      const oai = (this.config && this.config.ai && this.config.ai.openai) || {};
      const body = {
        api_key: oai.api_key || '',
        model: oai.model || '',
        base_url: oai.base_url || '',
      };

      // Frühe Sicherheitsprüfung damit der User sofort sieht wenn der Key
      // noch maskiert ist (also nie wirklich getippt wurde)
      if (!body.api_key) {
        this.testResults.openai = { ok: false, error: 'Kein API-Key im Feld - bitte eintragen' };
        this.showToast('Kein API-Key im Feld', 'error');
        return;
      }
      if (body.api_key.startsWith('•')) {
        // Gespeicherter Key wird ••• gemaskt zurückgegeben. Backend liest dann
        // aus Config. Wir senden den ••• mit und das Backend behandelt das.
        // Trotzdem warnen wenn die Maske kommt aber nichts gespeichert ist.
      }

      this.testing.openai = true;
      this.testResults.openai = null;
      try {
        const r = await this.api('POST', '/api/test/openai', body);
        const result = r || { ok: false, error: 'Leere Antwort vom Server' };
        this.testResults.openai = result;
        if (result.ok) {
          this.showToast('OpenAI: ' + (result.message || 'Verbindung ok'), 'ok');
        } else {
          this.showToast('OpenAI-Test fail: ' + (result.error || 'unbekannt'), 'error');
        }
      } catch(e) {
        this.testResults.openai = { ok: false, error: String(e) };
        this.showToast('Test-Aufruf fehlgeschlagen: ' + e, 'error');
      } finally {
        this.testing.openai = false;
      }
    },
    testPaths() { this.runTest('paths', '/api/test/paths'); },
    testYtdlp() { this.runTest('ytdlp', '/api/test/ytdlp'); },

    // ---------------- Webhooks ----------------
    addWebhook() {
      if (!this.config.webhooks) this.config.webhooks = [];
      this.config.webhooks.push({
        name: '', url: '', enabled: true,
        events: ['scraper_done', 'job_failed'],
      });
    },
    removeWebhook(idx) {
      if (!confirm('Webhook löschen? (Wird beim Speichern endgültig entfernt.)')) return;
      this.config.webhooks.splice(idx, 1);
    },
    toggleWebhookEvent(idx, ev, checked) {
      const hook = this.config.webhooks[idx];
      if (!hook.events) hook.events = [];
      const i = hook.events.indexOf(ev);
      if (checked && i < 0) hook.events.push(ev);
      else if (!checked && i >= 0) hook.events.splice(i, 1);
    },
    async testWebhook(idx) {
      const hook = this.config.webhooks[idx];
      if (!hook || !hook.url || hook.url === '••••••••') {
        this.showToast('URL leer oder noch nicht gespeichert', 'error');
        return;
      }
      this.testing.webhook = idx;
      this.testResults.webhook = null;
      try {
        const r = await this.api('POST', '/api/test/webhook',
                                 { name: hook.name || 'test', url: hook.url });
        this.testResults.webhook = { idx, ...r };
        this.showToast(r.ok ? 'Webhook-Test ok' : ('Test fail: ' + r.error), r.ok ? 'ok' : 'error');
      } catch(e) {
        this.testResults.webhook = { idx, ok: false, error: String(e) };
        this.showToast('Test fail: ' + e, 'error');
      } finally {
        this.testing.webhook = -1;
      }
    },

    // ------------- Schedule / Timer -------------
    async loadSchedule() {
      try {
        this.schedule = await this.api('GET', '/api/schedule');
        this.scheduleEdit.scraper = this.schedule.scraper.oncalendar || '';
      } catch(e) {}
    },
    async previewSchedule() {
      this.testing.schedule_preview = true;
      try {
        this.schedulePreview = await this.api('POST', '/api/schedule/preview', {
          scraper: this.scheduleEdit.scraper,
        });
      } finally {
        this.testing.schedule_preview = false;
      }
    },
    async saveSchedule() {
      this.testing.schedule_save = true;
      try {
        const r = await this.api('PUT', '/api/schedule', {
          scraper: this.scheduleEdit.scraper,
        });
        if (r && r.ok) {
          this.showToast('Schedule gespeichert ✓');
          await this.loadSchedule();
        } else {
          this.showToast('Speichern fehlgeschlagen: ' + (r && r.error || ''), 'error');
        }
      } finally {
        this.testing.schedule_save = false;
      }
    },

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

    openLocalBrowser(initialPath, callback, title) {
      this.browser.mode = 'local';
      this.browser.callback = callback;
      this.browser.title = title || 'Lokales Verzeichnis wählen';
      this.browser.show = true;
      this.loadBrowserPath(initialPath || '/mnt');
    },
    async loadBrowserPath(path) {
      this.browser.loading = true;
      // State sofort leeren - sonst zeigt das Modal bei einem API-Fehler
      // noch die Daten vom vorherigen (z.B. lokalen) Browse-Vorgang.
      this.browser.entries = [];
      this.browser.suggestedRoots = [];
      this.browser.parent = null;
      this.browser.isRoot = false;
      this.browser.currentPath = path || '';
      try {
        const endpoint = '/api/browse/local';
        const r = await this.api('GET', endpoint + '?path=' + encodeURIComponent(path || ''));
        if (r) {
          this.browser.currentPath = r.path;
          this.browser.entries = r.entries || [];
          this.browser.parent = r.parent;
          this.browser.isRoot = r.is_root || false;
          this.browser.suggestedRoots = r.suggested_roots || [];
        }
      } catch(e) {
        this.showToast('Browse-Fehler: ' + e, 'error');
      } finally {
        this.browser.loading = false;
      }
    },
    browserPick() {
      if (this.browser.callback) this.browser.callback(this.browser.currentPath);
      this.browser.show = false;
    },
    browserCancel() {
      this.browser.show = false;
      this.browser.callback = null;
    },
    async browserMkdir() {
      const name = prompt('Name des neuen Ordners?');
      if (!name) return;
      const newPath = (this.browser.currentPath.replace(/\/$/, '')) + '/' + name;
      try {
        await this.api('POST', '/api/browse/local/mkdir', { path: newPath });
        this.showToast('Ordner erstellt');
        this.loadBrowserPath(this.browser.currentPath);
      } catch(e) {
        this.showToast('Anlegen fehlgeschlagen', 'error');
      }
    },

        // ------------- Pending Reanalyze -------------
    reanalyzing: {},
    reanalyzingAll: false,
    async reanalyzeOne(item) {
      this.reanalyzing[item.url] = true;
      try {
        const r = await this.api('POST', '/api/pending/reanalyze', { url: item.url });
        if (r && r.ok) {
          if (r.action === 'auto_saved') {
            this.showToast('Automatisch einsortiert: ' + (r.analysis && r.analysis.name));
          } else {
            this.showToast('Neuer Vorschlag: ' + (r.analysis && r.analysis.name) + ' (' + Math.round((r.analysis && r.analysis.confidence || 0) * 100) + '%)');
          }
          await this.loadPending();
          await this.refreshStatus();
        } else {
          this.showToast('Reanalyze: ' + (r && r.error || 'Fehler'), 'error');
        }
      } catch(e) {} finally {
        delete this.reanalyzing[item.url];
      }
    },

    // ---------------- Failed Downloads (Email Recovery) ----------------
    async loadFailedDownloads() {
      try {
        this.failedDownloads = await this.api('GET', '/api/pending/failed') || [];
      } catch(e) {
        // Endpoint evtl. nicht da (alte Backend-Version)
        this.failedDownloads = [];
      }
    },
    async retryFailed(url) {
      this.retryingUrl = url;
      try {
        const r = await this.api('POST', '/api/pending/failed/'
                                   + encodeURIComponent(url) + '/retry');
        if (r && r.ok) {
          this.showToast('Counter zurückgesetzt - URL wird beim nächsten Mail-Sync neu versucht', 'ok');
          await this.loadFailedDownloads();
        } else {
          this.showToast('Reset fehlgeschlagen', 'error');
        }
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      } finally {
        this.retryingUrl = null;
      }
    },
    async clearAllFailed() {
      const n = this.failedDownloads.length;
      if (!confirm('Alle ' + n + ' Failure-Counter zurücksetzen? Die URLs werden beim nächsten Mail-Sync nochmal versucht (sofern noch in einer Mail vorhanden).')) return;
      try {
        const r = await this.api('POST', '/api/pending/failed/clear-all');
        this.showToast((r && r.cleared || n) + ' Counter zurückgesetzt', 'ok');
        await this.loadFailedDownloads();
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      }
    },

    async reanalyzeAll() {
      if (!confirm('Alle ' + this.pending.length + ' Pending-Items neu analysieren? Das läuft im Hintergrund als Job.')) return;
      try {
        const r = await this.api('POST', '/api/pending/reanalyze-all', {});
        if (r && r.job_id) {
          this.showToast('Reanalyze-Job gestartet (#' + r.job_id + ')');
          this.refreshStatus();
        }
      } catch(e) {}
    },

    // ------------- Schedule Helpers -------------
    SCHEDULE_PRESETS_SCRAPER: [
      { label: 'Alle 15 Min', value: '*:0/15' },
      { label: 'Alle 30 Min', value: '*:0/30' },
      { label: 'Stündlich',   value: 'hourly' },
      { label: 'Alle 2 Std',  value: '*:0/120' },
      { label: 'Alle 6 Std',  value: '00,06,12,18:00' },
      { label: 'Täglich 08:00', value: '*-*-* 08:00:00' },
    ],

    humanCron(cron) {
      if (!cron) return '—';
      const map = {
        'hourly': 'jede Stunde',
        'daily': 'täglich um Mitternacht',
        'weekly': 'wöchentlich (Mo 00:00)',
        'monthly': 'monatlich (1. um 00:00)',
      };
      if (map[cron]) return map[cron];
      let m;
      if ((m = cron.match(/^\*:0\/(\d+)$/))) return 'alle ' + m[1] + ' Minuten';
      if ((m = cron.match(/^\*-\*-\*\s+(\d\d):(\d\d):(\d\d)$/))) return 'täglich um ' + m[1] + ':' + m[2];
      if ((m = cron.match(/^(\w+)\.\.(\w+)\s+\*-\*-\*\s+(\d\d):(\d\d):(\d\d)$/)))
        return m[1] + '-' + m[2] + ' um ' + m[3] + ':' + m[4];
      if ((m = cron.match(/^(\w+)\s+\*-\*-\*\s+(\d\d):(\d\d):(\d\d)$/)))
        return m[1] + ' um ' + m[2] + ':' + m[3];
      return cron;   // custom
    },

    // ------------- History bearbeiten -------------
    editingItem: null,
    openEditItem(item) {
      this.editingItem = {
        url: item.url,
        original: item,
        name: item.name || '',
        type: item.type || '',          // wird aus target_dir abgeleitet wenn möglich
        category: item.category || '',
        content_type: item.content_type,
      };
      // Versuche Typ/Kategorie aus target_dir zu extrahieren
      if (item.target_dir) {
        const parts = item.target_dir.split('/').filter(Boolean);
        // /mnt/rezepte/Typ/Kategorie/Name oder /mnt/hochzeit/Kategorie/Name
        if (item.content_type === 'recipe' && parts.length >= 4) {
          this.editingItem.type = parts[parts.length - 3];
          this.editingItem.category = parts[parts.length - 2];
        } else if (item.content_type === 'wedding' && parts.length >= 3) {
          this.editingItem.category = parts[parts.length - 2];
        }
      }
    },
    cancelEdit() { this.editingItem = null; },
    async saveEditItem() {
      const e = this.editingItem;
      if (!e.name.trim()) { this.showToast('Name fehlt', 'error'); return; }
      const payload = {
        url: e.url,
        name: e.name.trim(),
        type: e.type ? e.type.trim() : null,
        category: e.category ? e.category.trim() : null,
      };
      try {
        const r = await this.api('POST', '/api/history/edit', payload);
        if (r && r.ok) {
          this.showToast(r.action === 'noop' ? 'Keine Änderung' : 'Gespeichert ✓');
          this.editingItem = null;
          await this.loadHistory();
        } else {
          this.showToast(r && r.error || 'Fehler', 'error');
        }
      } catch (e) { /* api zeigt schon Fehler */ }
    },
    async deleteItem(item) {
      if (!confirm('Wirklich löschen? Datei + Ordner werden entfernt.')) return;
      try {
        const r = await this.api('POST', '/api/history/delete', { url: item.url });
        if (r && r.ok) {
          this.showToast('Gelöscht');
          await this.loadHistory();
        }
      } catch(e) {}
    },

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

    // ── Stoppuhr pro Schritt ─────────────────────────────────────────
    startStepTimer(step) {
      if (!step || !step.timer_seconds) return;
      // Falls vorher schon ein Interval drauf war: aufräumen
      this.stopStepTimer(step, { silent: true });
      const id = step.id;
      const state = {
        status: 'running',     // 'idle' | 'running' | 'done'
        remaining: step.timer_seconds,
        intervalId: null,
        running: true,         // CSS hint
      };
      this.timers[id] = state;
      state.intervalId = setInterval(() => {
        state.remaining -= 1;
        if (state.remaining <= 0) {
          state.remaining = 0;
          state.status = 'done';
          state.running = false;
          clearInterval(state.intervalId);
          state.intervalId = null;
          this._playTimerDoneSound();
          this.showToast('⏰ Timer fertig: ' + (step.instruction || '').slice(0, 60), 'ok');
        }
        // Alpine reactivity: timers selber neu zuweisen erzwingt Re-Render
        this.timers = { ...this.timers, [id]: { ...state } };
      }, 1000);
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
      params.set('limit', f.limit);
      params.set('offset', f.offset);
      return params.toString();
    },

    // ── Recipe-Liste + Facets ─────────────────────────────────────────
    async loadRecipes() {
      // Bei normalem loadRecipes-Aufruf: offset zurücksetzen → wir starten
      // immer beim Anfang. Infinite-Scroll nutzt loadMoreRecipes(), das
      // den offset on-the-fly berechnet ohne filters.offset zu mutieren.
      this.recipes.filters.offset = 0;
      this.recipes.loading = true;
      try {
        const r = await this.api('GET', '/api/recipes?' + this._buildRecipeQuery());
        if (!r) return;
        this.recipes.items = r.items || [];
        this.recipes.total = r.total || 0;
        this.recipes.searchMeta = r.search_meta || { corrected: false, original: '', query: '', suggestion: '' };
        this.recipes.extractionRunning = !!r.extraction_running;
        this._scheduleExtractionPoll();
        this.loadFacets();   // Facetten-Counts an aktuelle Filter anpassen
      } finally {
        this.recipes.loading = false;
      }
    },

    async loadFacets() {
      // Filter mitschicken → cross-gefilterte Counts. limit/offset ignoriert die Route.
      const r = await this.api('GET', '/api/recipes/facets?' + this._buildRecipeQuery());
      if (!r) return;
      this.recipes.facets = r;
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
        search: '', type: '', category: '', tag_ids: [], ingredients: [],
        ingredients_status: '', verified: '', favorite_only: this.page === 'favorites', min_rating: 0,
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
      return n;
    },

    toggleIngredientFilter(canonicalName) {
      const arr = this.recipes.filters.ingredients;
      const i = arr.indexOf(canonicalName);
      if (i >= 0) arr.splice(i, 1); else arr.push(canonicalName);
      this.recipes.filters.offset = 0;
      this.loadRecipes();
    },

    // Zutaten-Chip-Liste filtern: Suche im Display-Name, Limit 60
    // (sonst rendert bei vielen Rezepten 200+ Chips → unbenutzbar).
    // Bereits ausgewählte werden eh oben separat angezeigt, hier ausschließen.
    filteredIngredientFacets() {
      const all = this.recipes.facets.ingredients || [];
      const selected = new Set(this.recipes.filters.ingredients);
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

    // Getter für das x-show im Template (würde Methodaufruf jedes Render
    // triggern; einfaches Property reicht)
    get ingredientFacetsLimited() {
      return this.recipes._ingFacetsLimited;
    },

    async syncRecipes() {
      this.showToast('Synchronisiere…');
      const r = await this.api('POST', '/api/recipes/sync');
      if (!r) return;
      this.showToast(`✓ ${r.scanned || 0} gescannt, ${r.added || 0} neu`);
      await this.loadRecipes();
      await this.loadFacets();
    },

    // ── Extraction-Polling ────────────────────────────────────────────
    _scheduleExtractionPoll() {
      // Idempotent: einen Timer für die Background-Extraction-Status-Updates
      if (this.recipes._pollTimer) return;
      const tick = async () => {
        try {
          const s = await this.api('GET', '/api/recipes/extraction/status');
          if (!s) return;
          const wasRunning = this.recipes.extractionRunning;
          this.recipes.extractionRunning = !!s.running;
          this.recipes.extractionStats = s.stats || {};
          this.recipes.extractionPending = s.stats?.pending || 0;
          if (!s.running) {
            // Worker fertig — Liste + Facets nachladen damit Zutaten-Filter
            // jetzt belegt ist, und Polling stoppen
            clearInterval(this.recipes._pollTimer);
            this.recipes._pollTimer = null;
            if (wasRunning && this.page === 'recipes') {
              this.loadRecipes();
              this.loadFacets();
            }
          }
        } catch(e) {}
      };
      tick();  // sofort einmal
      if (this.recipes._pollTimer) clearInterval(this.recipes._pollTimer);
      this.recipes._pollTimer = setInterval(tick, 5000);
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
    // Cache wird beim Modal-Close NICHT geleert — der nächste openRecipe
    // dürfte dieselbe ID sein wenn der User schnell wieder klickt.
    _detailPrefetch: new Map(),
    prefetchRecipeDetail(id) {
      if (this._detailPrefetch.has(id)) return;  // already fetched
      this._detailPrefetch.set(id, this.api('GET', '/api/recipes/' + id));
      // Cache nach 30s expirieren damit stale Daten nicht ewig leben
      setTimeout(() => this._detailPrefetch.delete(id), 30000);
    },

    async openRecipe(id) {
      this.recipeDetail.show = true;
      this.recipeDetail.data = null;
      this.recipeDetail.newTag = '';
      this.recipeDetail.multiplier = 1;
      this.recipeDetail.cookMode = false;
      this.recipeDetail.tab = 'info';
      this.recipeDetail.editingIngredients = false;
      this.recipeDetail.editIngs = [];
      this.recipeDetail.savingIngredients = false;
      this.recipeDetail.extracting = false;
      this.recipeDetail.verifying = false;
      this.recipeDetail.rescraping = false;
      // Prefetched-Promise nutzen falls da, sonst fresh fetch
      const cached = this._detailPrefetch.get(id);
      const r = cached ? await cached : await this.api('GET', '/api/recipes/' + id);
      this._detailPrefetch.delete(id);  // einmalig konsumieren
      if (r) this.recipeDetail.data = r;
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
      try {
        this._wakeLock = await navigator.wakeLock.request('screen');
        this.recipeDetail.wakeLockActive = true;
        // Browser kann Lock implizit beenden (z.B. Tab im Hintergrund) —
        // Listener informiert uns damit der UI-Indikator stimmt.
        this._wakeLock.addEventListener('release', () => {
          this.recipeDetail.wakeLockActive = false;
        });
      } catch (e) {
        // Verweigerung (User-Gesture fehlt o.ä.) — kein Showstopper
        this.recipeDetail.wakeLockActive = false;
      }
    },

    async _releaseWakeLock() {
      this.recipeDetail.wakeLockActive = false;
      if (this._wakeLock) {
        try { await this._wakeLock.release(); } catch (e) {}
        this._wakeLock = null;
      }
    },

    closeRecipeDetail() {
      // Laufende Step-Timer aufräumen — sonst tickern sie im Hintergrund weiter
      // und beepen evtl. nach Modal-close.
      Object.values(this.timers).forEach(t => {
        if (t && t.intervalId) clearInterval(t.intervalId);
      });
      this.timers = {};
      // Wake-Lock + Cook-Mode resetten — sonst hält der Lock weiter und der
      // nächste open würde mit angeschaltetem Cook-Mode starten
      this._releaseWakeLock();
      this.recipeDetail.cookMode = false;
      this.recipeDetail.show = false;
      this.recipeDetail.data = null;
      // Edit-State + ephemerale Loading-States ZWINGEND clearen
      this.recipeDetail.editingIngredients = false;
      this.recipeDetail.editIngs = [];
    },

    async addTagToRecipe() {
      const name = (this.recipeDetail.newTag || '').trim();
      if (!name || !this.recipeDetail.data) return;
      // Nur User-Tags durchreichen — Backend recipe_tags_set ersetzt
      // ohnehin nur auto=0; Auto-Tags bleiben.
      const userTags = (this.recipeDetail.data.tags || [])
        .filter(t => !t.auto)
        .map(t => t.name);
      if (userTags.includes(name)) { this.recipeDetail.newTag = ''; return; }
      userTags.push(name);
      const r = await this.api('PUT', `/api/recipes/${this.recipeDetail.data.id}/tags`,
                                { tags: userTags });
      if (r && r.ok) {
        // Re-fetch komplette Tag-Liste (User + Auto)
        const fresh = await this.api('GET', `/api/recipes/${this.recipeDetail.data.id}`);
        if (fresh) this.recipeDetail.data.tags = fresh.tags;
        this.recipeDetail.newTag = '';
        this.loadFacets();
      }
    },

    async removeTagFromRecipe(tagName) {
      if (!this.recipeDetail.data) return;
      // Auto-Tags lassen sich nicht entfernen (× ist im UI eh nicht da).
      // Defensiv: falls doch aufgerufen, hier abfangen.
      const tag = (this.recipeDetail.data.tags || []).find(t => t.name === tagName);
      if (!tag || tag.auto) return;
      const userTags = (this.recipeDetail.data.tags || [])
        .filter(t => !t.auto && t.name !== tagName)
        .map(t => t.name);
      const r = await this.api('PUT', `/api/recipes/${this.recipeDetail.data.id}/tags`,
                                { tags: userTags });
      if (r && r.ok) {
        const fresh = await this.api('GET', `/api/recipes/${this.recipeDetail.data.id}`);
        if (fresh) this.recipeDetail.data.tags = fresh.tags;
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

    async extractIngredients() {
      if (!this.recipeDetail.data || this.recipeDetail.extracting) return;
      this.recipeDetail.extracting = true;
      try {
        const id = this.recipeDetail.data.id;
        const r = await this.api('POST', `/api/recipes/${id}/extract`);
        if (r && r.ok) {
          this.showToast(`✓ ${r.count || 0} Zutaten extrahiert`);
          // Frisch laden um Zutatenliste im Modal zu aktualisieren
          const fresh = await this.api('GET', '/api/recipes/' + id);
          if (fresh) this.recipeDetail.data = fresh;
          this.loadFacets();
        }
      } finally {
        this.recipeDetail.extracting = false;
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
        dl.innerHTML = this.knownIngredients
          .map(ki => `<option value="${(ki.display_name || '').replace(/"/g, '&quot;')}">`)
          .join('');
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
      const id = this.recipeDetail.data?.id;
      if (!id) return;
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
        const r = await this.api('PUT', `/api/recipes/${id}/ingredients`,
                                  { ingredients: cleaned });
        if (r && r.ok) {
          // Server returnt die kanonisch verarbeitete Liste — UI darauf
          // aktualisieren statt den lokalen Working-Buffer zu nutzen
          this.recipeDetail.data.ingredients = r.ingredients;
          // Tags könnten sich geändert haben (Diät-Tags Recompute) — frisch laden
          const fresh = await this.api('GET', '/api/recipes/' + id);
          if (fresh) this.recipeDetail.data = fresh;
          this.recipeDetail.editingIngredients = false;
          this.recipeDetail.editIngs = [];
          this.showToast(`✓ ${cleaned.length} Zutaten gespeichert`);
          this.loadFacets();
        }
      } finally {
        this.recipeDetail.savingIngredients = false;
      }
    },

    // Nährwerte für das aktuelle Rezept berechnen (KI-Single-Call).
    // ⚡ Button im Detail-Modal — funktioniert sowohl für Erst-Berechnung
    // als auch für Recompute (z.B. nach manuellem Zutaten-Edit).
    async computeNutrition() {
      const id = this.recipeDetail.data?.id;
      if (!id || this.recipeDetail.computingNutrition) return;
      const ingCount = this.recipeDetail.data?.ingredients?.length || 0;
      if (ingCount < 3) {
        this.showToast('Mindestens 3 Zutaten nötig', 'err');
        return;
      }
      this.recipeDetail.computingNutrition = true;
      try {
        const r = await this.api('POST', `/api/recipes/${id}/nutrition`);
        if (r && r.ok) {
          // In-place die data-Felder updaten damit Modal sofort die Werte zeigt
          this.recipeDetail.data.calories_per_serving = r.calories;
          this.recipeDetail.data.protein_g = r.protein_g;
          this.recipeDetail.data.carbs_g = r.carbs_g;
          this.recipeDetail.data.fat_g = r.fat_g;
          this.showToast(`✓ ~${r.calories} kcal/Portion`);
        }
      } finally {
        this.recipeDetail.computingNutrition = false;
      }
    },

    // Print-View in neuem Tab öffnen — Browser-Cmd+P speichert als PDF.
    // Print-View ist eine eigenständige, auth-required HTML-Route mit
    // print-optimiertem Inline-CSS (@media print).
    printRecipe() {
      const id = this.recipeDetail.data?.id;
      if (!id) return;
      window.open(`/recipe/${id}/print`, '_blank', 'noopener,noreferrer');
    },

    // Signierten Share-Link erstellen + in Clipboard kopieren.
    // 30 Tage gültig. Empfänger braucht keinen Login, sieht nur das Rezept.
    async shareRecipe() {
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
    async toggleVerified(verified) {
      const id = this.recipeDetail.data?.id;
      if (!id || this.recipeDetail.verifying) return;
      this.recipeDetail.verifying = true;
      try {
        const r = await this.api('POST',
          `/api/recipes/${id}/verify?verified=${verified ? 'true' : 'false'}`);
        if (r && r.ok) {
          // In-place updaten damit UI sofort den Username + Timestamp zeigt
          this.recipeDetail.data.user_verified = verified ? 1 : 0;
          this.recipeDetail.data.verified_by = verified ? r.by : null;
          this.recipeDetail.data.verified_at = verified ? (Date.now() / 1000) : null;
          this.showToast(verified ? '✓ Als geprüft markiert' : '⊘ Verifikation entfernt');
        }
      } finally {
        this.recipeDetail.verifying = false;
      }
    },

    // Re-Scrape aus dem Detail-Modal — gleicher Endpoint wie aus Audit
    async rescrapeFromDetailModal() {
      const id = this.recipeDetail.data?.id;
      if (!id || this.recipeDetail.rescraping) return;
      this.recipeDetail.rescraping = true;
      try {
        const r = await this.api('POST', `/api/recipes/${id}/rescrape`);
        if (r && r.ok) {
          if (r.any_change) {
            const parts = [];
            if (r.description_updated) parts.push('Beschreibung');
            if (r.thumbnail_updated) parts.push('Bild');
            this.showToast(`✓ ${parts.join(' + ')} aktualisiert`);
            // Re-Fetch damit das neue Thumb + Description sichtbar werden
            const fresh = await this.api('GET', '/api/recipes/' + id);
            if (fresh) this.recipeDetail.data = fresh;
          } else {
            this.showToast('⊘ Schon aktuell — keine Änderung');
          }
        } else if (r) {
          this.showToast('Re-Scrape: ' + (r.error || 'fehler'), 'err');
        }
      } finally {
        this.recipeDetail.rescraping = false;
      }
    },

    // ── Einkaufskorb ──────────────────────────────────────────────────
    async loadCart() {
      // Defensive: falls ein vorheriger Push hängen geblieben ist (z.B.
      // Network-Drop oder Tab-Wechsel mid-request), beim Tab-Reload
      // den Loading-State zurücksetzen damit der Button wieder klickbar ist.
      this.pushingToEinkauf = false;
      const r = await this.api('GET', '/api/cart');
      if (r) this.cart.items = r.items || [];
    },

    async addToCart() {
      const name = (this.cart.add.name || '').trim();
      if (!name) { this.showToast('Zutat-Name fehlt', 'error'); return; }
      const r = await this.api('POST', '/api/cart/add', {
        name,
        amount: this.cart.add.amount,
        unit: this.cart.add.unit || null,
      });
      if (r && r.ok) {
        this.cart.add = { name: '', amount: null, unit: '' };
        this.loadCart();
      }
    },

    async toggleCartItem(id, checked) {
      await this.api('PATCH', '/api/cart/' + id, { checked });
      // Lokal sofort updaten damit UI snappy ist; full reload nur bei größeren
      // Änderungen
      const it = this.cart.items.find(x => x.id === id);
      if (it) it.checked = checked;
    },

    async deleteCartItem(id) {
      await this.api('DELETE', '/api/cart/' + id);
      this.cart.items = this.cart.items.filter(x => x.id !== id);
    },

    async clearCart() {
      if (!confirm('Den ganzen Einkaufskorb leeren?')) return;
      const r = await this.api('POST', '/api/cart/clear', { only_checked: false });
      if (r && r.ok) { this.cart.items = []; this.showToast('Gelöscht'); }
    },

    async clearCheckedFromCart() {
      const r = await this.api('POST', '/api/cart/clear', { only_checked: true });
      if (r && r.ok) {
        this.showToast(`${r.deleted} erledigt-Posten gelöscht`);
        this.loadCart();
      }
    },

    // Cart → externe Einkauf-App pushen (POST /items pro Eintrag, dann
    // optional /consolidate). Skippt abgehakte Items per Default — die
    // hat man ja schon. Bei vollem Erfolg fragt nochmal ob Cart geleert
    // werden soll (Quality-of-Life — sonst hat man die Items doppelt).
    async pushToEinkauf() {
      const open = this.cart.items.filter(i => !i.checked).length;
      if (open === 0) {
        this.showToast('Keine offenen Items zu senden', 'err');
        return;
      }
      const target = this.config?.einkauf?.api_url || 'die Einkauf-App';
      if (!confirm(`${open} Items an ${target} senden?\n\nAbgehakte Items werden übersprungen.`)) return;
      this.pushingToEinkauf = true;
      try {
        const r = await this.api('POST', '/api/cart/push-to-einkauf', {
          consolidate: !!(this.config?.einkauf?.auto_consolidate),
          only_unchecked: true,
          clear_after: false,
        });
        if (!r) return;
        const failedN = r.failed?.length || 0;
        if (failedN === 0) {
          this.showToast(`✓ ${r.pushed} Items gesendet${r.consolidated ? ' + konsolidiert' : ''}`);
          // Bei vollem Erfolg fragen ob Cart geleert werden soll
          if (r.pushed > 0 && confirm(`Cart jetzt leeren? (${r.pushed} Items wurden erfolgreich übertragen)`)) {
            await this.api('POST', '/api/cart/clear', { only_checked: false });
            this.cart.items = [];
          }
        } else {
          this.showToast(`⚠ ${r.pushed} gesendet, ${failedN} Fehler — siehe Console`, 'err');
          console.warn('Einkauf-Push-Fehler:', r.failed);
        }
      } finally {
        this.pushingToEinkauf = false;
      }
    },

    async exportCart() {
      // Plain-Text-Endpoint liefert die Liste, wir öffnen sie in einem Popup
      try {
        const resp = await fetch('/api/cart/export.txt', { credentials: 'include' });
        const text = await resp.text();
        // Versuche Clipboard, sonst Download
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          this.showToast('In die Zwischenablage kopiert');
        } else {
          const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'einkaufsliste.txt';
          a.click();
        }
      } catch(e) {
        this.showToast('Export fehlgeschlagen', 'error');
      }
    },

    // ════════════════════════════════════════════════════════════════════
    // Stammdaten-Verwaltung (Tags + canonical Zutaten-Namen)
    // ════════════════════════════════════════════════════════════════════

    async loadMaster() {
      // Lädt nur den aktiven Tab — canonicals werden lazy beim Tab-Switch
      // nachgeladen. Bei Klick auf 'Neu laden' werden beide neu geholt
      // falls schon initial geladen.
      this.master.loading = true;
      try {
        await this.loadTags();
        if (this.master.canLoaded) await this.loadCanonicals();
      } finally {
        this.master.loading = false;
      }
    },

    async loadTags() {
      const r = await this.api('GET', '/api/master/tags');
      if (r) this.master.tags = r.tags || [];
    },

    async loadCanonicals() {
      const r = await this.api('GET', '/api/master/canonicals');
      if (r) {
        this.master.canonicals = r.canonicals || [];
        this.master.canLoaded = true;
      }
    },

    filteredTags() {
      const q = (this.master.tagFilter || '').toLowerCase().trim();
      if (!q) return this.master.tags;
      return this.master.tags.filter(t => (t.name || '').toLowerCase().includes(q));
    },

    filteredCanonicals() {
      const q = (this.master.canFilter || '').toLowerCase().trim();
      if (!q) return this.master.canonicals;
      return this.master.canonicals.filter(c =>
        (c.canonical_name || '').toLowerCase().includes(q) ||
        (c.raw_names || '').toLowerCase().includes(q)
      );
    },

    async renameTag(tag) {
      const newName = window.prompt(
        `Tag „${tag.name}" umbenennen zu:\n\n` +
        `Existiert der neue Name bereits, werden die Rezept-Zuordnungen ` +
        `gemergt (kein Datenverlust).`,
        tag.name
      );
      if (!newName) return;
      const trimmed = newName.trim();
      if (!trimmed || trimmed === tag.name) return;
      const r = await this.api('POST', '/api/master/tags/rename', {
        old_name: tag.name, new_name: trimmed,
      });
      if (r && r.ok) {
        this.showToast(r.merged ? `✓ Tag gemergt zu „${trimmed}"` : `✓ Tag umbenannt`);
        await this.loadTags();
        this.loadFacets();   // Filter-Sidebar refreshen
      }
    },

    async deleteTag(tag) {
      const msg = tag.recipe_count > 0
        ? `Tag „${tag.name}" wirklich löschen?\n\n${tag.recipe_count} Rezept(e) verlieren diesen Tag.`
        : `Tag „${tag.name}" wirklich löschen?\n\n(Ist aktuell unbenutzt.)`;
      if (!confirm(msg)) return;
      const r = await this.api('DELETE', `/api/master/tags/${tag.id}`);
      if (r && r.ok) {
        this.showToast(`✓ Tag „${tag.name}" gelöscht`);
        await this.loadTags();
        this.loadFacets();
      }
    },

    async renameCanonical(can) {
      const newName = window.prompt(
        `Canonical „${can.canonical_name}" umbenennen/mergen zu:\n\n` +
        `${can.recipe_count} Rezept(e), ${can.ingredient_count} Vorkommen werden umgestellt.\n` +
        `Existiert der neue Name bereits, werden die Vorkommen zusammengeführt.`,
        can.canonical_name
      );
      if (!newName) return;
      const trimmed = newName.trim().toLowerCase();
      if (!trimmed || trimmed === can.canonical_name) return;
      const updateNames = confirm(
        `Soll auch das angezeigte Zutaten-Name-Feld (z.B. „${(can.raw_names || '').split(',')[0]}") ` +
        `auf „${trimmed}" gesetzt werden?\n\n` +
        `OK = ja (alle Schreibweisen werden vereinheitlicht).\n` +
        `Abbrechen = nein (canonical wird angepasst, einzelne Anzeige-Namen bleiben).`
      );
      const r = await this.api('POST', '/api/master/canonicals/rename', {
        old_canonical: can.canonical_name,
        new_canonical: trimmed,
        update_names: updateNames,
      });
      if (r && r.ok) {
        this.showToast(`✓ ${r.affected} Vorkommen umgestellt`);
        await this.loadCanonicals();
        this.loadFacets();
      }
    },

    // ════════════════════════════════════════════════════════════════════
    // Audit-Dashboard
    // ════════════════════════════════════════════════════════════════════

    async loadAudit() {
      this.audit.loading = true;
      try {
        const params = new URLSearchParams();
        if (this.audit.withAi) params.set('with_ai', 'true');
        const r = await this.api('GET', '/api/audit?' + params.toString());
        if (r) {
          this.audit.data = r;
          this.audit.summary = r.summary;
        }
      } finally {
        this.audit.loading = false;
      }
    },

    // Endgültig fehlgeschlagene Downloads: Retry (Zähler reset) / Verwerfen (History-Sperre)
    async retryFailedDownload(url) {
      const r = await this.api('POST', `/api/pending/failed/${encodeURIComponent(url)}/retry`);
      if (r?.ok) {
        this.showToast('Zähler zurückgesetzt — nächster Lauf versucht es neu');
        this.audit.data.failed_downloads = this.audit.data.failed_downloads.filter(f => f.url !== url);
        if (this.audit.summary) this.audit.summary.failed_download_count = Math.max(0, (this.audit.summary.failed_download_count || 1) - 1);
      }
    },
    async discardFailedDownload(url) {
      if (!confirm('URL dauerhaft verwerfen? Sie wird nie wieder versucht.')) return;
      const r = await this.api('POST', `/api/pending/failed/${encodeURIComponent(url)}/discard`);
      if (r?.ok) {
        this.showToast('Verworfen');
        this.audit.data.failed_downloads = this.audit.data.failed_downloads.filter(f => f.url !== url);
        if (this.audit.summary) this.audit.summary.failed_download_count = Math.max(0, (this.audit.summary.failed_download_count || 1) - 1);
      }
    },

    // KI-Sanity startet Background-Job, dann pollen wir den Status alle 2s
    async startAiSanity() {
      if (this.audit.aiSanity.running) return;
      const r = await this.api('POST', '/api/audit/ai-sanity');
      if (!r || !r.ok) return;
      this.audit.aiSanity = {
        running: true, processed: 0, total: r.total, findings: 0, pollHandle: null,
      };
      this.showToast(`KI-Sanity-Check gestartet (${r.total} Rezepte)…`);
      const tick = async () => {
        const st = await this.api('GET', '/api/audit/ai-sanity/status');
        if (!st) return;
        this.audit.aiSanity.processed = st.processed;
        this.audit.aiSanity.findings = st.findings;
        if (st.running) {
          this.audit.aiSanity.pollHandle = setTimeout(tick, 2000);
        } else {
          this.audit.aiSanity.running = false;
          if (st.error) {
            this.showToast(`KI-Sanity-Fehler: ${st.error}`, 'err');
          } else {
            this.showToast(`KI-Sanity fertig: ${st.findings} Findings`);
          }
          await this.loadAudit();  // Findings neu laden
        }
      };
      tick();
    },

    // KI-Finding als 'erledigt' (Ignorieren-Button) — wird damit aus der
    // Findings-Liste entfernt aber für Audit-Trail in DB behalten.
    async resolveFinding(findingId) {
      const r = await this.api('POST', `/api/audit/finding/${findingId}/resolve`);
      if (r && r.ok) await this.loadAudit();
    },

    // Bulk: alle Rezepte mit status=ok+0 Zutaten auf pending zurücksetzen.
    // Worker pickt sie auf und versucht KI-Extract neu (mit aktuellem Prompt).
    async recoverEmpty() {
      const n = this.audit.data?.empty_recipes?.length || 0;
      if (!confirm(`${n} Rezepte auf 'pending' zurücksetzen?\n\nDer Worker extrahiert sie dann neu mit dem aktuellen Prompt. Bestehende Zutaten/Schritte würden überschrieben (sind ja eh leer).`)) return;
      const r = await this.api('POST', '/api/recipes/recover-empty');
      if (r && r.ok) {
        this.showToast(`✓ ${r.reset_count} Rezepte auf pending — Worker läuft`);
        await this.loadAudit();
      }
    },

    // Bulk: Nährwerte für bis zu 50 Rezepte berechnen. Synchroner Lauf —
    // UI ist blockiert für ~30s, danach Audit neu laden. Bei mehr als 50
    // pending Rezepten muss User wiederholt klicken (siehe Audit-Liste).
    async bulkComputeNutrition() {
      const total = this.audit.data?.data_gaps?.no_nutrition?.length || 0;
      if (total === 0) return;
      const batch = Math.min(total, 50);
      if (!confirm(`Nährwerte für ${batch} Rezepte berechnen?\n\n~$${(batch * 0.0005).toFixed(3)} Kosten, ~${batch * 0.6}s Laufzeit.\n${total > 50 ? `\nNoch ${total - batch} bleiben übrig — Button danach erneut klicken.` : ''}`)) return;
      this.audit.computingNutritionBulk = true;
      try {
        const r = await this.api('POST', '/api/recipes/compute-nutrition-bulk?limit=50');
        if (r && r.ok) {
          const failedN = r.failed?.length || 0;
          if (failedN === 0) {
            this.showToast(`✓ ${r.computed} Nährwerte berechnet`);
          } else {
            this.showToast(`${r.computed} erfolgreich, ${failedN} Fehler`, 'err');
            console.warn('nutrition-bulk-failures:', r.failed);
          }
          await this.loadAudit();
        }
      } finally {
        this.audit.computingNutritionBulk = false;
      }
    },

    // Auto-Heal: für alle FS-missing-Rezepte den DB-Pfad mit dem tatsächlichen
    // FS-Folder synchronisieren (Underscore↔Space, Case-Toleranz).
    // Idempotent — sicher mehrfach klickbar.
    async healFsPaths() {
      const n = this.audit.data?.data_gaps?.fs_missing?.length || 0;
      if (n === 0) return;
      this.audit.healingFs = true;
      try {
        const r = await this.api('POST', '/api/audit/heal-fs-paths');
        if (r && r.ok) {
          const unresolved = r.unresolvable?.length || 0;
          if (unresolved === 0) {
            this.showToast(`✓ ${r.healed} FS-Pfade korrigiert`);
          } else {
            this.showToast(`${r.healed} korrigiert · ${unresolved} ungelöst (siehe Liste)`, 'err');
          }
          await this.loadAudit();
        }
      } finally {
        this.audit.healingFs = false;
      }
    },

    // Schnell-Verify einzelnes Rezept direkt aus der Audit-Liste, ohne
    // Modal zu öffnen. Sendet POST /verify?verified=true mit dem id.
    async quickVerify(recipeId) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/verify?verified=true`);
      if (r && r.ok) {
        this.haptic(15);
        this.showToast('✓ Geprüft markiert');
        await this.loadAudit();
      }
    },

    // Bulk-Verify: alle aktuell sichtbaren unverifizierten Rezepte auf
    // einmal als 'ok' markieren. Vorsicht-Confirm weil pauschal —
    // umgeht die manuelle Prüfung.
    async verifyBulkUnverified() {
      const ids = (this.audit.data?.data_gaps?.unverified || []).map(r => r.id);
      if (ids.length === 0) return;
      if (!confirm(`${ids.length} Rezepte pauschal als geprüft markieren?\n\nAchtung: das setzt das verified-Flag OHNE manuelle Sichtung. Wenn du auch die ungesehenen pauschal akzeptieren willst, ok.\n\nReversibel: pro Rezept im Modal wieder unchecken.`)) return;
      this.audit.verifyingBulk = true;
      try {
        const r = await this.api('POST', '/api/audit/verify-bulk', { recipe_ids: ids });
        if (r && r.ok) {
          this.showToast(`✓ ${r.verified} als geprüft markiert`);
          await this.loadAudit();
        }
      } finally {
        this.audit.verifyingBulk = false;
      }
    },

    // ─── Favorit + Bewertung + Share ────────────────────────────────────
    async toggleFavorite(recipeId) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/favorite`);
      if (r?.ok) {
        const item = this.recipes.items.find(i => i.id === recipeId);
        if (item) item.is_favorite = r.is_favorite;
        if (this.recipeDetail?.data?.id === recipeId) {
          this.recipeDetail.data.is_favorite = r.is_favorite;
        }
        this.showToast(r.is_favorite ? '⭐ Favorit gesetzt' : 'Favorit entfernt');
      }
    },
    async setRating(recipeId, value) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/rating?value=${value}`);
      if (r?.ok) {
        const item = this.recipes.items.find(i => i.id === recipeId);
        if (item) item.rating = r.rating;
        if (this.recipeDetail?.data?.id === recipeId) {
          this.recipeDetail.data.rating = r.rating;
        }
        this.showToast(value === 0 ? 'Bewertung entfernt' : `${'★'.repeat(value)} Bewertet`);
      }
    },
    // Web-Share-API: native iOS-Share-Sheet. Fallback auf Clipboard.
    async shareRecipe(recipe) {
      const url = recipe.url || (window.location.origin + '/?recipe=' + recipe.id);
      const shareData = { title: recipe.name, text: `Rezept: ${recipe.name}`, url };
      try {
        if (navigator.share) {
          await navigator.share(shareData);
        } else {
          await navigator.clipboard.writeText(url);
          this.showToast('🔗 Link kopiert');
        }
      } catch (e) {
        if (e.name !== 'AbortError') {
          this.showToast('Teilen fehlgeschlagen: ' + e.message, 'err');
        }
      }
    },

    // ─── Infinite-Scroll: lade nächste Seite an aktuelle items ─────────
    async loadMoreRecipes() {
      if (this.recipes.loading || this.recipes.items.length >= this.recipes.total) return;
      this.recipes.loading = true;
      try {
        const nextOffset = this.recipes.items.length;
        const query = this._buildRecipeQuery().replace(/offset=\d+/, 'offset=' + nextOffset);
        const r = await this.api('GET', '/api/recipes?' + query);
        if (r?.items) {
          this.recipes.items.push(...r.items);
          this.recipes.total = r.total;
        }
      } finally {
        this.recipes.loading = false;
      }
    },

    // IntersectionObserver für Infinite-Scroll. Wird beim ersten Render des
    // Sentinel-Elements initialisiert (x-init). Re-erstellt sich selbst nicht —
    // einmaliges Setup reicht, Observer beobachtet das gleiche Element.
    _scrollObserver: null,
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

    // ─── Papierkorb ─────────────────────────────────────────────────────
    async loadTrash() {
      this.trash.loading = true;
      try {
        const r = await this.api('GET', '/api/recipes/trash/list?limit=500');
        if (r) {
          this.trash.items = r.items || [];
          this.trash.totalCount = r.total || 0;
        }
      } finally {
        this.trash.loading = false;
      }
    },
    async restoreRecipe(id) {
      const r = await this.api('POST', `/api/recipes/${id}/restore`);
      if (r?.ok) {
        if (r.files_deleted) {
          this.showToast('↺ Wiederhergestellt — Files waren beim Löschen entfernt');
        } else {
          this.showToast('↺ Wiederhergestellt');
        }
        await this.loadTrash();
      }
    },
    async purgeRecipe(id, name) {
      if (!confirm(`"${name}" ENDGÜLTIG löschen?\n\nDB-Eintrag + Folder + Files — nicht reversibel.`)) return;
      const r = await this.api('DELETE', `/api/recipes/${id}?hard=true&delete_files=true`);
      if (r?.ok) {
        this.showToast(`✓ "${name}" endgültig gelöscht`);
        await this.loadTrash();
      }
    },
    async emptyTrash() {
      const n = this.trash.totalCount;
      if (!confirm(`Papierkorb leeren? ${n} Rezepte werden ENDGÜLTIG gelöscht (DB + Files).`)) return;
      if (!confirm(`Wirklich sicher? ${n} Rezepte für immer weg, nicht reversibel.`)) return;
      this.trash.emptying = true;
      try {
        const r = await this.api('DELETE', '/api/recipes/trash/empty?delete_files=true');
        if (r?.ok) {
          this.showToast(`✓ ${r.purged} Rezepte endgültig gelöscht${r.errors?.length ? ` · ${r.errors.length} Fehler` : ''}`);
          await this.loadTrash();
        }
      } finally {
        this.trash.emptying = false;
      }
    },
    async rescrapeRecipe(recipeId) {
      this.audit.rescrapingId = recipeId;
      try {
        const r = await this.api('POST', `/api/recipes/${recipeId}/rescrape`);
        if (r && r.ok) {
          if (r.any_change) {
            const parts = [];
            if (r.description_updated) parts.push('Beschreibung');
            if (r.thumbnail_updated) parts.push('Bild');
            this.showToast(`✓ ${parts.join(' + ')} aktualisiert`);
          } else {
            this.showToast('⊘ Schon aktuell — keine Änderung');
          }
          await this.loadAudit();
        } else if (r) {
          this.showToast('Re-Scrape: ' + (r.error || 'unbekannter Fehler'), 'err');
        }
      } finally {
        this.audit.rescrapingId = null;
      }
    },

    // Einzelnes Rezept als 'geprüft' markieren - fällt aus allen Audit-Detections raus.
    // Rezept + Files bleiben unberührt.
    async verifyRecipe(recipeId) {
      const r = await this.api('POST', `/api/recipes/${recipeId}/verify`, { verified: true });
      if (r?.ok) {
        this.showToast('✓ als geprüft markiert');
        await this.loadAudit();
      }
    },

    // Bulk: alle IDs einer Detection-Section als geprüft markieren.
    // 'detection' ist der key in audit.data.data_gaps (z.B. 'no_image', 'no_url', 'few_ingredients').
    async bulkVerifyDetection(detection) {
      const list = this.audit.data?.data_gaps?.[detection] || [];
      if (list.length === 0) return;
      const ids = list.map(r => r.id);
      const label = {
        no_image: 'ohne Bild', no_steps: 'ohne Schritte', no_url: 'ohne URL',
        few_ingredients: 'mit wenigen Zutaten', no_description: 'ohne Beschreibung',
        no_nutrition: 'ohne Nährwerte', fs_missing: 'mit fehlendem Pfad',
        unverified: 'noch nicht geprüft',
      }[detection] || detection;
      if (!confirm(`${ids.length} Rezepte "${label}" als geprüft markieren?\n\nSie fallen aus dem Audit raus. Files und DB-Einträge bleiben.`)) return;
      const r = await this.api('POST', '/api/audit/verify-bulk', { recipe_ids: ids });
      if (r?.ok) {
        this.showToast(`✓ ${r.verified} Rezepte als geprüft markiert`);
        await this.loadAudit();
      }
    },

    // Frame aus lokalem Video extrahieren via ffmpeg (Alternative zu rescrape
    // wenn URL tot ist aber Video noch vorhanden).
    async extractFrame(recipeId, seconds = 2.0) {
      this.audit.extractingId = recipeId;
      try {
        const r = await this.api('POST',
          `/api/recipes/${recipeId}/extract-frame?seconds=${seconds}`);
        if (r?.ok) {
          this.showToast(`✓ Frame aus ${r.video} @ ${r.seconds}s`);
          await this.loadAudit();
        } else if (r) {
          this.showToast('Frame: ' + (r.error || 'Fehler'), 'err');
        }
      } finally {
        this.audit.extractingId = null;
      }
    },

    // Eigenes Bild hochladen — fallback wenn Frame-Extract+Re-Scrape nichts taugen.
    // Akzeptiert JPEG/PNG/WebP, max 10MB.
    async uploadThumbnail(recipeId, file) {
      console.log('[uploadThumbnail] called', recipeId, file);
      if (!file) { console.log('[uploadThumbnail] no file selected'); return; }
      if (file.size > 10 * 1024 * 1024) {
        this.showToast('Datei zu groß (max 10MB)', 'err'); return;
      }
      this.showToast('Lade hoch…');
      const fd = new FormData();
      fd.append('file', file);
      try {
        const resp = await fetch(`/api/recipes/${recipeId}/upload-thumbnail`, {
          method: 'POST',
          body: fd,
          credentials: 'same-origin',
        });
        console.log('[uploadThumbnail] response status', resp.status);
        if (resp.status === 401) { window.location = '/login'; return; }
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: resp.statusText }));
          this.showToast('Upload: ' + (err.detail || 'Fehler ' + resp.status), 'err');
          return;
        }
        const r = await resp.json();
        console.log('[uploadThumbnail] response body', r);
        if (r?.ok) {
          this.showToast(`✓ Bild gesetzt (${(r.size_bytes/1024).toFixed(0)} KB)`);
          if (this.recipeDetail?.data?.id === recipeId) {
            // Cache-Bust: ETag-Header gilt nur bei Reload, hier direkt rendern
            this.recipeDetail.data.thumb_filename = r.thumbnail + '?t=' + Date.now();
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

    // Bulk Frame-Extract — sequenziell für ALLE Rezepte ohne Bild.
    // Lokal, ~1s pro Rezept → bei 100 Rezepten ca. 2 Min. Cancel-Knopf
    // (state-flip extractingBulk) bricht laufende Schleife sauber ab.
    async bulkExtractFrames() {
      const list = (this.audit.data?.data_gaps?.no_image || []);
      if (list.length === 0) return;
      const eta = Math.ceil(list.length * 1.5 / 60);
      if (!confirm(`${list.length} Rezepte Frame-Extract starten?\nEstimated ~${list.length}s (≈${eta}min). Cancel jederzeit möglich.`)) return;
      this.audit.extractingBulk = true;
      this.audit.extractProgress = 0;
      this.audit.extractTotal = list.length;
      let ok = 0, fail = 0;
      for (const r of list) {
        if (!this.audit.extractingBulk) break; // Cancel via state-flip
        this.audit.extractingId = r.id;
        try {
          const res = await this.api('POST',
            `/api/recipes/${r.id}/extract-frame?seconds=2.0`);
          if (res?.ok) ok++; else fail++;
        } catch { fail++; }
        this.audit.extractProgress++;
      }
      this.audit.extractingBulk = false;
      this.audit.extractingId = null;
      this.showToast(`Fertig: ${ok} ok, ${fail} fail`);
      await this.loadAudit();
    },

    // Einzeln löschen — mit/ohne Files.
    async deleteRecipe(recipeId, deleteFiles = true) {
      const rec = this.audit.data?.data_gaps?.no_image?.find(r => r.id === recipeId) ||
                  this.audit.data?.data_gaps?.no_url?.find(r => r.id === recipeId) ||
                  null;
      const name = rec?.name || `#${recipeId}`;
      if (!confirm(`Rezept "${name}" wirklich löschen?\n\n${deleteFiles ? '⚠ FILES + Folder werden gelöscht!' : 'Nur DB-Eintrag, Files bleiben.'}`)) return;
      const r = await this.api('DELETE',
        `/api/recipes/${recipeId}?delete_files=${deleteFiles}`);
      if (r?.ok) {
        this.showToast(`✓ "${name}" gelöscht`);
        await this.loadAudit();
      }
    },

    // Aus dem Detail-Modal heraus löschen
    async deleteRecipeFromDetail() {
      const data = this.recipeDetail?.data;
      if (!data?.id) return;
      const choice = confirm(
        `Rezept "${data.name}" löschen?\n\n` +
        `⚠ OK = Rezept + Folder + Files KOMPLETT weg.\n` +
        `Abbrechen = nichts passiert.\n\n` +
        `Möchtest du Files behalten und nur DB-Eintrag löschen, klicke 'Abbrechen' und nutze den 🗑-Button in der Audit-Liste.`
      );
      if (!choice) return;
      const r = await this.api('DELETE',
        `/api/recipes/${data.id}?delete_files=true`);
      if (r?.ok) {
        this.showToast(`✓ "${data.name}" gelöscht`);
        this.closeRecipeDetail();
        // Listen aktualisieren falls offen
        if (typeof this.loadAudit === 'function') await this.loadAudit();
        if (typeof this.loadRecipes === 'function') await this.loadRecipes();
      }
    },

    // Bulk-Delete pro Detection (mit Files).
    async bulkDeleteDetection(detection) {
      const list = this.audit.data?.data_gaps?.[detection] || [];
      if (list.length === 0) return;
      const ids = list.map(r => r.id);
      const label = {
        no_image: 'ohne Bild', no_url: 'ohne URL', no_steps: 'ohne Schritte',
        few_ingredients: 'mit wenigen Zutaten', no_description: 'ohne Beschreibung',
        no_nutrition: 'ohne Nährwerte', fs_missing: 'mit fehlendem Pfad',
      }[detection] || detection;
      const txt = `${ids.length} Rezepte "${label}" KOMPLETT löschen?\n\n` +
                  `⚠ DB-Einträge UND Folder + Files werden entfernt — nicht reversibel!`;
      if (!confirm(txt)) return;
      if (!confirm(`Wirklich SICHER? ${ids.length} Rezepte für immer weg.`)) return;
      let ok = 0, fail = 0;
      for (const id of ids) {
        try {
          const res = await this.api('DELETE', `/api/recipes/${id}?delete_files=true`);
          if (res?.ok) ok++; else fail++;
        } catch { fail++; }
      }
      this.showToast(`✓ ${ok} gelöscht, ${fail} Fehler`);
      await this.loadAudit();
    },

    // Bulk: ALLE 'Kein Bild'-Rezepte hintereinander re-scrapen.
    // Sequentiell (nicht parallel) damit yt-dlp nicht rate-limited wird.
    // Cancel via state-flip rescrapingBulk = false (z.B. erneuter Button-Klick).
    async rescrapeBulkNoImage() {
      const list = (this.audit.data?.data_gaps?.no_image || []);
      if (list.length === 0) return;
      const eta_sec = list.length * 15;
      const eta_str = eta_sec > 60 ? `~${Math.ceil(eta_sec/60)} Min` : `~${eta_sec}s`;
      if (!confirm(`${list.length} Rezepte sequenziell re-scrapen?\n\nDauert ${eta_str}. Bei Fehlern (URL down/geo-blocked) wird das Rezept übersprungen.\n\nCancel: erneut auf den Button klicken.`)) return;
      this.audit.rescrapingBulk = true;
      this.audit.rescrapeProgress = 0;
      this.audit.rescrapeTotal = list.length;
      let ok = 0, fail = 0;
      try {
        for (const r of list) {
          if (!this.audit.rescrapingBulk) break;  // cancel
          this.audit.rescrapeProgress++;
          try {
            const resp = await this.api('POST', `/api/recipes/${r.id}/rescrape`);
            if (resp && resp.ok && resp.any_change) ok++; else fail++;
          } catch (e) {
            fail++;
          }
        }
        this.showToast(`✓ ${ok} aktualisiert · ${fail} unverändert/fehlgeschlagen`);
        await this.loadAudit();
      } finally {
        this.audit.rescrapingBulk = false;
        this.audit.rescrapeProgress = 0;
        this.audit.rescrapeTotal = 0;
      }
    },

    // ─── Delete-DB-only: nur DB-Eintrag löschen, FS unangetastet ────────
    // Für tote Rezepte deren FS-Folder weg ist. delete_files=false damit
    // safe_delete_recipe nicht versucht den nicht-existenten Folder zu
    // löschen (würde fehlerfrei skippen, aber explizit ist sauberer).
    async deleteRecipeDbOnly(recipeId, name) {
      if (!confirm(`„${name}" nur aus DB löschen?\n\nFS-Files werden NICHT angetastet (Folder existiert eh nicht mehr).`)) return;
      const r = await this.api('DELETE', `/api/recipes/${recipeId}?delete_files=false`);
      if (r) {
        this.showToast(`✓ „${name}" aus DB entfernt`);
        await this.loadAudit();
      }
    },

    // Bulk: alle 'Kein FS-Match' aus DB löschen
    async deleteUnresolvableFsMissing() {
      const ids = (this.audit.data?.data_gaps?.fs_missing || [])
        .filter(r => !r.resolved_path)
        .map(r => r.id);
      if (ids.length === 0) {
        this.showToast('Keine ungelösten — Auto-Heal hat alles erwischt');
        return;
      }
      if (!confirm(`${ids.length} 'Kein FS-Match'-Rezepte aus DB löschen?\n\nFS unangetastet. Die Rezepte sind in den FS-Foldern eh weg, das räumt nur die DB auf. Reversibel nur per Backup.`)) return;
      this.audit.deletingUnresolvable = true;
      try {
        let ok = 0, fail = 0;
        for (const id of ids) {
          try {
            const r = await this.api('DELETE', `/api/recipes/${id}?delete_files=false`);
            if (r) ok++; else fail++;
          } catch (e) {
            fail++;
          }
        }
        this.showToast(`✓ ${ok} gelöscht${fail ? ' · ' + fail + ' Fehler' : ''}`);
        await this.loadAudit();
      } finally {
        this.audit.deletingUnresolvable = false;
      }
    },

    // KI-Vorschlag tatsächlich anwenden — abhängig vom finding_type:
    //   category_mismatch → Folder in neue Type/Kategorie verschieben
    //   name_mismatch     → recipe.name + Folder + info.json updaten
    //   folder_mismatch   → nur Folder umbenennen, recipe.name bleibt
    // FS-Move ist irreversibel, daher confirm() mit klarer Vorschau.
    async applyFinding(f) {
      const desc = {
        category_mismatch: `Folder verschieben:\n„${f.current_value}" → „${f.suggested_value}"`,
        name_mismatch:     `Rezept umbenennen + Folder umbenennen + info.json updaten:\n„${f.current_value}" → „${f.suggested_value}"`,
        folder_mismatch:   `Folder auf FS umbenennen (recipe.name bleibt):\n„${f.current_value}" → „${f.suggested_value}"`,
      }[f.finding_type] || `Anwenden: ${f.suggested_value}`;
      if (!confirm(desc + '\n\nFS-Move ist nicht rückgängig zu machen.')) return;
      const r = await this.api('POST', `/api/audit/finding/${f.id}/apply`);
      if (r && r.ok) {
        this.showToast(`✓ Angewendet → ${r.new_path?.split('/').slice(-2).join('/')}`);
        await this.loadAudit();
      }
    },

    // Bulk-Apply: alle offenen Findings eines Typs in einem Rutsch.
    // Bei Fehler (z.B. Ziel-Folder kollidiert) wird trotzdem weitergemacht,
    // Toast zeigt am Ende 'X erfolgreich / Y Fehler'. Details in Console.
    async applyAllFindings(findingType) {
      const counts = {
        category_mismatch: this.audit.data?.ai_category_findings?.length || 0,
        name_mismatch: this.audit.data?.ai_name_findings?.length || 0,
        folder_mismatch: this.audit.data?.ai_folder_findings?.length || 0,
      };
      const n = counts[findingType] || 0;
      if (n === 0) return;
      const label = {
        category_mismatch: 'Kategorie-Verschiebungen',
        name_mismatch: 'Namens-Änderungen (inkl. Folder + info.json)',
        folder_mismatch: 'Folder-Umbenennungen',
      }[findingType] || findingType;
      if (!confirm(`${n} ${label} auf einmal anwenden?\n\nJedes FS-Move ist irreversibel.\nBei Kollisionen wird das einzelne Finding übersprungen.`)) return;

      this.audit.bulkApplying = true;
      try {
        const r = await this.api('POST',
          `/api/audit/findings/apply-all?finding_type=${findingType}`);
        if (!r) return;
        const failedN = r.failed?.length || 0;
        if (failedN === 0) {
          this.showToast(`✓ Alle ${r.applied} angewendet`);
        } else {
          this.showToast(`${r.applied} erfolgreich, ${failedN} Fehler — Console für Details`, 'err');
          console.warn('apply-all-Fehler:', r.failed);
        }
        await this.loadAudit();
      } finally {
        this.audit.bulkApplying = false;
      }
    },

    // FS-Konflikt-Folder physisch löschen via Audit-Endpoint des bestehenden
    // delete-flows. Hier nur ein confirm + danach reload.
    // FS-Konflikt-Compare öffnen: lädt parallel DB-Rezept (per ID) und
    // FS-Folder-Preview (info.json + description + media-Liste). User sieht
    // beide Seiten und entscheidet welcher behalten wird.
    async openFsCompare(syncError) {
      this.fsCompare.show = true;
      this.fsCompare.syncError = syncError;
      this.fsCompare.dbRecipe = null;
      this.fsCompare.fsPreview = null;
      // Loading-Flags damit das UI 'Wird geladen' vs 'Keine Daten' unterscheiden kann
      this.fsCompare.loadingDb = !!syncError.conflict_with_id;
      this.fsCompare.loadingFs = true;
      try {
        const [db, fs] = await Promise.all([
          syncError.conflict_with_id
            ? this.api('GET', '/api/recipes/' + syncError.conflict_with_id)
            : Promise.resolve(null),
          this.api('GET', '/api/audit/folder-preview?path=' +
                   encodeURIComponent(syncError.folder_path)),
        ]);
        this.fsCompare.dbRecipe = db;
        this.fsCompare.fsPreview = fs;
      } catch (e) {
        this.showToast('Fehler beim Laden: ' + e.message, 'err');
      } finally {
        this.fsCompare.loadingDb = false;
        this.fsCompare.loadingFs = false;
      }
    },

    // Compare-Aktion: das in-DB Rezept (+ sein Folder) löschen, dann
    // automatisch nächsten Sync triggern damit der bisherige FS-Konflikt-
    // Folder neu indexiert wird (= übernimmt die URL).
    async deleteFsCompareDb() {
      const r = this.fsCompare.dbRecipe;
      if (!r) return;
      if (!confirm(`DB-Rezept #${r.id} „${r.name}" löschen?\n\nDer Folder ${r.folder_path} wird auch entfernt.\nDanach kann der Konflikt-Folder beim nächsten Sync rein.`)) return;
      const resp = await this.api('DELETE', '/api/recipes/' + r.id);
      if (resp && resp.ok) {
        this.showToast('✓ DB-Rezept gelöscht');
        this.fsCompare.show = false;
        await this.loadAudit();
      }
    },

    // FS-Konflikt-Folder löschen (gleiches wie der Inline-Button, aber direkt
    // aus dem Compare-Modal heraus).
    async deleteFsCompareFs() {
      const path = this.fsCompare.fsPreview?.folder_path;
      if (!path) return;
      if (!confirm(`FS-Folder löschen?\n\n${path}\n\nDie Dateien werden dauerhaft entfernt.`)) return;
      const r = await this.api('POST', '/api/audit/recipe/delete-by-path', { folder_path: path });
      if (r && r.ok) {
        this.showToast('✓ FS-Folder gelöscht');
        this.fsCompare.show = false;
        await this.loadAudit();
      }
    },

    async deleteFsConflictFolder(folderPath) {
      if (!confirm(`Wirklich folder löschen?\n\n${folderPath}\n\nDas entfernt die Dateien auf dem FS dauerhaft.`)) return;
      const r = await this.api('POST', '/api/audit/recipe/delete-by-path', { folder_path: folderPath });
      if (r && r.ok) {
        this.showToast('Folder gelöscht');
        await this.loadAudit();
      }
    },

    // Bad-Names gruppiert nach Grund, für die Sektion-Liste
    groupedBadNames() {
      if (!this.audit.data?.bad_names) return [];
      const groups = {};
      this.audit.data.bad_names.forEach(b => {
        if (!groups[b.reason]) groups[b.reason] = { reason: b.reason, items: [] };
        groups[b.reason].items.push(b);
      });
      // Nach Anzahl absteigend sortieren
      return Object.values(groups).sort((a, b) => b.items.length - a.items.length);
    },

    // ── Audit-Aktionen (Phase 2: destruktiv) ─────────────────────────
    async auditRenamePrompt(recipe) {
      const current = recipe.name || '';
      const newName = window.prompt(
        `Neuer Name für Rezept #${recipe.id}:\n\n` +
        `Folder wird auch umbenannt (mit normalisiertem Namen).`,
        current
      );
      if (!newName || newName.trim() === current) return;
      await this._auditDoRename(recipe.id, newName.trim());
    },

    async auditApplySuggestion(recipe, suggestion) {
      const ok = window.confirm(
        `Rezept #${recipe.id} umbenennen?\n\n` +
        `Alt:  ${recipe.name || '(leer)'}\n` +
        `Neu:  ${suggestion}\n\n` +
        `Folder wird mit umbenannt.`
      );
      if (!ok) return;
      await this._auditDoRename(recipe.id, suggestion);
    },

    async _auditDoRename(id, newName) {
      try {
        const r = await fetch(`/api/recipes/${id}/rename`, {
          method: 'PUT', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ new_name: newName, rename_folder: true }),
        });
        const data = await r.json();
        if (r.ok && data.ok) {
          this.showToast(`✓ Umbenannt: ${data.new_name}`);
          await this.loadAudit();   // Audit-Findings reloaden
          // wenn der Recipes-Tab eh aktiv war, dort auch refreshen
          if (this.page === 'recipes') this.loadRecipes();
        } else {
          this.showToast('Fehler: ' + (data.detail || data.error || 'unbekannt'), 'error');
        }
      } catch (e) {
        this.showToast('Rename-Request fehlgeschlagen: ' + e, 'error');
      }
    },

    async auditDelete(recipe) {
      const ok = window.confirm(
        `Rezept #${recipe.id} "${recipe.name}" wirklich löschen?\n\n` +
        `Das löscht:\n` +
        `  • DB-Eintrag (inkl. Zutaten, Schritte, Tags)\n` +
        `  • Folder im Filesystem: ${recipe.folder_path}\n\n` +
        `Aktion ist nicht rückgängig zu machen.`
      );
      if (!ok) return;
      try {
        const r = await fetch(`/api/recipes/${recipe.id}?delete_files=true`, {
          method: 'DELETE', credentials: 'include',
        });
        const data = await r.json();
        if (r.ok && data.ok) {
          this.showToast(`🗑️ Gelöscht: ${data.name}`);
          await this.loadAudit();
          if (this.page === 'recipes') this.loadRecipes();
        } else {
          this.showToast('Fehler: ' + (data.detail || 'unbekannt'), 'error');
        }
      } catch (e) {
        this.showToast('Delete-Request fehlgeschlagen: ' + e, 'error');
      }
    },

    async auditMerge(sourceId, targetId) {
      const ok = window.confirm(
        `Rezept #${sourceId} in #${targetId} mergen?\n\n` +
        `Was passiert:\n` +
        `  • Tags von #${sourceId} kommen zu #${targetId} (Union)\n` +
        `  • Cart-Referenzen werden umgeschrieben\n` +
        `  • #${sourceId} wird komplett gelöscht (DB + Folder)\n` +
        `  • #${targetId} bleibt mit allen Zutaten/Schritten erhalten`
      );
      if (!ok) return;
      try {
        const r = await fetch('/api/recipes/merge', {
          method: 'POST', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_id: sourceId, target_id: targetId, delete_source: true,
          }),
        });
        const data = await r.json();
        if (r.ok && data.ok) {
          this.showToast(
            `⇆ Merge ok: +${data.tags_merged} Tags, ${data.cart_remapped} Cart-Refs`
          );
          await this.loadAudit();
          if (this.page === 'recipes') this.loadRecipes();
        } else {
          this.showToast('Fehler: ' + (data.detail || 'unbekannt'), 'error');
        }
      } catch (e) {
        this.showToast('Merge-Request fehlgeschlagen: ' + e, 'error');
      }
    },
  };
}
