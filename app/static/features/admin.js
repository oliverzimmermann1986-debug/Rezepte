// admin: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["admin"] = function () {
    return {

    async selectAdminTab(tab, { updateUrl = true } = {}) {
      if (!this.session.is_admin) {
        this.admin.accessDenied = true;
        this.navTo('recipes', { updateUrl: false });
        return;
      }
      const allowed = new Set(['home','import','quality','versions','pdf','search','maintenance','master','settings','trash','users']);
      this.admin.tab = allowed.has(tab) ? tab : 'home';
      if (this.admin.tab !== 'users') this.users.draft = null;
      this.page = 'admin';
      if (updateUrl && window.history?.replaceState) {
        const target = this.admin.tab === 'home'
          ? '/admin'
          : (this.admin.tab === 'pdf' ? '/admin/pdf' : `/admin?section=${encodeURIComponent(this.admin.tab)}`);
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
        case 'settings': this.loadConfig(); break;
        case 'trash': this.loadTrash(); break;
        case 'users': this.loadUsers(); break;
      }
    },

    adminTabLabel() {
      return ({
        home: 'Übersicht',
        import: 'Importzentrale',
        quality: 'Qualität',
        versions: 'Versionen',
        pdf: 'PDF & Scan',
        search: 'Suche',
        maintenance: 'Wartung',
        master: 'Stammdaten',
        settings: 'Einstellungen',
        trash: 'Papierkorb',
        users: 'Benutzerverwaltung',
      })[this.admin.tab] || 'Administration';
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
      const context = this._detailContext();
      const r = await this.api('POST', `/api/admin/versions/${item.id}/restore`, {});
      if (r?.ok) {
        if (r.media_restored) this.invalidateRecipeThumbnail(item.recipe_id);
        this.showToast('Version wiederhergestellt');
        await this.loadAdminVersions();
        if (context.id === item.recipe_id && this._detailOwns(context)) await this.openRecipe(item.recipe_id);
        await this.loadRecipes();
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
      if (!this.canUseAdminTools()) return;
      const id = Number(this.admin.pdf.recipe_id || 0);
      if (!id) return this.showToast('Bitte zuerst eine Rezept-ID eingeben', 'err');
      const epoch = ++this.admin.pageEditor.requestEpoch;
      this.admin.pageEditor.loadedRecipeId = null;
      this.admin.pageEditor.filename = '';
      this.admin.pageEditor.pages = [];
      this.admin.pageEditor.loading = true;
      try {
        const r = await this.api('GET', `/api/admin/pdf/${id}/pages`);
        if (
          epoch !== this.admin.pageEditor.requestEpoch
          || Number(this.admin.pdf.recipe_id || 0) !== id
          || !r
        ) return;
        this.admin.pageEditor.loadedRecipeId = id;
        this.admin.pageEditor.filename = r.filename || '';
        this.admin.pageEditor.pages = (r.pages || []).map(page => ({ ...page, rotation_delta: 0, deleted: false }));
        this.admin.pageEditor.previewKey = Date.now();
      } catch (_) { this.showToast('PDF-Seiten konnten nicht geladen werden', 'err'); }
      finally {
        if (epoch === this.admin.pageEditor.requestEpoch) {
          this.admin.pageEditor.loading = false;
        }
      }
    },

    invalidatePdfPageEditor() {
      const currentId = Number(this.admin.pdf.recipe_id || 0);
      if (
        this.admin.pageEditor.loadedRecipeId !== null
        && currentId !== Number(this.admin.pageEditor.loadedRecipeId)
      ) {
        this.admin.pageEditor.requestEpoch += 1;
        this.admin.pageEditor.loadedRecipeId = null;
        this.admin.pageEditor.filename = '';
        this.admin.pageEditor.pages = [];
        this.admin.pageEditor.loading = false;
      }
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
      if (!this.canUseAdminTools()) return;
      const id = Number(this.admin.pageEditor.loadedRecipeId || 0);
      const inputId = Number(this.admin.pdf.recipe_id || 0);
      const active = this.admin.pageEditor.pages.filter(page => !page.deleted);
      if (!id || id !== inputId) {
        return this.showToast('Rezept-ID wurde geändert – Seiten bitte neu laden', 'err');
      }
      if (!active.length) return this.showToast('Mindestens eine Seite muss erhalten bleiben', 'err');
      if (!confirm(`${active.length} PDF-Seite(n) in der angezeigten Reihenfolge speichern? Das Original wird vorher gesichert.`)) return;
      const epoch = this.admin.pageEditor.requestEpoch;
      this.admin.pageEditor.saving = true;
      try {
        const rotations = {};
        active.forEach(page => { if (page.rotation_delta) rotations[String(page.page)] = page.rotation_delta; });
        const r = await this.api('POST', `/api/admin/pdf/${id}/pages/apply`, {
          order: active.map(page => page.page), rotations, keep_original: true,
        });
        if (
          r?.ok
          && epoch === this.admin.pageEditor.requestEpoch
          && Number(this.admin.pageEditor.loadedRecipeId) === id
        ) {
          this.showToast('PDF-Seiten gespeichert');
          this.admin.pageEditor.saving = false;
          await this.loadPdfPages();
          await this.loadAdminOverview();
          if (typeof this.loadRecipes === 'function') await this.loadRecipes();
        }
      } catch (_) { this.showToast('PDF-Seiten konnten nicht gespeichert werden', 'err'); }
      finally { this.admin.pageEditor.saving = false; }
    },

    async loadPdfPreflight() {
      if (!this.canUseAdminTools()) return;
      this.admin.pdf.legacyMode = false;
      try {
        this.admin.pdf.preflight = await this.api('GET', '/api/admin/pdf/preflight', undefined, { silent: true });
        if (!this.admin.pdf.running) {
          const active = await this.api('GET', '/api/admin/pdf/jobs/active', undefined, { silent: true });
          if (active?.active && active?.job?.id) {
            this.admin.pdf.running = true;
            this.admin.pdf.jobId = Number(active.job.id);
            this.admin.pdf.result = { ...(active.job.result || {}), run_id: active.job.id };
            this.pollAdminPdfJob(this.admin.pdf.jobId);
          }
        }
      } catch (e) {
        if (e?.status === 404) {
          // Kompatibilität bei Mischständen: StaticFiles liest bereits das neue
          // app.js von Disk, der laufende Uvicorn-Prozess hat aber noch die alten
          // Router importiert. Der alte synchrone PDF-Endpunkt bleibt nutzbar.
          this.admin.pdf.legacyMode = true;
          this.systemInfo.backendOutdated = true;
          this.admin.pdf.preflight = {
            ok: true, legacy: true, issues: [],
            warnings: [{ code: 'backend_restart_required', message: 'Backend ist noch nicht neu gestartet. PDF läuft vorübergehend im alten Synchronmodus; bitte scrapper-web neu starten.' }],
          };
          return;
        }
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
      if (!this.canUseAdminTools()) return;
      if (this.admin.pdf.running) return;
      const usesAI = !!this.admin.pdf.extract_recipe_data;
      if (usesAI && !this.confirmAIProcessing('Rezeptdaten aus PDFs ermitteln', 'Die aus den ausgewählten PDFs gelesenen Texte', 'Auch ein Probelauf überträgt die Inhalte zur Analyse. Die PDF-Aufbereitung ohne Rezeptdaten-Ermittlung benötigt diese Erlaubnis nicht.')) return;
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
          background: !this.admin.pdf.legacyMode,
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
          extract_recipe_data: !!p.extract_recipe_data,
          overwrite_recipe_data: !!p.overwrite_recipe_data,
          ...(usesAI ? this.aiProcessingPayload() : {}),
        };
        let accepted;
        try {
          accepted = await this.api('POST', '/api/admin/pdf/process', payload, { silent: true });
        } catch (e) {
          if (e?.status === 404) {
            throw new Error('PDF-Backend fehlt. Bitte den Dienst scrapper-web neu starten oder das lokale Update vollständig einspielen.');
          }
          throw e;
        }
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
      if (!this.canUseAdminTools()) return;
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

    async startRecipeImageBackfill() {
      if (!this.canUseAdminTools()) return;
      if (!this.confirmAIProcessing('Bilder für alle Rezepte erzeugen', 'Rezepttitel, Zutaten und Zubereitungshinweise aller betroffenen Rezepte',
        'Für alle Rezepte neue Bilder generieren?\n\n' +
        'Vor der ersten Generierung werden ausnahmslos alle vorhandenen Bilder ' +
        'checksummiert gesichert. Der Lauf nutzt die kostenpflichtige OpenAI Image API.'
      )) return;
      this.admin.maintenanceBusy = 'recipe_images';
      try {
        const result = await this.api('POST', '/api/recipes/images/backfill', this.aiProcessingPayload());
        this.admin.imageBackfill = { status: 'running', result };
        this.showToast('Bildsicherung und anschließende Generierung gestartet');
        this.pollRecipeImageBackfill(result.run_id);
      } catch (_) {
        this.showToast('Bildlauf konnte nicht gestartet werden', 'err');
        this.admin.maintenanceBusy = '';
      }
    },

    async pollRecipeImageBackfill(runId) {
      try {
        const run = await this.api('GET', `/api/recipes/images/backfill/${runId}`, undefined, { silent: true });
        this.admin.imageBackfill = run;
        if (run.status === 'running') {
          window.setTimeout(() => this.pollRecipeImageBackfill(runId), 3000);
          return;
        }
        this.admin.maintenanceBusy = '';
        this.showToast(run.status === 'ok' ? 'Rezeptbilder fertig' : 'Bildlauf mit Fehlern beendet', run.status === 'ok' ? 'ok' : 'err');
        await this.loadMaintenanceRuns();
      } catch (_) {
        this.admin.maintenanceBusy = '';
      }
    },

    async loadMaintenanceRuns() {
      try {
        const r = await this.api('GET', '/api/admin/maintenance/runs');
        this.admin.maintenanceRuns = r.items || [];
      } catch (_) { this.admin.maintenanceRuns = []; }
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
    };
  };
})();
