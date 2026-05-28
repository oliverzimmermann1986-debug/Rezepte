// Scrapper Manager - Frontend Logic
function scrapperApp() {
  return {
    page: 'recipes',
    moreDrawerOpen: false,  // Bottom-Sheet mit Dashboard/Historie/Jobs/Config (Mobile only)

    // Zentrale Navigations-Helper — page-switch plus die zugehörigen
    // Loader. Vorher waren die Loader direkt im @click jeder nav-item,
    // was beim Refactor (z.B. neue Bottom-Sheet) duplication erzeugte.
    navTo(targetPage) {
      this.page = targetPage;
      // Drawer immer schließen wenn man eine andere Page wählt
      this.moreDrawerOpen = false;
      switch (targetPage) {
        case 'pending':   this.loadPending(); this.loadFailedDownloads(); break;
        case 'history':   this.loadHistory(); break;
        case 'jobs':      this.loadJobs(); break;
        case 'config':    this.loadConfig(); this.loadUsers(); break;
        case 'recipes':   this.loadRecipes(); this.loadFacets(); break;
        case 'cart':      this.loadCart(); break;
        case 'audit':     this.loadAudit(); break;
        case 'master':    this.loadMaster(); break;
        // dashboard: keine spezielle Loader, x-show triggert die Widgets
      }
    },
    config: {},
    rcloneArgsText: '',
    rcloneFilterText: '',
    pairArgsText: {},      // idx -> textarea content for per-pair args
    pairScheduleData: {},  // name -> {schedule, last_run, next_run, due}
    pairScheduleDefault: '0 3 * * *',
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
    status: { scraper: null, backup: null, pending_count: 0 },
    lastScraper: null,
    lastBackup: null,
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
      filters: { search: '', type: '', category: '', tag_ids: [], ingredients: [], limit: 60, offset: 0 },
      filterDrawerOpen: false,  // nur auf Mobile sichtbar: Filter als Drawer statt Sidebar
      facets: { types: [], categories: [], tags: [], ingredients: [] },
      extractionRunning: false, extractionPending: 0,
      extractionStats: {}, _pollTimer: null,
    },
    cart: {
      items: [],
      add: { name: '', amount: null, unit: '' },
    },
    pushingToEinkauf: false,    // Loading-state für Push-Button
    audit: {
      data: null,
      summary: null,
      loading: false,
      withAi: false,
      // KI-Sanity-Background-Job: Progress beim Polling
      aiSanity: { running: false, processed: 0, total: 0, findings: 0, pollHandle: null },
      bulkApplying: false,    // Loading-state für apply-all
      activeTab: 'gaps',      // Audit-Tab-State: gaps / fs / ai / duplicates
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
      addForm: { username: '', password: '', role: 'user' },
    },
    recipeDetail: {
      show: false, data: null, newTag: '',
      cooking: false, extracting: false,
      multiplier: 1,    // Portionen-Skalierung beim Kochen
      cookMode: false,  // Koch-Modus: nur Schritte, große Schrift, Wake-Lock
      wakeLockActive: false,  // UI-Indikator ob Wake-Lock greift
      // Zutaten-Edit-Modus: lokaler working-copy bis 'Speichern' geklickt wird
      editingIngredients: false,
      editIngs: [],
      savingIngredients: false,
    },
    _wakeLock: null,
    // Per-Schritt-Timer (key = step.id, value = {status, remaining, intervalId})
    // Bewusst auf scrapperApp-Top-Level damit Alpine reactivity trackt.
    timers: {},
    _audioCtx: null,

    init() {
      this.loadRecentJobs();
      this.loadStats();
      this.loadHddStatus();   // Externe-HDD-Card auf dem Dashboard
      // Job-/Stats-Karten brauchen weiterhin gelegentliches Reload (kein
      // Live-Update, da nur Snapshot-Daten)
      this._jobsTimer = setInterval(() => this.loadRecentJobs(), 15000);
      this._statsTimer = setInterval(() => this.loadStats(), 60000);
      this._hddTimer = setInterval(() => this.loadHddStatus(), 30000);
      // Live-Status via Server-Sent-Events. Eine offene Connection statt
      // 2+ req/s Polling. Browser-EventSource reconnected automatisch bei
      // Drop. Wenn der Endpoint nicht da ist (alte Backend-Version): Fall-
      // back auf setInterval-Polling.
      this._startEventStream();
    },
    _startEventStream() {
      try {
        const es = new EventSource('/api/events');
        this._eventSource = es;
        es.addEventListener('status', (e) => {
          try { this.status = JSON.parse(e.data); } catch(_) {}
        });
        es.addEventListener('backup_progress', (e) => {
          try { this.backupProgress = JSON.parse(e.data); } catch(_) {}
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
      if (j.kind === 'backup') {
        return `${s.ok_count||0}/${s.total_pairs||0} Paare${s.dry_run ? ' (dry)' : ''}`;
      }
      if (j.kind === 'reanalyze') {
        return `${s.auto_saved||0} eingeordnet, ${s.still_pending||0} pending, ${s.errors||0} err (${s.processed||0}/${s.total||0})`;
      }
      if (j.kind === 'quicksync') {
        return `${s.verb || s.direction || '?'}: ${s.remote || '?'} ⇄ ${s.local || '?'}${s.dry_run ? ' (dry)' : ''}`;
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
        // Quick-Sync zählt auch als Backup für die Dashboard-Card
        this.lastBackup = all.find(j => (j.kind === 'backup' || j.kind === 'quicksync') && j.status === 'ok');
      } catch(e) {}
    },
    async loadStats() {
      this.statsLoading = true;
      try {
        const [jobs, conf, perPair] = await Promise.all([
          this.api('GET', '/api/stats/jobs-per-day?days=14'),
          this.api('GET', '/api/stats/confidence-histogram?buckets=10'),
          this.api('GET', '/api/stats/per-pair?days=30'),
        ]);
        this.stats = { jobs, conf, perPair };
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
      const kinds = Object.keys(s.series);
      const palette = { scraper: '#f97316', backup: '#22c55e', quicksync: '#06b6d4', reanalyze: '#a855f7' };
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
    async runBackup(dryRun, pairs) {
      let url = `/api/jobs/backup/run?dry_run=${dryRun}`;
      if (pairs && pairs.length) url += '&pairs=' + encodeURIComponent(pairs.join(','));
      await this.api('POST', url);
      this.showToast(`Backup gestartet${pairs ? ' (' + pairs.join(', ') + ')' : ''}${dryRun ? ' [dry-run]' : ''}`);
      this.refreshStatus();
    },
    async runQuickSync() {
      if (!this.quickSync.remote_path || !this.quickSync.local_path) {
        this.showToast('Remote- und Local-Pfad sind Pflicht', 'error');
        return;
      }
      this.quickSync.running = true;
      try {
        const r = await this.api('POST', '/api/jobs/backup/quick', {
          remote_path: this.quickSync.remote_path,
          local_path: this.quickSync.local_path,
          direction: this.quickSync.direction,
          mode: this.quickSync.mode,
          dry_run: this.quickSync.dry_run,
        });
        if (r && r.job_id) {
          this.showToast('Quick-Sync gestartet (Job #' + r.job_id + ')');
          this.refreshStatus();
          this.showQuickSync = false;
        }
      } catch(e) {
        this.showToast('Fehler: ' + (e.message || e), 'error');
      } finally {
        this.quickSync.running = false;
      }
    },

    async cancelBackup() {
      if (!confirm('Backup wirklich abbrechen? Alle laufenden rclone-Prozesse werden gestoppt.')) return;
      try {
        await this.api('POST', '/api/jobs/backup/cancel', {});
        this.showToast('Cancel-Signal gesendet', 'ok');
        this.refreshStatus();
      } catch(e) {}
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
    // Benutzer-Verwaltung (Multi-User-Auth, admin-only)
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
          role: f.role,
        });
        if (r && r.ok) {
          this.showToast(`✓ Benutzer '${r.username}' angelegt`);
          this.users.showAdd = false;
          this.users.addForm = { username: '', password: '', role: 'user' };
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

    async setUserRole(u, newRole) {
      if (u.role === newRole) return;
      const r = await this.api('PATCH', `/api/users/${u.id}`, { role: newRole });
      if (r && r.ok) {
        this.showToast(`✓ Rolle: ${newRole}`);
        await this.loadUsers();
      } else {
        // Bei Fehler (z.B. letzter Admin) Liste neu laden — Select wieder
        // auf alten Wert zurück
        await this.loadUsers();
      }
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
      cfg.backup ||= {};
      cfg.backup.pairs ||= [];
      cfg.backup.rclone_args ||= [];
      cfg.ytdlp ||= {};
      cfg.webhooks ||= [];
      cfg.einkauf ||= { api_url: '', auto_consolidate: true };
      this.config = cfg;
      this.rcloneArgsText = (cfg.backup.rclone_args || []).join('\n');
      // Pro-Pair-Args ins UI laden
      this.pairArgsText = {};
      (cfg.backup.pairs || []).forEach((p, idx) => {
        this.pairArgsText[idx] = (p.rclone_args || []).join('\n');
      });
      this.recipeTypes = cfg.recipe_types || this.recipeTypes;
      this.weddingCategories = cfg.wedding_categories || this.weddingCategories;
      this.loadSchedule();
      this.loadFilterFile();   // rclone-Filter parallel laden
      this.loadMaintenance();  // Wartungs-Stats parallel
      this.loadPairScheduleInfo();
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
    async runBackupNow() {
      this.maintBusy = true;
      this.maintenanceOutput = '';
      try {
        const r = await this.api('POST', '/api/config/backups/run-now');
        this.maintenanceOutput = (r.stdout || '') + (r.stderr ? '\n[STDERR]\n' + r.stderr : '');
        this.showToast(r.ok ? 'Backup ok' : 'Backup-Fehler', r.ok ? 'ok' : 'error');
        await this.loadMaintenance();
      } catch(e) {
        this.showToast('Backup-Fehler: ' + e, 'error');
      } finally {
        this.maintBusy = false;
      }
    },
    async saveConfig() {
      // rclone-args aus textarea zurück in array
      this.config.backup.rclone_args = this.rcloneArgsText
        .split('\n').map(s => s.trim()).filter(Boolean);
      // Pfad-Werte trimmen damit nicht versehentlich Leerzeichen reinrutschen
      // (führt sonst zu 'path does not exist' beim healthz/deep)
      if (this.config.paths) {
        ['recipe_dir', 'wedding_dir', 'temp_dir', 'logs_dir'].forEach(k => {
          if (typeof this.config.paths[k] === 'string') {
            this.config.paths[k] = this.config.paths[k].trim();
          }
        });
      }
      if (this.config.backup) {
        ['filter_file', 'backup_dir', 'bwlimit'].forEach(k => {
          if (typeof this.config.backup[k] === 'string') {
            this.config.backup[k] = this.config.backup[k].trim();
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
      rclone: false, paths: false, ytdlp: false,
      schedule_preview: false, schedule_save: false,
      webhook: -1,   // Index des gerade getesteten Webhook (-1 = keiner)
    },
    testResults: {},
    schedule: { scraper: { oncalendar: '', next_run: null }, backup: { oncalendar: '', next_run: null } },
    scheduleEdit: { scraper: '', backup: '' },
    schedulePreview: null,
    backupProgress: null,
    scraperProgress: null,
    reanalyzeProgress: null,
    _progressTimer: null,
    quickSync: {
      remote_path: '',
      local_path: '',
      direction: 'bisync',
      mode: 'bisync',
      dry_run: false,
      running: false,
    },
    showQuickSync: false,
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
    testRclone(pairIndex = null) {
      const body = pairIndex !== null ? { pair_index: pairIndex } : {};
      this.runTest(pairIndex !== null ? 'rclone_' + pairIndex : 'rclone', '/api/test/rclone', body);
    },
    testPaths() { this.runTest('paths', '/api/test/paths'); },
    testYtdlp() { this.runTest('ytdlp', '/api/test/ytdlp'); },

    // ---------------- Backup-Pairs (Pro-Pair-Args) ----------------
    addBackupPair() {
      const arr = this.config.backup.pairs;
      const idx = arr.length;
      arr.push({ name: '', remote: '', local: '', schedule: '' });
      this.pairArgsText[idx] = '';
    },
    async loadPairScheduleInfo() {
      // Holt für alle Pairs die Schedule-Info vom Backend (next_run etc.)
      try {
        const r = await this.api('GET', '/api/jobs/backup/schedule');
        const info = {};
        for (const p of (r.pairs || [])) {
          info[p.name] = p;
        }
        this.pairScheduleData = info;
        this.pairScheduleDefault = r.default_schedule;
      } catch(e) {
        // Endpoint evtl. nicht verfügbar - keine Info, ignorieren
      }
    },
    pairScheduleInfo(name) {
      const p = this.pairScheduleData && this.pairScheduleData[name];
      if (!p) return null;
      if (p.reason === 'disabled' || (p.reason || '').startsWith('schedule=')) {
        return `(${p.reason})`;
      }
      const lr = p.last_run ? new Date(p.last_run * 1000).toLocaleString('de-DE') : 'noch nie';
      const nr = p.next_run ? new Date(p.next_run * 1000).toLocaleString('de-DE') : '—';
      const dueLabel = p.due ? ' · ⏰ jetzt fällig' : '';
      return `letzter: ${lr} · nächster: ${nr}${dueLabel}`;
    },
    async runSinglePair(name) {
      if (!name) { this.showToast('Pair-Name fehlt - erst speichern', 'error'); return; }
      try {
        const r = await this.api('POST', `/api/jobs/backup/run-pair/${encodeURIComponent(name)}`);
        if (r.ok) {
          this.showToast(`Sync für ${name} gestartet (Job ${r.job_id})`, 'ok');
        } else {
          this.showToast('Sync-Start fail', 'error');
        }
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      }
    },
    syncPairArgs(idx) {
      // Textarea -> pair.rclone_args array. Leer = Feld weglassen.
      const text = (this.pairArgsText[idx] || '').trim();
      const arr = text.split('\n').map(s => s.trim()).filter(Boolean);
      const pair = this.config.backup.pairs[idx];
      if (!pair) return;
      if (arr.length === 0) {
        delete pair.rclone_args;
      } else {
        pair.rclone_args = arr;
      }
    },

    // ---------------- rclone Filter-Datei ----------------
    async loadFilterFile() {
      this.filterBusy = true;
      try {
        const r = await this.api('GET', '/api/config/filter-file');
        this.rcloneFilterText = (r && r.content) || '';
      } catch(e) {
        // 404/error: leer lassen (Datei existiert noch nicht)
        this.rcloneFilterText = '';
      } finally {
        this.filterBusy = false;
      }
    },
    async saveFilterFile() {
      this.filterBusy = true;
      try {
        const r = await this.api('PUT', '/api/config/filter-file',
                                 { content: this.rcloneFilterText });
        this.showToast(`Filter gespeichert (${r.bytes} Bytes)`, 'ok');
      } catch(e) {
        this.showToast('Speichern fehlgeschlagen: ' + e, 'error');
      } finally {
        this.filterBusy = false;
      }
    },

    // ---------------- Webhooks ----------------
    addWebhook() {
      if (!this.config.webhooks) this.config.webhooks = [];
      this.config.webhooks.push({
        name: '', url: '', enabled: true,
        events: ['scraper_done', 'backup_done', 'job_failed'],
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
      // State sofort leeren - sonst zeigt das Modal bei einem API-Fehler
      // noch die Daten vom vorherigen (z.B. lokalen) Browse-Vorgang.
      this.browser.entries = [];
      this.browser.suggestedRoots = [];
      this.browser.parent = null;
      this.browser.isRoot = false;
      this.browser.currentPath = path || '';
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

    pickFolderForQuick(which) {
      if (which === 'remote') {
        this.openRcloneBrowser(
          this.quickSync.remote_path || '',
          (path) => { this.quickSync.remote_path = path; },
          'Remote-Pfad für Quick-Sync'
        );
      } else {
        this.openLocalBrowser(
          this.quickSync.local_path || '/mnt',
          (path) => { this.quickSync.local_path = path; },
          'Lokalen Pfad für Quick-Sync'
        );
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
      f.tag_ids.forEach(id => params.append('tag_id', id));
      f.ingredients.forEach(name => params.append('ingredient', name));
      params.set('limit', f.limit);
      params.set('offset', f.offset);
      return params.toString();
    },

    // ── Recipe-Liste + Facets ─────────────────────────────────────────
    async loadRecipes() {
      this.recipes.loading = true;
      try {
        const r = await this.api('GET', '/api/recipes?' + this._buildRecipeQuery());
        if (!r) return;
        this.recipes.items = r.items || [];
        this.recipes.total = r.total || 0;
        this.recipes.extractionRunning = !!r.extraction_running;
        // Wenn die Extraction läuft, periodisch refreshen (Status + Liste)
        this._scheduleExtractionPoll();
      } finally {
        this.recipes.loading = false;
      }
    },

    async loadFacets() {
      const r = await this.api('GET', '/api/recipes/facets');
      if (!r) return;
      this.recipes.facets = r;
    },

    resetFilters() {
      this.recipes.filters = {
        search: '', type: '', category: '', tag_ids: [], ingredients: [],
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
          this.recipes.extractionRunning = !!s.running;
          this.recipes.extractionStats = s.stats || {};
          this.recipes.extractionPending = s.stats?.pending || 0;
          if (!s.running) {
            // Worker fertig — Liste + Facets nachladen damit Zutaten-Filter
            // jetzt belegt ist, und Polling stoppen
            clearInterval(this.recipes._pollTimer);
            this.recipes._pollTimer = null;
            if (this.page === 'recipes') {
              this.loadRecipes();
              this.loadFacets();
            }
          }
        } catch(e) {}
      };
      tick();  // sofort einmal
      this.recipes._pollTimer = setInterval(tick, 5000);
    },

    // ── Detail-Modal ──────────────────────────────────────────────────
    async openRecipe(id) {
      this.recipeDetail.show = true;
      this.recipeDetail.data = null;
      this.recipeDetail.newTag = '';
      this.recipeDetail.multiplier = 1;
      this.recipeDetail.cookMode = false;
      const r = await this.api('GET', '/api/recipes/' + id);
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
    startEditIngredients() {
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
      // Leere Zutaten (Name leer) werden serverseitig sowieso geskippt,
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

    // ── Einkaufskorb ──────────────────────────────────────────────────
    async loadCart() {
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
