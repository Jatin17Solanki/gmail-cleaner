/**
 * Gmail Cleanup - Mark as Read View (Phase 3)
 *
 * Replaces the old blind "mark N most recent unread" count-based flow with
 * a sender-row list matching Delete/Archive - own scan (always scoped to
 * unread mail) and a senders-scoped bulk action. See PROGRESS.md.
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.MarkRead = (() => {
    const view = GmailCleaner.SenderList.create({
        prefix: 'markread',
        scanEndpoint: '/api/markread-scan',
        scanStatusEndpoint: '/api/markread-scan-status',
        scanResultsEndpoint: '/api/markread-scan-results',
        showUnsubscribe: false,
        showSubjectPreview: false,
        showUnreadFilter: false,
        emptyAfterScanTitle: 'No unread senders found',
        emptyAfterScanBody: 'Try widening your filters and scanning again.',
        onSelectionChange: (emails, count) => updateSelectionBar(emails, count),
    });

    function updateSelectionBar(emails, count) {
        const summary = view.id('SelectionSummary');
        if (summary) {
            summary.textContent = `${count} emails selected`;
        }
    }

    function buildActionButtons() {
        const container = view.id('ActionButtons');
        if (!container) return;
        container.innerHTML = `
            <button class="btn btn-primary" id="markReadSelectedBtn"><i class="ti ti-mail-opened"></i>Mark selected as read</button>
        `;
        document.getElementById('markReadSelectedBtn').addEventListener('click', markSelectedAsRead);
    }

    async function markSelectedAsRead() {
        const emails = view.getSelectedSenderEmails();
        if (emails.length === 0) {
            GmailCleaner.UI.showErrorToast('No senders selected');
            return;
        }
        try {
            await fetch('/api/mark-read-bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: emails, filters: view.filters, excluded_message_ids: view.getExcludedMessageIds() })
            });
            await pollMarkRead();
            view.removeSendersFromResults(emails);
            GmailCleaner.UI.showSuccessToast(`Marked emails from ${emails.length} sender${emails.length === 1 ? '' : 's'} as read`);
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error marking as read: ' + error.message);
        }
    }

    async function pollMarkRead(attempts = 0) {
        const response = await fetch('/api/mark-read-status');
        const status = await response.json();
        if (status.error) throw new Error(status.error);
        if (status.done) return;
        // Generous sanity ceiling (30 min), not a tight estimate - quota
        // pacing (Phase 4a2) means a large mark-as-read run can legitimately
        // take several minutes. See senderList.js's _pollScanStatus for the
        // math.
        if (attempts > 6000) throw new Error('Mark-as-read timed out');
        await new Promise(resolve => setTimeout(resolve, 300));
        return pollMarkRead(attempts + 1);
    }

    buildActionButtons();

    return { view };
})();
