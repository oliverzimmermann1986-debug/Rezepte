// imports: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["imports"] = function () {
    return {

    // Zustimmung gilt nur für die jetzt gestartete Aktion, nie für spätere Importe.
    confirmAIProcessing(action, dataDescription = 'Rezepttexte, Fotos, PDF-Inhalte und gegebenenfalls Video- und Audiodaten', details = '') {
      return confirm(`${action}?\n\n${dataDescription} werden über den Rezeptserver an OpenAI zur KI-Verarbeitung für diese Aktion übermittelt. Je nach Quelle gehören Erkennung, Übersetzung, die Ermittlung von Zutaten und Schritten sowie die Erzeugung eines Rezeptbilds dazu. Auch enthaltene personenbezogene Angaben können übertragen werden.\n\n${details ? details + '\n\n' : ''}Mit OK erlaubst du diese Übertragung für diese Aktion. Abbrechen startet keine Verarbeitung. Datenschutzhinweise findest du unter Datenschutz.`);
    },

    aiProcessingPayload(payload = {}) {
      return { ...payload, ai_processing_consent: 'openai-recipe-v1' };
    },

    // ------------- Jobs -------------
    async loadJobs() {
      this.jobs = await this.api('GET', '/api/jobs/list?limit=50');
    },
    async cleanupFailedJobs() {
      if (!this.canUseAdminTools()) return;
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

    async importUrl() {
      if (!this.canImport()) return;
      const url = (this.manualImportUrl || '').trim();
      if (!url || this.manualImporting) return;
      if (!this.confirmAIProcessing('Rezeptlink importieren')) return;
      this.manualImporting = true;
      try {
        const r = await this.api('POST', '/api/pending/import-url', this.aiProcessingPayload({ url, type: 'recipe', visibility: this.session.is_admin ? (this.manualImportVisibility || 'private') : 'private' }));
        if (r && r.ok) {
          this.manualImportNotice = r.message || 'Link übernommen';
          if (r.status === 'linked_global') this.showToast('Globales Rezept im Haushalt gespeichert');
          else if (r.status === 'duplicate') this.showToast('Bereits in deiner Sammlung');
          else this.showToast(`✓ ${r.status === 'pending' ? 'In Pending' : 'Importiert'}: ${r.name || url}`);
          this.manualImportUrl = '';
          if (this.session.is_admin) {
            this.loadPending();
            if (typeof this.loadAdminImport === 'function') this.loadAdminImport();
          }
          if (this.page === 'recipes') await this.loadRecipes();
        } else {
          this.showToast('Import fehlgeschlagen: ' + ((r && r.error) || 'Download/URL-Fehler'), 'error');
        }
      } catch (e) {
        // api() zeigt bei 4xx (z.B. Profil-URL) schon einen Fehler-Toast
      } finally {
        this.manualImporting = false;
      }
    },

    async importRecipeFile(event) {
      if (!this.canImport()) return;
      const input = event?.target;
      const file = input?.files?.[0];
      if (!file || this.manualImporting) return;
      if (!this.confirmAIProcessing('Foto oder PDF importieren', 'Die ausgewählte Datei einschließlich Fotos, PDF-Inhalten und lesbaren Texten')) {
        if (input) input.value = '';
        return;
      }
      this.manualImporting = true;
      this.manualImportNotice = '';
      try {
        if (file.size > 25 * 1024 * 1024) throw new Error('Die Datei darf höchstens 25 MB groß sein.');
        const form = new FormData();
        form.append('ai_processing_consent', this.aiProcessingPayload().ai_processing_consent);
        form.append('file', file, file.name);
        form.append('visibility', this.session.is_admin ? (this.manualImportVisibility || 'private') : 'private');
        const result = await this.fetchWithTimeout('/api/pending/import-file', { method: 'POST', body: form }, 120000, async response => {
          if (response.status === 401) { window.location.assign('/login'); throw new Error('Bitte erneut anmelden.'); }
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(this.apiErrorMessage(payload.detail || payload.error, 'Datei konnte nicht importiert werden.'));
          return payload;
        });
        this.manualImportNotice = result.message || 'Datei übernommen. Offene Vorschläge findest du unter Mein Konto.';
        await this.loadRecipes();
      } catch (error) {
        this.manualImportNotice = error.message;
        this.showToast(error.message, 'error');
      } finally {
        this.manualImporting = false;
        if (input) input.value = '';
      }
    },

    async loadPending() {
      let items = await this.api('GET', '/api/pending?visibility=global');
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
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
      const payload = {
        url: item.url,
        visibility: item.visibility || 'global',
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
        if (!this.confirmAIProcessing('Import speichern und Rezeptdaten vervollständigen', 'Die Rezeptquelle, Texte, Foto- und PDF-Inhalte sowie Rezepttitel und Zutaten', 'Beim Speichern können weitere Rezeptdaten erkannt, Texte übersetzt und ein Rezeptbild erzeugt werden.')) return;
      }
      const r = await this.api('POST', '/api/pending', action === 'save' ? this.aiProcessingPayload(payload) : payload);
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
      if (!this.canUseAdminTools()) return;
      if (!this.confirmAIProcessing('Diese Quelle erneut analysieren')) return;
      this.reanalyzingHistoryUrl = item.url;
      try {
        const r = await this.api('POST', '/api/history/reanalyze',
                                  this.aiProcessingPayload({ url: item.url, dry_run: false,
                                    auto_move: this.historyAutoMove }));
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
      if (!this.canUseAdminTools()) return;
      const moveHint = this.historyAutoMove
        ? '\n\n⚠️ Auto-Move ist aktiv - Files werden in neue Ordner verschoben!'
        : '\n\nNur DB wird aktualisiert, Files bleiben wo sie sind.';
      const msg = dry_run
        ? 'Dry-Run starten? Liest alle History-URLs neu via yt-dlp und schickt durch den AI-Provider. Zeigt nur was sich ändern WÜRDE, kein DB-/FS-Write.' + moveHint
        : 'Alle History-URLs neu analysieren? Aktualisiert die Klassifikation, wenn der neue Provider sicher ist.' + moveHint
          + '\n\nDas kann je nach History-Größe ein paar Minuten dauern.';
      if (!this.confirmAIProcessing('Alle History-Quellen erneut analysieren', 'Die Texte und Medien aller betroffenen History-Quellen', msg)) return;
      try {
        this.historyReanalyzing = true;
        const r = await this.api('POST', '/api/history/reanalyze-all',
                                  this.aiProcessingPayload({ dry_run, limit: 1000,
                                    auto_move: this.historyAutoMove }));
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
      if (!this.canUseAdminTools()) return;
      if (!this.junkItems || !this.junkItems.items || this.junkItems.items.length === 0) return;
      const n = this.junkItems.items.length;
      const moveHint = this.historyAutoMove
        ? ' Auto-Move ist aktiv - Files werden in neue Ordner verschoben.'
        : ' Nur DB-Updates, Files bleiben.';
      if (!this.confirmAIProcessing(`${n} Junk-Quellen erneut analysieren`, 'Die Texte und Medien dieser ausgewählten Quellen', moveHint)) return;

      this.historyReanalyzing = true;
      let updated = 0, moved = 0, unchanged = 0, lowConf = 0, failed = 0;
      for (const j of this.junkItems.items) {
        try {
          const r = await this.api('POST', '/api/history/reanalyze',
                                    this.aiProcessingPayload({ url: j.url, dry_run: false,
                                      auto_move: this.historyAutoMove }));
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
      if (!this.canUseAdminTools()) return;
      // One-Click: Junk finden + sofort aufräumen mit der aktuellen Auto-Move-Einstellung.
      const moveWarn = this.historyAutoMove
        ? '\n\n⚠️ Auto-Move ist AN - Files werden physisch in andere Ordner verschoben.'
        : '\n\nAuto-Move ist AUS - nur die DB-Namen werden aktualisiert, Files bleiben wo sie sind.';
      if (!this.confirmAIProcessing('Junk-Cleanup starten', 'Die Texte und Medien der als verdächtig erkannten Quellen', moveWarn
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
                                      this.aiProcessingPayload({ url: j.url, dry_run: false,
                                        auto_move: this.historyAutoMove }));
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
    async scanPendingPhoto(item, event) {
      if (!this.canImport() || (item.visibility !== 'private' && !this.canUseAdminTools())) return;
      const input = event && event.target;
      const file = input && input.files && input.files[0];
      if (input) input.value = '';
      if (!file || this.reanalyzing[item.url]) return;
      if (!this.confirmAIProcessing('Rezeptfoto auslesen', 'Das ausgewählte Foto mit lesbaren Texten und die zugehörige Rezeptquelle')) return;
      this.reanalyzing[item.url] = true;
      try {
        const form = new FormData();
        form.append('ai_processing_consent', this.aiProcessingPayload().ai_processing_consent);
        form.append('file', file, file.name || 'rezeptfoto.jpg');
        const endpoint = '/api/pending/scan-photo?visibility=' + (item.visibility === 'private' ? 'private' : 'global') + '&url=' + encodeURIComponent(item.url);
        const { response, result } = await this.fetchWithTimeout(
          endpoint, { method: 'POST', body: form }, 60000, async response => {
            let result = {};
            if (response.status !== 401) {
              try { result = await response.json(); }
              catch (error) { if (error?.name === 'AbortError') throw error; }
            }
            return { response, result };
          }
        );
        if (response.status === 401) {
          window.location = '/login';
          return;
        }
        if (!response.ok || !result.ok) {
          const detail = result.detail || result.error || `HTTP ${response.status}`;
          throw new Error(this.apiErrorMessage(detail, 'Foto-Scan fehlgeschlagen'));
        }
        if (result.action === 'auto_saved' || result.action === 'already_saved') {
          this.showToast(result.message || 'Foto erkannt und Rezept einsortiert', 'ok');
        } else {
          this.showToast(result.message || 'Foto erkannt; KI-Vorschlag aktualisiert', 'ok');
        }
        if (item.visibility === 'private') await this.loadAccount();
        else { await this.loadPending(); await this.refreshStatus(); }
      } catch (error) {
        this.showToast('Foto-Scan: ' + (error && error.message || error), 'error');
      } finally {
        delete this.reanalyzing[item.url];
      }
    },
    async reanalyzeOne(item) {
      if (!this.canUseAdminTools()) return;
      if (!this.confirmAIProcessing('Rezept erneut analysieren')) return;
      this.reanalyzing[item.url] = true;
      try {
        const r = await this.api('POST', '/api/pending/reanalyze', this.aiProcessingPayload({ url: item.url, visibility: 'global' }));
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

    // ---------------- Failed Downloads ----------------
    async loadFailedDownloads() {
      try {
        this.failedDownloads = await this.api('GET', '/api/pending/failed') || [];
      } catch(e) {
        // Endpoint evtl. nicht da (alte Backend-Version)
        this.failedDownloads = [];
      }
    },
    async retryFailed(url) {
      if (!this.canUseAdminTools()) return;
      this.retryingUrl = url;
      try {
        const r = await this.api('POST', '/api/pending/failed/'
                                   + encodeURIComponent(url) + '/retry');
        if (r && r.ok) {
          this.showToast('Fehlerzähler zurückgesetzt – die URL kann erneut importiert werden', 'ok');
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
      if (!this.canUseAdminTools()) return;
      const n = this.failedDownloads.length;
      if (!confirm('Alle ' + n + ' Fehlerzähler zurücksetzen? Danach können die URLs erneut importiert werden.')) return;
      try {
        const r = await this.api('POST', '/api/pending/failed/clear-all');
        this.showToast((r && r.cleared || n) + ' Counter zurückgesetzt', 'ok');
        await this.loadFailedDownloads();
      } catch(e) {
        this.showToast('Fehler: ' + e, 'error');
      }
    },

    async reanalyzeAll() {
      if (!this.canUseAdminTools()) return;
      if (!this.confirmAIProcessing('Alle ' + this.pending.length + ' offenen Importe erneut analysieren', 'Die Rezepttexte und Medien aller offenen Importe', 'Der bestätigte Lauf arbeitet im Hintergrund weiter.')) return;
      try {
        const r = await this.api('POST', '/api/pending/reanalyze-all', this.aiProcessingPayload());
        if (r && r.job_id) {
          this.showToast('Reanalyze-Job gestartet (#' + r.job_id + ')');
          this.refreshStatus();
        }
      } catch(e) {}
    },
    openEditItem(item) {
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
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
  };
})();
