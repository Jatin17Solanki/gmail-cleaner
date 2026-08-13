/**
 * Gmail Cleaner - Restore Module (Phase 2: Restore-from-Trash)
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Restore = {
    ACTION_LABELS: {
        delete: 'Deleted',
        archive: 'Archived',
        mark_read: 'Marked as read',
        label_add: 'Labeled',
        label_remove: 'Unlabeled'
    },

    async loadEntries() {
        const list = document.getElementById('restoreList');
        const empty = document.getElementById('restoreEmpty');
        if (!list) return;

        try {
            const response = await fetch('/api/restore');
            const entries = await response.json();
            this.render(entries);
        } catch (error) {
            console.error('Error loading restore entries:', error);
            list.innerHTML = '';
            empty.classList.remove('hidden');
            empty.querySelector('p').textContent = 'Failed to load restorable actions.';
        }
    },

    render(entries) {
        const list = document.getElementById('restoreList');
        const empty = document.getElementById('restoreEmpty');
        if (!list || !empty) return;

        list.innerHTML = '';

        if (!entries || entries.length === 0) {
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');

        entries.forEach(entry => {
            const li = document.createElement('li');
            li.className = 'restore-item';
            li.innerHTML = `
                <div>
                    <div class="restore-item-title">${GmailCleaner.UI.escapeHtml(this.describe(entry))}</div>
                    <div class="restore-item-sub">${GmailCleaner.UI.escapeHtml(this.describeSource(entry))}</div>
                </div>
                <button class="btn" data-entry-id="${entry.id}">
                    <i class="ti ti-rotate"></i>Restore
                </button>
            `;
            const restoreBtn = li.querySelector('button');
            restoreBtn.addEventListener('click', () => this.restoreEntry(entry.id, restoreBtn));
            list.appendChild(li);
        });
    },

    describe(entry) {
        const verb = this.ACTION_LABELS[entry.action_type] || entry.action_type;
        const count = entry.message_count;
        const noun = count === 1 ? 'email' : 'emails';

        if (entry.action_type === 'label_add' || entry.action_type === 'label_remove') {
            const label = entry.label_name ? `"${entry.label_name}"` : '';
            return `${verb} ${count} ${noun} ${label}`.trim();
        }
        if (entry.senders && entry.senders.length > 0) {
            const maxShown = 3;
            const shown = entry.senders.slice(0, maxShown).join(', ');
            const extra = entry.senders.length - maxShown;
            const senderList = extra > 0 ? `${shown} +${extra} more` : shown;
            return `${verb} ${count} ${noun} · ${senderList}`;
        }
        return `${verb} ${count} ${noun}`;
    },

    describeSource(entry) {
        const sourceLabel = entry.source === 'manual' ? 'Manual action' : entry.source;
        const when = new Date(entry.timestamp).toLocaleString();
        return `${sourceLabel} · ${when}`;
    },

    async restoreEntry(entryId, triggerBtn) {
        GmailCleaner.UI.setButtonLoading(triggerBtn, 'Restoring...');
        try {
            const response = await fetch(`/api/restore/${entryId}`, { method: 'POST' });
            const result = await response.json();

            if (result.success) {
                GmailCleaner.UI.showSuccessToast(result.message || 'Restored successfully');
            } else {
                GmailCleaner.UI.showErrorToast(result.message || 'Restore failed');
            }
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Restore failed: ' + error.message);
        }

        this.loadEntries();
    }
};
