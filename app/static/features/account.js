// Persönliche Anmeldung und Kontoeinladung im gemeinsamen Haushalt.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures.account = function () {
    return {
      canUseAdminTools() { return this.session.loaded && this.session.is_admin === true; },
      canImport() { return this.session.loaded && ['full_user', 'admin'].includes(this.session.role); },
      canWrite() { return this.session.loaded && ['user', 'full_user', 'admin'].includes(this.session.role); },
      canManageOwnAccount() { return this.canWrite() || (this.session.loaded && this.session.role === 'guest' && !!this.account.profile); },
      userRoleLabel(role) { return ({ guest: 'Gast', user: 'Benutzer', full_user: 'Vollbenutzer', admin: 'Admin' })[role] || 'Unbekannt'; },
      canEditRecipe() { return this.canWrite() && (this.session.is_admin || this.recipeDetail.data?.can_edit === true); },
      async loadAccount() {
        const generation = ++this.account._loadGeneration;
        this.account._loadController?.abort();
        const controller = new AbortController();
        this.account._loadController = controller;
        this.account.loading = true;
        this.account.error = '';
        try {
          const data = await this.api('GET', '/api/account', undefined, { signal: controller.signal });
          if (generation !== this.account._loadGeneration || controller.signal.aborted) return;
          if (generation === this.account._loadGeneration && !controller.signal.aborted && data) this.account.data = data;
          if (data && !data.is_guest) {
            const [imports, profile, sessions, identities] = await Promise.all([
              this.canImport() ? this.api('GET', '/api/account/imports', undefined, { signal: controller.signal }) : null,
              ...['/api/account/profile', '/api/account/sessions', '/api/account/identities']
                .map(path => this.api('GET', path, undefined, { signal: controller.signal })),
            ]);
            if (generation !== this.account._loadGeneration || controller.signal.aborted) return;
            this.account.imports = (imports?.items || []).map(item => ({ ...item, suggestion: {
              ...item.suggestion,
              ingredients: Array.isArray(item.suggestion?.ingredients) ? item.suggestion.ingredients.map(value => typeof value === 'string' ? { name: value, amount: null, unit: '' } : { ...value }) : [],
              steps: Array.isArray(item.suggestion?.steps) ? item.suggestion.steps.map(value => typeof value === 'string' ? { instruction: value } : { ...value }) : [],
            } }));
            this.account.profile = profile;
            this.account.sessions = sessions?.sessions || [];
            this.account.identities = identities?.identities || [];
            this.account.providers = (identities?.providers || []).filter(provider => provider.enabled && ['apple', 'google'].includes(provider.id));
          }
        } catch (error) {
          if (generation === this.account._loadGeneration && !controller.signal.aborted) this.account.error = error.message;
        } finally {
          if (generation === this.account._loadGeneration) this.account.loading = false;
        }
      },
      accountPasswordError(password) {
        const bytes = new TextEncoder().encode(password).length;
        return [...password].length < 10 || bytes > 72 ? 'Das neue Passwort braucht mindestens 10 Zeichen und darf höchstens 72 UTF-8-Bytes lang sein.' : '';
      },
      clearAccountPasswords() {
        this.account.currentPassword = this.account.newPassword = this.account.confirmPassword = this.account.deletePassword = '';
      },
      async accountAction(operation) {
        if (!this.canManageOwnAccount() || this.account.busy) return;
        this.account.busy = true;
        this.account.error = this.account.notice = '';
        try { await operation(); }
        catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; this.clearAccountPasswords(); }
      },
      async changeAccountPassword() {
        await this.accountAction(async () => {
          const problem = this.accountPasswordError(this.account.newPassword);
          if (problem) throw new Error(problem);
          if (this.account.newPassword !== this.account.confirmPassword) throw new Error('Die neuen Passwörter stimmen nicht überein.');
          const result = await this.api('POST', '/api/account/password', { current_password: this.account.currentPassword, new_password: this.account.newPassword });
          if (result?.ok) { this.clearAccountPasswords(); window.location.assign('/login?notice=password-changed'); }
        });
      },
      async deleteAccount() {
        if (!confirm('Dein Konto endgültig löschen? Diese Aktion lässt sich nicht rückgängig machen. Haushaltsdaten können die Löschung verhindern.')) return;
        await this.accountAction(async () => {
          const result = await this.api('DELETE', '/api/account/profile', { current_password: this.account.deletePassword });
          if (result?.ok) { this.clearAccountPasswords(); window.location.assign('/login'); }
        });
      },
      async revokeAccountSession(item) {
        if (!confirm(item.is_current ? 'Diese Sitzung beenden und abmelden?' : 'Dieses Gerät abmelden?')) return;
        await this.accountAction(async () => {
          const result = await this.api('DELETE', `/api/account/sessions/${encodeURIComponent(item.id)}`);
          if (!result?.ok) return;
          if (item.is_current) window.location.assign('/login');
          else { await this.loadAccount(); this.account.notice = 'Sitzung beendet.'; }
        });
      },
      async logoutAllAccountSessions() {
        if (!confirm('Auf allen Geräten abmelden, einschließlich dieser Sitzung?')) return;
        await this.accountAction(async () => {
          const result = await this.api('POST', '/api/auth/logout-all', {});
          if (result?.ok) window.location.assign('/login');
        });
      },
      async unlinkAccountProvider(provider) {
        if (!['apple', 'google'].includes(provider) || !confirm('Diese Anmeldung vom Konto trennen? Eine andere Anmeldemöglichkeit muss erhalten bleiben.')) return;
        await this.accountAction(async () => {
          const result = await this.api('DELETE', `/api/account/identities/${provider}`, { current_password: this.account.currentPassword });
          if (result?.ok) { await this.loadAccount(); this.account.notice = 'Verknüpfung entfernt.'; }
        });
      },
      availableAccountProviders() {
        return this.account.providers.filter(provider => !this.account.identities.some(identity => identity.provider === provider.id));
      },
      async loadUsers() {
        if (!this.session.is_admin) { this.users.items = []; this.users.draft = null; return; }
        const generation = ++this.users._loadGeneration;
        this.users.loading = true;
        this.users.error = '';
        try {
          const result = await this.api('GET', '/api/users');
          if (generation === this.users._loadGeneration && this.session.is_admin) this.users.items = result?.users || [];
        } catch (error) { if (generation === this.users._loadGeneration) this.users.error = error.message; }
        finally { if (generation === this.users._loadGeneration) this.users.loading = false; }
      },
      userAuthMethods(item) {
        const labels = { password: 'Passwort', apple: 'Apple', google: 'Google' };
        return (Array.isArray(item.auth_methods) ? item.auth_methods : [])
          .filter((method, index, methods) => Object.hasOwn(labels, method) && methods.indexOf(method) === index)
          .map(method => labels[method]);
      },
      filteredUsers() {
        const search = this.users.search.trim().toLowerCase();
        return this.users.items.filter(item => [item.username, this.userRoleLabel(item.role), ...this.userAuthMethods(item)]
          .some(value => value.toLowerCase().includes(search)));
      },
      resetUserSearch() { this.users.search = ''; },
      editUser(item = null) {
        if (!this.session.is_admin || this.users.busy) return;
        this.users.error = this.users.notice = '';
        this.users.draft = { id: item?.id || null, username: item?.username || '', role: item?.role || 'user', disabled: !!item?.disabled, password: '' };
      },
      async userAdminAction(operation) {
        if (!this.session.is_admin || this.users.busy) return;
        this.users.busy = true;
        this.users.error = this.users.notice = '';
        try { await operation(); }
        catch (error) { this.users.error = error.message; }
        finally { this.users.busy = false; if (this.users.draft) this.users.draft.password = ''; }
      },
      async saveUser() {
        const draft = this.users.draft;
        if (!draft) return;
        await this.userAdminAction(async () => {
          if (!draft.id && !/^[a-zA-Z0-9_.-]{3,32}$/.test(draft.username.trim())) throw new Error('Benutzername: 3–32 Buchstaben, Ziffern, Punkt, Bindestrich oder Unterstrich.');
          if (!draft.id || draft.password) { const problem = this.accountPasswordError(draft.password); if (problem) throw new Error(problem); }
          if (draft.id && draft.username === this.session.username && (draft.password || draft.role !== 'admin') && !confirm('Diese Änderung beendet auch deine eigene Sitzung. Fortfahren?')) return;
          const body = draft.id ? { role: draft.role, disabled: draft.disabled } : { username: draft.username.trim(), role: draft.role };
          if (draft.password) body.password = draft.password;
          const result = await this.api(draft.id ? 'PATCH' : 'POST', draft.id ? `/api/users/${draft.id}` : '/api/users', body);
          if (!result?.ok) return;
          this.users.draft = null;
          if (draft.id && draft.username === this.session.username && (draft.password || draft.role !== 'admin')) window.location.assign('/login');
          else { await this.loadUsers(); this.users.notice = draft.id ? 'Benutzer gespeichert.' : 'Benutzer erstellt.'; }
        });
      },
      async deleteUser(item) {
        if (item.username === this.session.username || !confirm(`Benutzer „${item.username}“ endgültig löschen? Zugehörige Haushaltsdaten können die Löschung verhindern.`)) return;
        await this.userAdminAction(async () => {
          const result = await this.api('DELETE', `/api/users/${item.id}`);
          if (result?.ok) { this.users.draft = null; await this.loadUsers(); this.users.notice = 'Benutzer gelöscht.'; }
        });
      },
      async revokeUserSessions(item) {
        if (!confirm(`Alle Sitzungen von „${item.username}“ beenden?`)) return;
        await this.userAdminAction(async () => {
          const result = await this.api('POST', `/api/users/${item.id}/revoke-sessions`, {});
          if (!result?.ok) return;
          if (item.username === this.session.username) window.location.assign('/login');
          else this.users.notice = 'Alle Sitzungen dieses Benutzers beendet.';
        });
      },
      async createAccountInvitation() {
        if (!this.canWrite() || this.account.busy) return;
        this.account.busy = true;
        this.account.error = '';
        try {
          this.account.invitation = await this.api('POST', '/api/account/invitations', {});
          await this.loadAccount();
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
      accountInvitationUrl() {
        return this.account.invitation ? window.location.origin + this.account.invitation.invite_path : '';
      },
      async copyAccountInvitation() {
        try {
          await navigator.clipboard.writeText(this.accountInvitationUrl());
          this.showToast('Einladungslink kopiert');
        } catch (_) { this.showToast('Link bitte aus dem Feld kopieren', 'err'); }
      },
      async revokeAccountInvitation(item) {
        if (!this.canWrite() || this.account.busy) return;
        this.account.busy = true;
        try {
          await this.api('DELETE', `/api/account/invitations/${item.id}`);
          if (this.account.invitation?.id === item.id) this.account.invitation = null;
          await this.loadAccount();
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
      async acceptAccountInvitation() {
        if (!this.canWrite() || this.account.busy || !this.account.joinToken.trim()) return;
        if (!confirm('Diesem Haushalt beitreten? Deine privaten Rezepte, Einkaufsliste und Kochhistorie werden übernommen. Das lässt sich hier nicht rückgängig machen.')) return;
        this.account.busy = true;
        this.account.error = '';
        try {
          let token = this.account.joinToken.trim();
          if (token.includes('://')) token = new URL(token).searchParams.get('invite') || '';
          await this.api('POST', '/api/account/invitations/accept', { token });
          this.account.joinToken = '';
          this.account.invitation = null;
          await this.loadAccount();
          this.showToast('Einladung angenommen');
          window.location.assign?.('/account');
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
      async saveHouseholdImport(item) {
        if (!this.canImport() || this.householdImportBusy(item) || !item.name?.trim()) return;
        this.account.busy = true;
        try {
          const suggestion = item.suggestion || {};
          const result = await this.api('POST', '/api/pending', { url: item.url, visibility: 'private', action: 'save', name: item.name.trim(),
            type: suggestion.type || 'Sonstiges', category: suggestion.category || 'Allgemein',
            ingredients: (suggestion.ingredients || []).filter(row => row.name?.trim()).map(row => ({ ...row, name: row.name.trim(), amount: row.amount === '' ? null : row.amount })),
            steps: (suggestion.steps || []).filter(row => row.instruction?.trim()).map((row, index) => ({ ...row, instruction: row.instruction.trim(), step_number: index + 1 })),
            servings: suggestion.servings || null });
          if (!result?.ok) throw new Error(result?.error || 'Rezept konnte nicht übernommen werden');
          await this.loadAccount();
          this.showToast('Privates Rezept übernommen');
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
      householdImportBusy(item) {
        return this.account.busy || !!this.reanalyzing[item.url] || ['queued', 'running'].includes(item.suggestion?.analysis_state);
      },
      async changeHouseholdImport(item, action) {
        if (!this.canImport() || this.householdImportBusy(item) || !['skip', 'reanalyze'].includes(action)) return;
        if (action === 'skip' && !confirm('Diesen Importvorschlag verwerfen?')) return;
        this.account.busy = true;
        this.account.error = '';
        try {
          const path = action === 'reanalyze' ? '/api/pending/reanalyze' : '/api/pending';
          const result = await this.api('POST', path, { url: item.url, visibility: 'private', ...(action === 'skip' ? { action } : {}) });
          if (!result?.ok) throw new Error(result?.error || 'Import konnte nicht aktualisiert werden.');
          await this.loadAccount();
          this.showToast(action === 'skip' ? 'Import verworfen.' : 'Import erneut analysiert.');
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
    };
  };
})();
