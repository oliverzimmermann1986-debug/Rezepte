// Scrapper Manager - Frontend Logic
function scrapperApp() {
  return {
    page: 'dashboard',
    config: {},
    rcloneArgsText: '',
    rcloneFilterText: '',
    pairArgsText: {},      // idx -> textarea content for per-pair args
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

    init() {
      this.loadRecentJobs();
      this.loadStats();
      // Job-/Stats-Karten brauchen weiterhin gelegentliches Reload (kein
      // Live-Update, da nur Snapshot-Daten)
      this._jobsTimer = setInterval(() => this.loadRecentJobs(), 15000);
      this._statsTimer = setInterval(() => this.loadStats(), 60000);
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
    async reanalyzeHistoryOne(item) {
      this.reanalyzingHistoryUrl = item.url;
      try {
        const r = await this.api('POST', '/api/history/reanalyze',
                                  { url: item.url, dry_run: false });
        if (!r.ok) {
          this.showToast('Reanalyze fail: ' + (r.error || 'unbekannt'), 'error');
          return;
        }
        const action = r.action;
        if (action === 'updated') {
          this.showToast(`Aktualisiert: "${r.old.name}" → "${r.new.name}" (conf ${Math.round(r.new.confidence * 100)}%)`, 'ok');
          await this.loadHistory();
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
      const msg = dry_run
        ? 'Dry-Run starten? Liest alle History-URLs neu via yt-dlp und schickt durch den AI-Provider. Zeigt nur was sich ändern WÜRDE, kein DB-Write.'
        : 'Alle History-URLs neu analysieren? Aktualisiert die Klassifikation, wenn der neue Provider sicher ist. Files werden NICHT verschoben.\n\nDas kann je nach History-Größe ein paar Minuten dauern.';
      if (!confirm(msg)) return;
      try {
        this.historyReanalyzing = true;
        const r = await this.api('POST', '/api/history/reanalyze-all',
                                  { dry_run, limit: 1000 });
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
    async loadConfig() {
      const cfg = await this.api('GET', '/api/config');
      // Defaults absichern damit Alpine-Bindings nicht meckern
      cfg.web ||= {};
      cfg.paths ||= {};
      cfg.mail ||= {};
      cfg.mail.recipe ||= {};
      cfg.mail.wedding ||= {};
      cfg.ai ||= {};
      cfg.ai.provider ||= 'ollama';
      cfg.ai.ollama ||= {};
      cfg.ai.openai ||= { api_key: '', model: 'gpt-4o-mini', base_url: '', timeout: 30 };
      cfg.backup ||= {};
      cfg.backup.pairs ||= [];
      cfg.backup.rclone_args ||= [];
      cfg.ytdlp ||= {};
      cfg.webhooks ||= [];
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
      ollama: false, openai: false,
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
    testOllama() { this.runTest('ollama', '/api/test/ollama'); },
    async testOpenAI() {
      // Aktuelle UI-Werte mitschicken, damit der User nicht erst speichern muss.
      const oai = (this.config && this.config.ai && this.config.ai.openai) || {};
      const body = {
        api_key: oai.api_key || '',
        model: oai.model || '',
        base_url: oai.base_url || '',
      };
      this.testing.openai = true;
      this.testResults.openai = null;
      try {
        const r = await this.api('POST', '/api/test/openai', body);
        this.testResults.openai = r;
      } catch(e) {
        this.testResults.openai = { ok: false, error: String(e) };
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
      arr.push({ name: '', remote: '', local: '' });
      this.pairArgsText[idx] = '';
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
  };
}
