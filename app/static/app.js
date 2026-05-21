// Scrapper Manager - Frontend Logic
function scrapperApp() {
  return {
    page: 'dashboard',
    config: {},
    rcloneArgsText: '',
    pending: [],
    history: [],
    jobs: [],
    status: { scraper: null, backup: null, pending_count: 0 },
    lastScraper: null,
    lastBackup: null,
    currentLog: '',
    currentLogJob: null,
    toast: { show: false, message: '', type: 'ok' },
    recipeTypes: ['Hauptgericht','Vorspeise','Nachspeise','Snack','Frühstück','Getränk','Beilage'],
    weddingCategories: ['Deko','Foto','Basteln','Einladung','Standesamt','Outfit','Catering','Sonstiges'],
    _statusTimer: null,

    init() {
      this.refreshStatus();
      this.loadRecentJobs();
      this._statusTimer = setInterval(() => this.refreshStatus(), 4000);
      this._progressTimer = setInterval(() => this.refreshProgress(), 3000);
      this.refreshProgress();
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
      if (j.kind === 'backup') {
        return `${s.ok_count||0}/${s.total_pairs||0} Paare${s.dry_run ? ' (dry)' : ''}`;
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
        this.lastBackup = all.find(j => j.kind === 'backup' && j.status === 'ok');
      } catch(e) {}
    },
    async refreshProgress() {
      // Nur abfragen wenn Job läuft, sonst Last-Info behalten
      if (this.status && this.status.backup) {
        try { this.backupProgress = await this.api('GET', '/api/jobs/backup/progress'); } catch(e) {}
      } else if (this.backupProgress && this.backupProgress.running) {
        // war running, jetzt nicht mehr → finalen Stand laden
        try { this.backupProgress = await this.api('GET', '/api/jobs/backup/progress'); } catch(e) {}
      }
      if (this.status && this.status.scraper) {
        try { this.scraperProgress = await this.api('GET', '/api/jobs/scraper/progress'); } catch(e) {}
      } else if (this.scraperProgress && this.scraperProgress.running) {
        try { this.scraperProgress = await this.api('GET', '/api/jobs/scraper/progress'); } catch(e) {}
      }
    },

    // ------------- Jobs -------------
    async runScraper() {
      await this.api('POST', '/api/jobs/scraper/run');
      this.showToast('Scraper gestartet');
      this.refreshStatus();
    },
    async runBackup(dryRun, pairs) {
      let url = `/api/jobs/backup/run?dry_run=${dryRun}`;
      if (pairs && pairs.length) url += '&pairs=' + encodeURIComponent(pairs.join(','));
      await this.api('POST', url);
      this.showToast(`Backup gestartet${pairs ? ' (' + pairs.join(', ') + ')' : ''}${dryRun ? ' [dry-run]' : ''}`);
      this.refreshStatus();
    },
    async cancelBackup() {
      if (!confirm('Backup wirklich abbrechen? Alle laufenden rclone-Prozesse werden gestoppt.')) return;
      try {
        await this.api('POST', '/api/jobs/backup/cancel', {});
        this.showToast('Cancel-Signal gesendet', 'ok');
        this.refreshStatus();
      } catch(e) {}
    },
    async loadJobs() {
      this.jobs = await this.api('GET', '/api/jobs/list?limit=50');
    },
    async loadJobLog(id) {
      this.currentLogJob = id;
      this.currentLog = 'lädt…';
      const r = await this.api('GET', `/api/jobs/${id}/log?tail=800`);
      this.currentLog = r.log || '(leer)';
    },

    // ------------- Pending -------------
    async loadPending() {
      this.pending = await this.api('GET', '/api/pending');
      this.pending.forEach(p => {
        p._name = (p.ai_suggestion && p.ai_suggestion.name) && p.ai_suggestion.name !== 'Unbekannt'
                  ? p.ai_suggestion.name : '';
        p._type = (p.ai_suggestion && p.ai_suggestion.type) && p.ai_suggestion.type !== 'Unbekannt'
                  ? p.ai_suggestion.type : '';
        p._category = (p.ai_suggestion && p.ai_suggestion.category) && p.ai_suggestion.category !== 'Unbekannt'
                      ? p.ai_suggestion.category : '';
      });
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

    // ------------- Config -------------
    async loadConfig() {
      const cfg = await this.api('GET', '/api/config');
      // Defaults absichern damit Alpine-Bindings nicht meckern
      cfg.web ||= {};
      cfg.paths ||= {};
      cfg.mail ||= {};
      cfg.mail.recipe ||= {};
      cfg.mail.wedding ||= {};
      cfg.ai ||= {};
      cfg.ai.ollama ||= {};
      cfg.ai.openai ||= {};
      cfg.telegram ||= {};
      cfg.backup ||= {};
      cfg.backup.pairs ||= [];
      cfg.backup.rclone_args ||= [];
      cfg.ytdlp ||= {};
      this.config = cfg;
      this.rcloneArgsText = (cfg.backup.rclone_args || []).join('\n');
      this.recipeTypes = cfg.recipe_types || this.recipeTypes;
      this.weddingCategories = cfg.wedding_categories || this.weddingCategories;
      this.loadSchedule();
    },
    async saveConfig() {
      // rclone-args aus textarea zurück in array
      this.config.backup.rclone_args = this.rcloneArgsText
        .split('\n').map(s => s.trim()).filter(Boolean);
      await this.api('PUT', '/api/config', this.config);
      await this.api('POST', '/api/config/reload', null);
      this.showToast('Konfiguration gespeichert');
    },

    // ------------- Tests -------------
    testing: {
      mail_recipe: false, mail_wedding: false,
      ollama: false, openai: false,
      tg_recipe: false, tg_wedding: false, tg_backup: false,
      rclone: false, paths: false, ytdlp: false,
      schedule_preview: false, schedule_save: false,
    },
    testResults: {},
    schedule: { scraper: { oncalendar: '', next_run: null }, backup: { oncalendar: '', next_run: null } },
    scheduleEdit: { scraper: '', backup: '' },
    schedulePreview: null,
    backupProgress: null,
    scraperProgress: null,
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
    testOllama() { this.runTest('ollama', '/api/test/ollama'); },
    testOpenAI() { this.runTest('openai', '/api/test/openai'); },
    testTelegram(bot) { this.runTest('tg_' + bot, '/api/test/telegram', { bot }); },
    testRclone(pairIndex = null) {
      const body = pairIndex !== null ? { pair_index: pairIndex } : {};
      this.runTest(pairIndex !== null ? 'rclone_' + pairIndex : 'rclone', '/api/test/rclone', body);
    },
    testPaths() { this.runTest('paths', '/api/test/paths'); },
    testYtdlp() { this.runTest('ytdlp', '/api/test/ytdlp'); },

    // ------------- Schedule / Timer -------------
    async loadSchedule() {
      try {
        this.schedule = await this.api('GET', '/api/schedule');
        this.scheduleEdit.scraper = this.schedule.scraper.oncalendar || '';
        this.scheduleEdit.backup  = this.schedule.backup.oncalendar  || '';
      } catch(e) {}
    },
    async previewSchedule() {
      this.testing.schedule_preview = true;
      try {
        this.schedulePreview = await this.api('POST', '/api/schedule/preview', {
          scraper: this.scheduleEdit.scraper,
          backup:  this.scheduleEdit.backup,
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
          backup:  this.scheduleEdit.backup,
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
      mode: 'local',          // 'local' | 'rclone'
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
    openRcloneBrowser(initialPath, callback, title) {
      this.browser.mode = 'rclone';
      this.browser.callback = callback;
      this.browser.title = title || 'rclone-Pfad wählen';
      this.browser.show = true;
      this.loadBrowserPath(initialPath || '');
    },
    async loadBrowserPath(path) {
      this.browser.loading = true;
      try {
        const endpoint = this.browser.mode === 'local' ? '/api/browse/local' : '/api/browse/rclone';
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
    async reanalyzeAll() {
      if (!confirm('Alle ' + this.pending.length + ' Pending-Items neu analysieren? Das kann je nach KI-Modell ein paar Minuten dauern.')) return;
      this.reanalyzingAll = true;
      try {
        const r = await this.api('POST', '/api/pending/reanalyze-all', {});
        if (r) {
          this.showToast(`Fertig: ${r.auto_saved} eingeordnet, ${r.still_pending} weiterhin pending, ${r.errors} Fehler`);
          await this.loadPending();
          await this.refreshStatus();
        }
      } catch(e) {} finally {
        this.reanalyzingAll = false;
      }
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
    SCHEDULE_PRESETS_BACKUP: [
      { label: 'Täglich 02:00', value: '*-*-* 02:00:00' },
      { label: 'Täglich 03:00', value: '*-*-* 03:00:00' },
      { label: 'Täglich 04:00', value: '*-*-* 04:00:00' },
      { label: 'Mo-Fr 03:00', value: 'Mon..Fri *-*-* 03:00:00' },
      { label: 'Wöchentlich So 02:00', value: 'Sun *-*-* 02:00:00' },
      { label: 'Alle 6 Std',  value: '00,06,12,18:00' },
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
  };
}
