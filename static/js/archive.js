/**
 * Gmail Cleanup - Archive View (Phase 3)
 *
 * Archive gets its own sidebar tab and its own scan (previously it only
 * operated on whatever the Delete tab had already scanned, with no
 * filters/scan of its own - see PROGRESS.md).
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Archive = (() => {
    const view = GmailCleaner.SenderList.create({
        prefix: 'archive',
        scanEndpoint: '/api/archive-scan',
        scanStatusEndpoint: '/api/archive-scan-status',
        scanResultsEndpoint: '/api/archive-scan-results',
        showUnsubscribe: false,
        showSubjectPreview: false,
        emptyAfterScanTitle: 'No senders found',
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
            <button class="btn btn-primary" id="archiveSelectedBtn"><i class="ti ti-archive"></i>Archive selected</button>
        `;
        document.getElementById('archiveSelectedBtn').addEventListener('click', archiveSelected);
    }

    async function archiveSelected() {
        const emails = view.getSelectedSenderEmails();
        if (emails.length === 0) {
            GmailCleaner.UI.showErrorToast('No senders selected');
            return;
        }
        try {
            await fetch('/api/archive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senders: emails, filters: view.filters })
            });
            await pollArchive();
            view.removeSendersFromResults(emails);
            GmailCleaner.UI.showSuccessToast(`Archived emails from ${emails.length} sender${emails.length === 1 ? '' : 's'}`);
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error archiving: ' + error.message);
        }
    }

    async function pollArchive(attempts = 0) {
        const response = await fetch('/api/archive-status');
        const status = await response.json();
        if (status.error) throw new Error(status.error);
        if (status.done) return;
        if (attempts > 600) throw new Error('Archive timed out');
        await new Promise(resolve => setTimeout(resolve, 300));
        return pollArchive(attempts + 1);
    }

    buildActionButtons();

    return { view };
})();
