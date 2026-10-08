// audit: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["audit"] = function () {
    return {

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

    async setShoppingExclusion(can, excluded) {
      const previous = Boolean(can.shopping_excluded);
      can.shopping_excluded = excluded ? 1 : 0;
      const canonical = encodeURIComponent(can.canonical_name);
      const r = await this.api(
        'PUT',
        `/api/master/canonicals/${canonical}/shopping-exclusion`,
        { excluded: Boolean(excluded) }
      );
      if (!r?.ok) {
        can.shopping_excluded = previous ? 1 : 0;
        return;
      }
      this.showToast(
        excluded
          ? `${can.canonical_name} wird nicht eingekauft`
          : `${can.canonical_name} wird wieder eingekauft`
      );
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
      if (!this.canUseAdminTools()) return;
      const r = await this.api('POST', `/api/pending/failed/${encodeURIComponent(url)}/retry`);
      if (r?.ok) {
        this.showToast('Zähler zurückgesetzt — nächster Lauf versucht es neu');
        this.audit.data.failed_downloads = this.audit.data.failed_downloads.filter(f => f.url !== url);
        if (this.audit.summary) this.audit.summary.failed_download_count = Math.max(0, (this.audit.summary.failed_download_count || 1) - 1);
      }
    },
    async discardFailedDownload(url) {
      if (this.discardingUrl || !confirm(
        'URL dauerhaft verwerfen?\n\nSie wird aus den Fehlversuchen entfernt und künftig nicht erneut importiert.'
      )) return;
      this.discardingUrl = url;
      try {
        const r = await this.api(
          'POST',
          `/api/pending/failed/${encodeURIComponent(url)}/discard`
        );
        if (r?.ok) {
          this.showToast('URL dauerhaft verworfen');
          this.failedDownloads = this.failedDownloads.filter(f => f.url !== url);
          if (Array.isArray(this.audit.data?.failed_downloads)) {
            this.audit.data.failed_downloads =
              this.audit.data.failed_downloads.filter(f => f.url !== url);
          }
          if (this.audit.summary) {
            this.audit.summary.failed_download_count = Math.max(
              0,
              (this.audit.summary.failed_download_count || 1) - 1
            );
          }
        }
      } finally {
        this.discardingUrl = null;
      }
    },

    // KI-Sanity startet Background-Job, dann pollen wir den Status alle 2s
    async startAiSanity() {
      if (!this.canUseAdminTools()) return;
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

    // Bulk: aktive leere Rezepte mit gespeichertem Text auf pending setzen.
    // Worker pickt ok/error/skipped auf und versucht den KI-Extract erneut.
    async recoverEmpty() {
      if (!this.canUseAdminTools()) return;
      const n = this.audit.summary?.empty_recipe_count || 0;
      if (!confirm(`${n} Rezepte auf 'pending' zurücksetzen?\n\nDer Worker extrahiert sie dann neu mit dem aktuellen Prompt. Bestehende Zutaten/Schritte würden überschrieben (sind ja eh leer).`)) return;
      const r = await this.api('POST', '/api/recipes/recover-empty');
      if (r && r.ok) {
        this.showToast(`✓ ${r.reset_count} Rezepte auf pending — Worker läuft`);
        await this.loadAudit();
      }
    },

    // Alle aktiven, nicht verifizierten Rezepte ohne Zutaten und mit URL
    // sequenziell durch denselben Quellenabruf wie im Rezept-Modal schicken.
    // reanalyze=true plant die Extraktion auch bei unveränderter Caption neu ein.
    async rescrapeBulkRecipeIds(ids, label) {
      if (!this.canUseAdminTools()) return;
      if (this.audit.rescrapingBulk) {
        this.audit.rescrapingBulk = false;
        return;
      }
      if (ids.length === 0) {
        this.showToast(`Keine Rezepte ${label} mit abrufbarer URL gefunden`);
        return;
      }
      const etaSec = ids.length * 15;
      const eta = etaSec > 60 ? `~${Math.ceil(etaSec / 60)} Min` : `~${etaSec}s`;
      if (!confirm(
        `${ids.length} Rezepte ${label} erneut von ihrer URL abrufen?\n\n` +
        `TikTok-Captions werden aufgeklappt und anschließend neu analysiert. ` +
        `Der Lauf ist sequenziell und dauert ungefähr ${eta}.\n\n` +
        `Zum Abbrechen den Fortschritts-Button erneut anklicken.`
      )) return;

      this.audit.rescrapingBulk = true;
      this.audit.rescrapeProgress = 0;
      this.audit.rescrapeTotal = ids.length;
      let queued = 0, browserCaptions = 0, failed = 0;
      try {
        for (const id of ids) {
          if (!this.audit.rescrapingBulk) break;
          this.audit.rescrapeProgress++;
          try {
            const response = await this.api(
              'POST', `/api/recipes/${id}/rescrape?reanalyze=true`
            );
            if (response?.ok && response.ingredients_queued) {
              queued++;
              if (response.description_source === 'tiktok-browser') browserCaptions++;
            } else {
              failed++;
            }
          } catch (e) {
            failed++;
          }
        }
        this.showToast(
          `✓ ${queued} neu eingeplant · ${browserCaptions} lange TikTok-Captions · ${failed} übersprungen/fehlgeschlagen`
        );
        await this.loadAudit();
      } finally {
        this.audit.rescrapingBulk = false;
        this.audit.rescrapeProgress = 0;
        this.audit.rescrapeTotal = 0;
      }
    },

    // Bulk: Nährwerte für bis zu 50 Rezepte berechnen. Synchroner Lauf —
    // UI ist blockiert für ~30s, danach Audit neu laden. Bei mehr als 50
    // pending Rezepten muss User wiederholt klicken (siehe Audit-Liste).
    async rescrapeBulkMissingIngredients() {
      if (!this.canUseAdminTools()) return;
      const ids = this.audit.data?.empty_rescrape_ids || [];
      return this.rescrapeBulkRecipeIds(ids, 'ohne Zutaten');
    },

    async rescrapeBulkMissingSteps() {
      if (!this.canUseAdminTools()) return;
      const ids = (this.audit.data?.data_gaps?.no_steps || [])
        .filter(r => String(r.url || '').trim())
        .map(r => r.id);
      return this.rescrapeBulkRecipeIds(ids, 'ohne Schritte');
    },

    async bulkComputeNutrition() {
      if (!this.canUseAdminTools()) return;
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
        this.showToast(r.files_restored ? 'Rezept und Dateien wiederhergestellt' : 'Rezept wiederhergestellt');
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
      if (!this.canUseAdminTools()) return;
      this.audit.rescrapingId = recipeId;
      try {
        const r = await this.api('POST', `/api/recipes/${recipeId}/rescrape`);
        if (r && r.ok) {
          if (r.any_change) {
            const parts = [];
            if (r.description_updated) parts.push('Beschreibung');
            if (r.thumbnail_updated) parts.push('Bild');
            this.showToast(
              `✓ ${parts.join(' + ')} aktualisiert${r.ingredients_queued ? ' · Zutatenanalyse gestartet' : ''}`
            );
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
      if (!this.canUseAdminTools()) return;
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

    // Bulk Frame-Extract — sequenziell für ALLE Rezepte ohne Bild.
    // Lokal, ~1s pro Rezept → bei 100 Rezepten ca. 2 Min. Cancel-Knopf
    // (state-flip extractingBulk) bricht laufende Schleife sauber ab.
    async bulkExtractFrames() {
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
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
      if (!this.canUseAdminTools()) return;
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
      this.fsCompare.controller?.abort();
      const controller = new AbortController();
      const epoch = ++this.fsCompare.requestEpoch;
      this.fsCompare.controller = controller;
      this.fsCompare.show = true;
      this.fsCompare.syncError = {...syncError};
      this.fsCompare.dbRecipe = null;
      this.fsCompare.fsPreview = null;
      this.fsCompare.actionBusy = false;
      // Loading-Flags damit das UI 'Wird geladen' vs 'Keine Daten' unterscheiden kann
      this.fsCompare.loadingDb = !!syncError.conflict_with_id;
      this.fsCompare.loadingFs = true;
      try {
        const [db, fs] = await Promise.all([
          syncError.conflict_with_id
            ? this.api(
                'GET',
                '/api/recipes/' + syncError.conflict_with_id,
                undefined,
                {signal: controller.signal},
              )
            : Promise.resolve(null),
          this.api(
            'GET',
            '/api/audit/folder-preview?path=' + encodeURIComponent(syncError.folder_path),
            undefined,
            {signal: controller.signal},
          ),
        ]);
        if (
          epoch !== this.fsCompare.requestEpoch
          || !this.fsCompare.show
          || this.fsCompare.syncError?.id !== syncError.id
        ) return;
        this.fsCompare.dbRecipe = db;
        this.fsCompare.fsPreview = fs;
      } catch (e) {
        if (epoch === this.fsCompare.requestEpoch) {
          this.showToast('Fehler beim Laden: ' + e.message, 'err');
        }
      } finally {
        if (epoch === this.fsCompare.requestEpoch) {
          this.fsCompare.loadingDb = false;
          this.fsCompare.loadingFs = false;
        }
      }
    },

    closeFsCompare() {
      this.fsCompare.controller?.abort();
      this.fsCompare.controller = null;
      this.fsCompare.requestEpoch += 1;
      this.fsCompare.show = false;
      this.fsCompare.syncError = null;
      this.fsCompare.dbRecipe = null;
      this.fsCompare.fsPreview = null;
      this.fsCompare.loadingDb = false;
      this.fsCompare.loadingFs = false;
      this.fsCompare.actionBusy = false;
    },

    // Compare-Aktion: das in-DB Rezept (+ sein Folder) löschen, dann
    // automatisch nächsten Sync triggern damit der bisherige FS-Konflikt-
    // Folder neu indexiert wird (= übernimmt die URL).
    async deleteFsCompareDb() {
      const r = this.fsCompare.dbRecipe;
      const epoch = this.fsCompare.requestEpoch;
      if (!r || this.fsCompare.loadingDb || this.fsCompare.loadingFs || this.fsCompare.actionBusy) return;
      if (!confirm(`DB-Rezept #${r.id} „${r.name}" löschen?\n\nDer Folder ${r.folder_path} wird auch entfernt.\nDanach kann der Konflikt-Folder beim nächsten Sync rein.`)) return;
      if (epoch !== this.fsCompare.requestEpoch || this.fsCompare.dbRecipe?.id !== r.id) return;
      this.fsCompare.actionBusy = true;
      try {
        const resp = await this.api('DELETE', '/api/recipes/' + r.id);
        if (
          resp?.ok
          && epoch === this.fsCompare.requestEpoch
          && this.fsCompare.dbRecipe?.id === r.id
        ) {
          this.showToast('✓ DB-Rezept gelöscht');
          this.closeFsCompare();
          await this.loadAudit();
        }
      } finally {
        if (epoch === this.fsCompare.requestEpoch) this.fsCompare.actionBusy = false;
      }
    },

    // FS-Konflikt-Folder löschen (gleiches wie der Inline-Button, aber direkt
    // aus dem Compare-Modal heraus).
    async deleteFsCompareFs() {
      const path = this.fsCompare.fsPreview?.folder_path;
      const epoch = this.fsCompare.requestEpoch;
      if (!path || this.fsCompare.loadingDb || this.fsCompare.loadingFs || this.fsCompare.actionBusy) return;
      if (!confirm(`FS-Folder löschen?\n\n${path}\n\nDie Dateien werden dauerhaft entfernt.`)) return;
      if (epoch !== this.fsCompare.requestEpoch || this.fsCompare.fsPreview?.folder_path !== path) return;
      this.fsCompare.actionBusy = true;
      try {
        const r = await this.api('POST', '/api/audit/recipe/delete-by-path', { folder_path: path });
        if (
          r?.ok
          && epoch === this.fsCompare.requestEpoch
          && this.fsCompare.fsPreview?.folder_path === path
        ) {
          this.showToast('✓ FS-Folder gelöscht');
          this.closeFsCompare();
          await this.loadAudit();
        }
      } finally {
        if (epoch === this.fsCompare.requestEpoch) this.fsCompare.actionBusy = false;
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
        const { response: r, data } = await this.fetchWithTimeout(`/api/recipes/${id}/rename`, {
          method: 'PUT', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ new_name: newName, rename_folder: true }),
        }, 30000, async response => ({ response, data: await response.json() }));
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
        const { response: r, data } = await this.fetchWithTimeout(`/api/recipes/${recipe.id}?delete_files=true`, {
          method: 'DELETE', credentials: 'include',
        }, 30000, async response => ({ response, data: await response.json() }));
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
        const { response: r, data } = await this.fetchWithTimeout('/api/recipes/merge', {
          method: 'POST', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source_id: sourceId, target_id: targetId, delete_source: true,
          }),
        }, 60000, async response => ({ response, data: await response.json() }));
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
  };
})();
