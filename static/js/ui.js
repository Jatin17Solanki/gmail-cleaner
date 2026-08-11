/**
 * Gmail Cleanup - UI Utilities Module
 */

window.GmailCleaner = window.GmailCleaner || {};

// Phase 3 IA (PRD Section 5): Delete (default), Mark as read, Archive,
// Routines (placeholder - Phase 4b), Restore. Each sender-list view owns
// its own filter drawer now, so there's no single shared filter bar to
// toggle per view anymore (see senderList.js).
const VIEW_TITLES = {
    delete: 'Delete',
    markread: 'Mark as read',
    archive: 'Archive',
    routines: 'Routines',
    restore: 'Restore'
};

GmailCleaner.UI = {
    setupNavigation() {
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                if (item.classList.contains('nav-item-disabled')) return;
                this.showView(item.dataset.view);
            });
        });
    },

    showView(viewName) {
        GmailCleaner.currentView = viewName;

        document.querySelectorAll('.view').forEach(view => {
            view.classList.add('hidden');
        });

        const view = document.getElementById(viewName + 'View');
        if (view) {
            view.classList.remove('hidden');
        }

        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.view === viewName);
        });

        const titleEl = document.getElementById('accountBarTitle');
        if (titleEl && VIEW_TITLES[viewName]) {
            titleEl.textContent = VIEW_TITLES[viewName];
        }

        if (viewName === 'restore') {
            GmailCleaner.Restore.loadEntries();
        }
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    },

    formatSize(bytes) {
        if (!bytes || bytes === 0) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return size.toFixed(unitIndex > 0 ? 1 : 0) + ' ' + units[unitIndex];
    },

    showToast(message, type = 'success', duration = 5000) {
        const container = document.getElementById('toastContainer');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = `toast${type === 'error' ? ' toast-error' : ''}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => toast.remove(), duration);
    },

    showSuccessToast(message) {
        this.showToast(message, 'success', 4000);
    },

    showErrorToast(message) {
        this.showToast(message, 'error', 6000);
    },

    showInfoToast(message) {
        this.showToast(message, 'info', 4000);
    }
};
