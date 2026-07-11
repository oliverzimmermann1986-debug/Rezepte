// Rezeptliebe – Mobile-First Frontend
function recipeApp() {
  return {
    page: 'recipes',
    isOnline: navigator.onLine,
    recipes: [], recipeTotal: 0, recipeQuery: '', recipeType: '', recipeCategory: '', recipeSort: 'newest', recipeFiltersOpen: false,
    recipeFilters: { types: [], categories: [] }, recipeLimit: 48, recipeOffset: 0, recipesLoading: false,
    recipeDetail: null,
    jobs: [], lastScraper: null, scraperProgress: null,
    status: { scraper: null, reanalyze: null, pending_count: 0 },
    schedule: {}, scheduleEdit: { scraper: '*:0/30' },
    pending: [], pendingSort: 'newest', pendingLoading: false, reanalyzing: {}, failedDownloads: [],
    history: [], historyQuery: '', historyTypeFilter: 'all', editingItem: null,
    config: { web: {}, schedule: {}, paths: {}, mail: { recipe: {}, wedding: {} }, ai: { provider: 'ollama', ollama: {}, openai: {} }, ytdlp: {} }, configSaving: false, testing: {}, testResults: {},
    maintenance: null, maintenanceBusy: false, maintenanceOutput: '',
    currentLog: '',
    browser: { show: false, title: '', currentPath: '', parent: null, entries: [], loading: false, callback: null },
    toast: { show: false, message: '', type: 'ok' },
    recipeTypes: ['Hauptgericht','Vorspeise','Nachspeise','Snack','Frühstück','Getränk','Beilage','Sonstiges'],
    weddingCategories: ['Deko','Foto','Basteln','Einladung','Standesamt','Outfit','Catering','Sonstiges'],
    _eventSource: null, _statusTimer: null, _toastTimer: null,

    init() {
      const initial = (window.location.hash || '#recipes').slice(1);
      this.navigate(initial, false);
      window.addEventListener('online', () => { this.isOnline = true; this.refreshStatus(); });
      window.addEventListener('offline', () => { this.isOnline = false; });
      window.addEventListener('popstate', () => this.navigate((window.location.hash || '#recipes').slice(1), false));
      this._startEventStream();
      this._statusTimer = window.setInterval(() => this.refreshStatus(), 15000);
      this.refreshStatus();
      queueMicrotask(() => this.enhanceUi());
    },

    pageTitle() {
      return ({ recipes: 'Rezepte', import: 'Import', pending: 'Prüfen', history: 'Historie', config: 'Einstellungen' })[this.page] || 'Rezepte';
    },

    navigate(target, pushState = true) {
      const allowed = new Set(['recipes', 'import', 'pending', 'history', 'config']);
      const next = allowed.has(target) ? target : 'recipes';
      this.page = next;
      if (pushState && window.location.hash !== `#${next}`) history.pushState({ page: next }, '', `#${next}`);
      else if (!window.location.hash) history.replaceState({ page: next }, '', `#${next}`);
      if (next === 'recipes') this.loadRecipes(true);
      if (next === 'import') this.loadImportPage();
      if (next === 'pending') { this.loadPending(); this.loadFailedDownloads(); }
      if (next === 'history') this.loadHistory();
      if (next === 'config') this.loadConfig();
      requestAnimationFrame(() => {
        const main = document.getElementById('main-content');
        if (main) main.scrollTo({ top: 0, behavior: 'auto' });
        window.scrollTo({ top: 0, behavior: 'auto' });
        this.enhanceUi();
      });
    },

    async api(url, options = {}) {
      const opts = { credentials: 'same-origin', ...options };
      opts.headers = { Accept: 'application/json', ...(options.headers || {}) };
      if (opts.body && typeof opts.body !== 'string') {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.body);
      }
      const response = await fetch(url, opts);
      if (response.status === 401) { window.location.assign('/login'); throw new Error('Anmeldung abgelaufen'); }
      const type = response.headers.get('content-type') || '';
      const data = type.includes('application/json') ? await response.json() : await response.text();
      if (!response.ok) {
        const message = typeof data === 'object' ? (data.detail || data.error || JSON.stringify(data)) : data;
        throw new Error(message || `HTTP ${response.status}`);
      }
      return data;
    },

    async refreshStatus() {
      if (!navigator.onLine) return;
      try {
        const wasRunning = Boolean(this.status.scraper);
        this.status = await this.api('/api/jobs/status/current');
        if (this.status.scraper) this.loadScraperProgress();
        if (wasRunning && !this.status.scraper) { this.loadRecipes(true); this.loadJobs(); }
      } catch (_) { /* transient */ }
    },

    _startEventStream() {
      if (!window.EventSource) return;
      try {
        this._eventSource = new EventSource('/api/events');
        this._eventSource.addEventListener('status', event => {
          try { const next = JSON.parse(event.data); const finished = Boolean(this.status.scraper) && !next.scraper; this.status = next; if (finished) { this.loadRecipes(true); this.loadJobs(); } } catch (_) { /* ignore malformed event */ }
        });
        this._eventSource.addEventListener('scraper_progress', event => {
          try { this.scraperProgress = JSON.parse(event.data); } catch (_) { /* ignore malformed event */ }
        });
      } catch (_) { /* polling remains active */ }
    },

    async loadRecipes(reset = true) {
      if (this.recipesLoading) return;
      this.recipesLoading = true;
      if (reset) this.recipeOffset = 0;
      try {
        const p = new URLSearchParams({
          q: this.recipeQuery || '', type: this.recipeType || '', category: this.recipeCategory || '',
          sort: this.recipeSort || 'newest', limit: String(this.recipeLimit), offset: String(this.recipeOffset),
        });
        const result = await this.api(`/api/recipes?${p}`);
        this.recipes = reset ? (result.items || []) : [...this.recipes, ...(result.items || [])];
        this.recipeTotal = Number(result.total || 0);
        this.recipeFilters = { types: result.types || [], categories: result.categories || [] };
        this.recipeOffset = this.recipes.length;
      } catch (error) { this.notify(error.message, 'error'); }
      finally { this.recipesLoading = false; }
    },
    loadMoreRecipes() { return this.loadRecipes(false); },
    resetRecipeFilters() {
      this.recipeQuery = ''; this.recipeType = ''; this.recipeCategory = ''; this.recipeSort = 'newest';
      this.recipeFiltersOpen = false;
      this.loadRecipes(true);
    },
    recipeResultLabel() {
      if (!this.recipeTotal) return 'Keine Rezepte';
      const suffix = this.recipeQuery || this.recipeType || this.recipeCategory ? ' passend zu deiner Auswahl' : ' in deiner Sammlung';
      return `${this.recipeTotal} ${this.recipeTotal === 1 ? 'Rezept' : 'Rezepte'}${suffix}`;
    },
    async openRecipe(recipe) {
      try { this.recipeDetail = await this.api(`/api/recipes/${encodeURIComponent(recipe.id)}`); }
      catch (error) { this.notify(error.message, 'error'); }
    },
    mediaSymbol(kind) { return ({ video: '▶', image: '▧', pdf: 'PDF', file: '•' })[kind] || '•'; },
    mediaLabel(kind) { return ({ video: 'Video', image: 'Bild', pdf: 'PDF', file: 'Datei' })[kind] || 'Datei'; },

    async loadImportPage() {
      await Promise.allSettled([this.refreshStatus(), this.loadJobs(), this.loadSchedule(), this.loadScraperProgress()]);
      if (!this.recipeTotal) this.loadRecipes(true);
    },
    async loadJobs() {
      try {
        this.jobs = await this.api('/api/jobs/list?limit=50');
        this.lastScraper = this.jobs.find(job => job.kind === 'scraper') || null;
      } catch (error) { this.notify(error.message, 'error'); }
    },
    async loadScraperProgress() {
      try { this.scraperProgress = await this.api('/api/jobs/scraper/progress'); }
      catch (_) { this.scraperProgress = null; }
    },
    async runScraper() {
      try {
        await this.api('/api/jobs/scraper/run', { method: 'POST' });
        this.notify('Import gestartet'); await this.refreshStatus(); await this.loadJobs();
      } catch (error) { this.notify(error.message, 'error'); }
    },
    async cancelScraper() {
      try { const r = await this.api('/api/jobs/scraper/cancel', { method: 'POST' }); this.notify(r.ok ? 'Abbruch angefordert' : (r.error || 'Kein Import aktiv'), r.ok ? 'ok' : 'error'); }
      catch (error) { this.notify(error.message, 'error'); }
    },
    async openLog(job) {
      try { const r = await this.api(`/api/jobs/${job.id}/log?tail=1000`); this.currentLog = r.log || 'Kein Log vorhanden.'; }
      catch (error) { this.notify(error.message, 'error'); }
    },
    summarizeJob(job) {
      const s = job.summary || {};
      if (typeof s === 'string') return s;
      const parts = [];
      if (s.auto != null) parts.push(`${s.auto} automatisch`);
      if (s.pending != null) parts.push(`${s.pending} zu prüfen`);
      if (s.processed != null) parts.push(`${s.processed} verarbeitet`);
      if (s.errors) parts.push(`${s.errors} Fehler`);
      return parts.join(' · ') || s.error || 'Keine Zusammenfassung';
    },
    async loadSchedule() {
      try {
        this.schedule = await this.api('/api/schedule');
        this.scheduleEdit.scraper = this.schedule.scraper?.oncalendar || this.config.schedule?.scraper_interval || '*:0/30';
      } catch (_) { /* systemd can be unavailable in development */ }
    },
    async saveSchedule() {
      try {
        const value = (this.scheduleEdit.scraper || '').trim();
        if (!value) throw new Error('Zeitplan darf nicht leer sein');
        await this.api('/api/schedule', { method: 'PUT', body: { scraper: value } });
        this.notify('Import-Zeitplan gespeichert'); await this.loadSchedule();
      } catch (error) { this.notify(error.message, 'error'); }
    },
    humanSchedule(value) {
      const v = String(value || '');
      const known = { '*:0/30': 'Alle 30 Minuten', 'hourly': 'Stündlich', 'daily': 'Täglich', 'weekly': 'Wöchentlich' };
      return known[v] || v || 'Nicht festgelegt';
    },

    async loadPending() {
      this.pendingLoading = true;
      try {
        const items = await this.api(`/api/pending?sort=${encodeURIComponent(this.pendingSort)}`);
        this.pending = items.map(item => {
          const s = item.ai_suggestion || {};
          return { ...item, form: {
            name: s.name || item.name || '', type: s.type || 'Hauptgericht',
            category: s.category || (item.content_type === 'wedding' ? 'Sonstiges' : 'Allgemein'),
          }};
        });
      } catch (error) { this.notify(error.message, 'error'); }
      finally { this.pendingLoading = false; }
    },
    pendingFileUrl(item) { return `/api/pending/file?url=${encodeURIComponent(item.url)}`; },
    confidenceLabel(item) {
      const value = Number((item.ai_suggestion || {}).confidence);
      return Number.isFinite(value) ? `${Math.round(value * 100)} % sicher` : 'KI-Vorschlag';
    },
    async resolvePending(item, action) {
      try {
        const body = { url: item.url, action, name: item.form.name, type: item.form.type, category: item.form.category };
        const r = await this.api('/api/pending', { method: 'POST', body });
        if (r.ok === false) throw new Error(r.error || 'Eintrag konnte nicht verarbeitet werden');
        this.notify(action === 'save' ? 'Eintrag gespeichert' : 'Eintrag übersprungen');
        await this.loadPending(); await this.refreshStatus();
        if (action === 'save') this.loadRecipes(true);
      } catch (error) { this.notify(error.message, 'error'); }
    },
    async reanalyzePending(item) {
      this.reanalyzing[item.url] = true;
      try { await this.api('/api/pending/reanalyze', { method: 'POST', body: { url: item.url } }); this.notify('Neu analysiert'); await this.loadPending(); }
      catch (error) { this.notify(error.message, 'error'); }
      finally { delete this.reanalyzing[item.url]; }
    },
    async reanalyzeAllPending() {
      try { await this.api('/api/pending/reanalyze-all', { method: 'POST' }); this.notify('Neuanalyse gestartet'); await this.refreshStatus(); }
      catch (error) { this.notify(error.message, 'error'); }
    },
    async loadFailedDownloads() {
      try { this.failedDownloads = await this.api('/api/pending/failed?limit=100'); }
      catch (_) { this.failedDownloads = []; }
    },
    async retryFailed(url) {
      try { await this.api(`/api/pending/failed/${encodeURIComponent(url)}/retry`, { method: 'POST' }); this.notify('Download wird erneut versucht'); await this.loadFailedDownloads(); }
      catch (error) { this.notify(error.message, 'error'); }
    },
    async clearAllFailed() {
      try { await this.api('/api/pending/failed/clear-all', { method: 'POST' }); this.notify('Fehlerliste geleert'); await this.loadFailedDownloads(); }
      catch (error) { this.notify(error.message, 'error'); }
    },

    async loadHistory() {
      try { this.history = await this.api('/api/history?limit=2000'); }
      catch (error) { this.notify(error.message, 'error'); }
    },
    filteredHistory() {
      const q = this.historyQuery.trim().toLowerCase();
      return this.history.filter(item => {
        const typeOk = this.historyTypeFilter === 'all' || item.content_type === this.historyTypeFilter;
        const text = `${item.name || ''} ${item.recipe_type || ''} ${item.category || ''} ${item.target_dir || ''} ${item.url || ''}`.toLowerCase();
        return typeOk && (!q || text.includes(q));
      });
    },
    openEditItem(item) {
      this.editingItem = { ...item, type: item.recipe_type || '', category: item.category || '' };
    },
    async saveEditItem() {
      try {
        const item = this.editingItem;
        await this.api('/api/history/edit', { method: 'POST', body: { url: item.url, name: item.name, type: item.type || null, category: item.category || null } });
        this.editingItem = null; this.notify('Eintrag aktualisiert'); await this.loadHistory(); await this.loadRecipes(true);
      } catch (error) { this.notify(error.message, 'error'); }
    },
    async deleteHistoryItem(item) {
      if (!window.confirm(`„${item.name || 'Eintrag'}“ wirklich samt Dateien löschen?`)) return;
      try { await this.api('/api/history/delete', { method: 'POST', body: { url: item.url } }); this.notify('Eintrag gelöscht'); await this.loadHistory(); await this.loadRecipes(true); }
      catch (error) { this.notify(error.message, 'error'); }
    },

    ensureConfigDefaults(raw = {}) {
      const cfg = structuredClone(raw || {});
      cfg.web ||= {}; cfg.schedule ||= {}; cfg.paths ||= {}; cfg.mail ||= {}; cfg.mail.recipe ||= {}; cfg.mail.wedding ||= {};
      cfg.ai ||= {}; cfg.ai.ollama ||= {}; cfg.ai.openai ||= {}; cfg.ytdlp ||= {};
      cfg.web.username ||= ''; cfg.web.password ||= ''; cfg.web.bind_host ||= '127.0.0.1';
      cfg.schedule.scraper_interval ||= '*:0/30';
      cfg.paths.recipe_dir ||= '/opt/scrapper/files/rezepte'; cfg.paths.wedding_dir ||= '/opt/scrapper/files/hochzeit';
      cfg.paths.temp_dir ||= '/opt/scrapper/temp'; cfg.paths.logs_dir ||= '/opt/scrapper/logs';
      cfg.ai.provider ||= 'ollama'; cfg.ai.ollama.url ||= 'http://127.0.0.1:11434'; cfg.ai.ollama.model ||= '';
      cfg.ai.openai.api_key ||= ''; cfg.ai.openai.model ||= 'gpt-4o-mini'; cfg.ai.openai.base_url ||= '';
      cfg.ytdlp.binary ||= '/opt/scrapper/venv/bin/yt-dlp'; cfg.ytdlp.cookies_file ||= '';
      delete cfg.backup;
      return cfg;
    },
    async loadConfig() {
      try {
        this.config = this.ensureConfigDefaults(await this.api('/api/config'));
        this.recipeTypes = this.config.recipe_types || this.recipeTypes;
        this.weddingCategories = this.config.wedding_categories || this.weddingCategories;
        this.scheduleEdit.scraper = this.config.schedule.scraper_interval;
        await Promise.allSettled([this.loadSchedule(), this.loadMaintenance()]);
        queueMicrotask(() => this.enhanceUi());
      } catch (error) { this.notify(error.message, 'error'); }
    },
    async saveConfig() {
      this.configSaving = true;
      try {
        const payload = structuredClone(this.config); delete payload.backup;
        await this.api('/api/config', { method: 'PUT', body: payload });
        this.config = this.ensureConfigDefaults(await this.api('/api/config'));
        this.notify('Einstellungen gespeichert');
      } catch (error) { this.notify(error.message, 'error'); }
      finally { this.configSaving = false; }
    },
    async runTest(key, url, body = null) {
      this.testing[key] = true; this.testResults[key] = null;
      try { this.testResults[key] = await this.api(url, { method: 'POST', ...(body ? { body } : {}) }); }
      catch (error) { this.testResults[key] = { ok: false, error: error.message }; }
      finally { this.testing[key] = false; }
    },
    testMail(kind) { return this.runTest(`mail_${kind}`, '/api/test/mail', { account: kind }); },
    testPaths() { return this.runTest('paths', '/api/test/paths'); },
    testYtdlp() { return this.runTest('ytdlp', '/api/test/ytdlp'); },
    testAi() {
      if (this.config.ai.provider === 'openai') {
        return this.runTest('ai', '/api/test/openai', this.config.ai.openai);
      }
      return this.runTest('ai', '/api/test/ollama');
    },
    testResultMessage(key) {
      const r = this.testResults[key]; if (!r) return '';
      if (r.ok) return r.message || r.version || 'Verbindung erfolgreich';
      return r.error || 'Test fehlgeschlagen';
    },
    async loadMaintenance() {
      try {
        const [backups, logs] = await Promise.all([this.api('/api/config/backups/list'), this.api('/api/config/logs/stats')]);
        this.maintenance = { ...backups, logs };
      } catch (error) { this.maintenanceOutput = error.message; }
    },
    async runDbBackup() {
      this.maintenanceBusy = true;
      try { const r = await this.api('/api/config/backups/run-now', { method: 'POST' }); this.maintenanceOutput = r.message || r.path || 'Datenbank gesichert'; this.notify('Datenbank gesichert'); await this.loadMaintenance(); }
      catch (error) { this.maintenanceOutput = error.message; this.notify(error.message, 'error'); }
      finally { this.maintenanceBusy = false; }
    },
    async cleanupLogs() {
      this.maintenanceBusy = true;
      try { const r = await this.api('/api/config/logs/cleanup', { method: 'POST' }); this.maintenanceOutput = `${r.deleted || 0} Logdateien gelöscht`; this.notify(this.maintenanceOutput); await this.loadMaintenance(); }
      catch (error) { this.maintenanceOutput = error.message; this.notify(error.message, 'error'); }
      finally { this.maintenanceBusy = false; }
    },

    async openLocalBrowser(path, callback, title = 'Ordner wählen') {
      this.browser = { show: true, title, currentPath: '', parent: null, entries: [], loading: false, callback };
      await this.loadBrowserPath(path || '');
    },
    async loadBrowserPath(path) {
      this.browser.loading = true;
      try {
        const r = await this.api(`/api/browse/local?path=${encodeURIComponent(path || '')}`);
        this.browser.currentPath = r.path || ''; this.browser.parent = r.parent; this.browser.entries = r.entries || [];
      } catch (error) { this.notify(error.message, 'error'); }
      finally { this.browser.loading = false; }
    },
    async browserMkdir() {
      const name = window.prompt('Name des neuen Ordners:');
      if (!name) return;
      if (name.includes('/') || name.includes('\\') || name === '.' || name === '..') return this.notify('Ungültiger Ordnername', 'error');
      try {
        const path = `${this.browser.currentPath.replace(/\/$/, '')}/${name}`;
        await this.api('/api/browse/local/mkdir', { method: 'POST', body: { path } });
        await this.loadBrowserPath(path);
      } catch (error) { this.notify(error.message, 'error'); }
    },
    browserPick() { if (this.browser.callback) this.browser.callback(this.browser.currentPath); this.browserCancel(); },
    browserCancel() { this.browser = { show: false, title: '', currentPath: '', parent: null, entries: [], loading: false, callback: null }; },

    scrollToConfig(id) { document.getElementById(`config-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); },
    closeTopLayer() {
      if (this.browser.show) this.browserCancel();
      else if (this.recipeDetail) this.recipeDetail = null;
      else if (this.editingItem) this.editingItem = null;
      else if (this.currentLog) this.currentLog = '';
    },
    statusText(value) { return ({ running: 'Läuft', ok: 'Erfolgreich', partial: 'Teilweise', error: 'Fehler', skipped: 'Übersprungen' })[value] || value || 'Unbekannt'; },
    formatDate(value) { if (!value) return '—'; const d = new Date(Number(value) * 1000); return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium' }).format(d); },
    formatDateTime(value) { if (!value) return '—'; const d = new Date(Number(value) * 1000); return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(d); },
    formatDuration(seconds) { const s = Math.max(0, Number(seconds) || 0); const m = Math.floor(s / 60); return m ? `${m} Min. ${Math.round(s % 60)} Sek.` : `${Math.round(s)} Sek.`; },
    shortUrl(value) { const s = String(value || ''); return s.length > 70 ? `${s.slice(0, 67)}…` : s; },
    notify(message, type = 'ok') {
      this.toast = { show: true, message: String(message || ''), type };
      clearTimeout(this._toastTimer); this._toastTimer = setTimeout(() => { this.toast.show = false; }, 3500);
    },
    enhanceUi() {
      document.querySelectorAll('table').forEach(table => {
        const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
        table.querySelectorAll('tbody tr').forEach(row => [...row.children].forEach((cell, index) => { if (headers[index]) cell.dataset.label = headers[index]; }));
      });
      if (window.matchMedia('(max-width: 700px)').matches) {
        document.querySelectorAll('.config-section').forEach((section, index) => {
          if (section.dataset.accordionReady) return;
          const head = section.querySelector(':scope > .head'); const body = section.querySelector(':scope > .body');
          if (!head || !body) return;
          section.dataset.accordionReady = 'true';
          if (index > 0) section.classList.add('collapsed');
          head.setAttribute('role', 'button'); head.setAttribute('tabindex', '0');
          const toggle = event => { if (event.target.closest('button,input,select,a')) return; section.classList.toggle('collapsed'); };
          head.addEventListener('click', toggle); head.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') toggle(event); });
        });
      }
    },
  };
}
