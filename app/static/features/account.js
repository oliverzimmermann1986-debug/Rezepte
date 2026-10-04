// Persönliche Anmeldung und Kontoeinladung im gemeinsamen Haushalt.
(() => {
  'use strict';
  window.RezepteFeatures = window.RezepteFeatures || {};
  window.RezepteFeatures.account = function () {
    return {
      canWrite() { return this.session.loaded && ['user', 'admin'].includes(this.session.role); },
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
            const imports = await this.api('GET', '/api/account/imports', undefined, { signal: controller.signal });
            if (generation === this.account._loadGeneration && !controller.signal.aborted) this.account.imports = imports?.items || [];
          }
        } catch (error) {
          if (generation === this.account._loadGeneration && !controller.signal.aborted) this.account.error = error.message;
        } finally {
          if (generation === this.account._loadGeneration) this.account.loading = false;
        }
      },
      async createAccountInvitation() {
        if (this.account.busy) return;
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
        if (this.account.busy) return;
        this.account.busy = true;
        try {
          await this.api('DELETE', `/api/account/invitations/${item.id}`);
          if (this.account.invitation?.id === item.id) this.account.invitation = null;
          await this.loadAccount();
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
      async acceptAccountInvitation() {
        if (this.account.busy || !this.account.joinToken.trim()) return;
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
        if (!this.canWrite() || this.account.busy || !item.name?.trim()) return;
        this.account.busy = true;
        try {
          const suggestion = item.suggestion || {};
          const result = await this.api('POST', '/api/pending', { url: item.url, visibility: 'private', action: 'save', name: item.name.trim(),
            type: suggestion.type || 'Sonstiges', category: suggestion.category || 'Allgemein',
            ingredients: suggestion.ingredients || [], steps: suggestion.steps || [], servings: suggestion.servings || null });
          if (!result?.ok) throw new Error(result?.error || 'Rezept konnte nicht übernommen werden');
          await this.loadAccount();
          this.showToast('Privates Rezept übernommen');
        } catch (error) { this.account.error = error.message; }
        finally { this.account.busy = false; }
      },
    };
  };
})();
