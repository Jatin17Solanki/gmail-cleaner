/**
 * Gmail Cleanup - Delete View (Phase 3)
 *
 * Merged with the old standalone Unsubscribe tab (PRD Section 5): one scan
 * (scan_senders_for_delete) now surfaces both delete counts and per-sender
 * unsubscribe status, so this is the only view with the "Unsub" toggle/
 * badge and the "Unsubscribe selected" bulk action alongside Delete's own.
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Delete = (() => {
    const view = GmailCleaner.SenderList.create({
        prefix: 'delete',
        scanEndpoint: '/api/delete-scan',
        scanStatusEndpoint: '/api/delete-scan-status',
        scanResultsEndpoint: '/api/delete-scan-results',
        showUnsubscribe: true,
        showSubjectPreview: true,
        emptyAfterScanTitle: 'No senders found',
        emptyAfterScanBody: 'Try widening your filters and scanning again.',
        onSelectionChange: (emails, count, unsubToggled) => updateSelectionBar(emails, count, unsubToggled),
    });

    function updateSelectionBar(emails, count, unsubToggled) {
        const summary = view.id('SelectionSummary');
        if (!summary) return;

        const deletePart = emails.length > 0
            ? `${count} emails selected across ${emails.length} sender${emails.length === 1 ? '' : 's'}`
            : '';
        const unsubPart = unsubToggled.length > 0
            ? `${unsubToggled.length} sender${unsubToggled.length === 1 ? '' : 's'} queued to unsubscribe`
            : '';

        summary.textContent = [deletePart, unsubPart].filter(Boolean).join(' · ') || '0 emails selected';
    }

    function buildActionButtons() {
        const container = view.id('ActionButtons');
        if (!container) return;
        container.innerHTML = `
            <button class="btn" id="deleteUnsubSelectedBtn">Unsubscribe selected</button>
            <button class="btn" id="deleteDownloadBtn" title="Download as CSV"><i class="ti ti-download"></i></button>
            <button class="btn btn-danger" id="deleteSelectedBtn"><i class="ti ti-trash"></i>Delete selected</button>
        `;
        document.getElementById('deleteUnsubSelectedBtn').addEventListener('click', unsubscribeSelected);
        document.getElementById('deleteDownloadBtn').addEventListener('click', downloadSelected);
        document.getElementById('deleteSelectedBtn').addEventListener('click', deleteSelected);
    }

    async function unsubscribeSelected() {
        const toggled = view.getUnsubToggledSenders().filter(s => s.link);
        if (toggled.length === 0) {
            GmailCleaner.UI.showErrorToast('No senders have "Unsub" toggled on');
            return;
        }

        let succeeded = 0;
        for (const sender of toggled) {
            try {
                const resp = await fetch('/api/unsubscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ domain: sender.email, link: sender.link })
                });
                const result = await resp.json();
                if (result.success) succeeded++;
            } catch (error) {
                console.error('Unsubscribe error for', sender.email, error);
            }
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        GmailCleaner.UI.showSuccessToast(`Unsubscribed from ${succeeded}/${toggled.length} senders`);
    }

    async function downloadSelected() {
        const emails = view.getSelectedSenderEmails();
        if (emails.length === 0) {
            GmailCleaner.UI.showErrorToast('No senders selected');
            return;
        }
        try {
            await fetch('/api/download-emails', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: emails })
            });
            await pollDownload();
            window.location.href = '/api/download-csv';
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error downloading: ' + error.message);
        }
    }

    async function pollDownload(attempts = 0) {
        const response = await fetch('/api/download-status');
        const status = await response.json();
        if (status.error) throw new Error(status.error);
        if (status.done) return;
        // Generous sanity ceiling (30 min), not a tight estimate - quota
        // pacing (Phase 4a2) means a large download can legitimately take
        // several minutes. See senderList.js's _pollScanStatus for the math.
        if (attempts > 6000) throw new Error('Download timed out');
        await new Promise(resolve => setTimeout(resolve, 300));
        return pollDownload(attempts + 1);
    }

    function deleteSelected() {
        const emails = view.getSelectedSenderEmails();
        if (emails.length === 0) {
            GmailCleaner.UI.showErrorToast('No senders selected');
            return;
        }
        // getSelectedCount() sums each sender's real total (not just what
        // the scan happened to sample) - this is every message matching
        // sender + active filters, since that's what the action itself
        // actually affects, so the confirm step must not understate it.
        const count = view.getSelectedCount();
        const summary = view.id('ConfirmSummary');
        if (summary) {
            summary.textContent = `Delete ${count} email${count === 1 ? '' : 's'} from ${emails.length} sender${emails.length === 1 ? '' : 's'}? This includes all of their mail matching your active filters, not just what's shown here. They'll be moved to Trash and kept for 30 days.`;
        }
        view.id('ConfirmOverlay')?.classList.remove('hidden');
    }

    function closeConfirm() {
        view.id('ConfirmOverlay')?.classList.add('hidden');
    }

    async function confirmDelete() {
        const emails = view.getSelectedSenderEmails();
        closeConfirm();
        if (emails.length === 0) return;
        try {
            await fetch('/api/delete-emails-bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: emails, excluded_message_ids: view.getExcludedMessageIds() })
            });
            await pollDeleteBulk();
            view.removeSendersFromResults(emails);
            GmailCleaner.UI.showSuccessToast(`Deleted emails from ${emails.length} sender${emails.length === 1 ? '' : 's'}`);
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error deleting: ' + error.message);
        }
    }

    async function pollDeleteBulk(attempts = 0) {
        const response = await fetch('/api/delete-bulk-status');
        const status = await response.json();
        if (status.error) throw new Error(status.error);
        if (status.done) return;
        // Generous sanity ceiling (30 min) - see pollDownload above.
        if (attempts > 6000) throw new Error('Delete timed out');
        await new Promise(resolve => setTimeout(resolve, 300));
        return pollDeleteBulk(attempts + 1);
    }

    buildActionButtons();
    view.id('ConfirmCancelBtn')?.addEventListener('click', closeConfirm);
    view.id('ConfirmDeleteBtn')?.addEventListener('click', confirmDelete);

    return { view };
})();
