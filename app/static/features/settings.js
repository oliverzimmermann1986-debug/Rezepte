// settings: fachliche Methoden der Rezepte-Oberfläche.
// Zustand und gemeinsame Infrastruktur bleiben in app.js.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures["settings"] = function () {
    return {

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

    // ------------- Config -------------
    // ════════════════════════════════════════════════════════════════════
    // ════════════════════════════════════════════════════════════════════
    async loadConfig() {
      const cfg = await this.api('GET', '/api/config');
      // Defaults absichern damit Alpine-Bindings nicht meckern
      cfg.web ||= {};
      cfg.paths ||= {};
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
      cfg.einkauf ||= {};
      cfg.einkauf.api_url ||= '';
      cfg.einkauf.app_token ||= '';
      cfg.einkauf.cf_access_client_id ||= '';
      cfg.einkauf.cf_access_client_secret ||= '';
      if (cfg.einkauf.auto_consolidate === undefined) cfg.einkauf.auto_consolidate = true;
      this.config = cfg;
      // Pro-Pair-Args ins UI laden
      this.recipeTypes = cfg.recipe_types || this.recipeTypes;
      this.weddingCategories = cfg.wedding_categories || this.weddingCategories;
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
    async runBackupNow() {
      if (this.maintBusy) return;
      this.maintBusy = true;
      this.maintenanceOutput = '';
      try {
        const result = await this.api('POST', '/api/config/backups/run-now');
        this.maintenanceOutput = (result.stdout || '')
          + (result.stderr ? '\n[STDERR]\n' + result.stderr : '')
          + (result.error ? '\n' + result.error : '');
        this.showToast(result.ok ? 'Backup erstellt' : 'Backup fehlgeschlagen', result.ok ? 'ok' : 'error');
        await this.loadMaintenance();
      } catch (error) {
        this.showToast('Backup fehlgeschlagen: ' + error.message, 'error');
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
      if (!this.canUseAdminTools()) return;
      this.testing[key] = true;
      this.testResults[key] = null;
      try {
        const data = await this.fetchWithTimeout(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: body ? JSON.stringify(body) : null,
        }, 45000, response => response.json());
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
    async testOpenAI() {
      if (!this.canUseAdminTools()) return;
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
        events: ['job_failed', 'pending_high'],
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

    };
  };
})();
