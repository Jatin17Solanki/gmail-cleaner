/**
 * Gmail Cleaner - Label Management Module
 *
 * Phase 3: Label/Important became per-row inline actions on every sender
 * row (PRD Section 5), owned by senderList.js's _openLabelPicker/
 * _toggleImportant - the old bulk multi-select "Organize" dropdown/overlay
 * UI that lived here is retired. This module keeps the label CRUD/cache
 * that senderList.js and label-management UI (create/delete labels) rely
 * on.
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Labels = {
    labels: {
        system: [],
        user: []
    },

    async loadLabels() {
        try {
            const response = await fetch('/api/labels');
            const data = await response.json();

            if (data.success) {
                this.labels.system = data.system_labels || [];
                this.labels.user = data.user_labels || [];
                return { user_labels: this.labels.user, system_labels: this.labels.system };
            }
            console.error('Failed to load labels:', data.error);
            return null;
        } catch (error) {
            console.error('Error loading labels:', error);
            return null;
        }
    },

    async createLabel(name) {
        try {
            const response = await fetch('/api/labels', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            const result = await response.json();

            if (result.success) {
                this.labels.user.push(result.label);
                this.labels.user.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
            }

            return result;
        } catch (error) {
            return { success: false, error: error.message };
        }
    },

    async deleteLabel(labelId) {
        try {
            const response = await fetch(`/api/labels/${encodeURIComponent(labelId)}`, {
                method: 'DELETE'
            });
            const result = await response.json();

            if (result.success) {
                this.labels.user = this.labels.user.filter(l => l.id !== labelId);
            }

            return result;
        } catch (error) {
            return { success: false, error: error.message };
        }
    }
};

// Load labels once signed in, so senderList.js's per-row label picker and
// each view's filter drawer "Label" dropdown have data ready.
document.addEventListener('DOMContentLoaded', async () => {
    setTimeout(async () => {
        const authResponse = await fetch('/api/auth-status');
        const authStatus = await authResponse.json();
        if (authStatus.logged_in) {
            await GmailCleaner.Labels.loadLabels();
        }
    }, 500);
});
