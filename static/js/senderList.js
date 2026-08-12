/**
 * Gmail Cleanup - Shared Sender-Row-List Module (Phase 3)
 *
 * Delete, Mark-as-read, and Archive each get their own scan, filter drawer,
 * and sender-row list (PRD Section 5) - this module implements that shared
 * shell once instead of tripling near-identical rendering code across
 * delete.js/archive.js/markread.js. Each view's own JS file supplies a
 * small config (endpoints, whether to show unsubscribe affordances, the
 * action buttons in the bottom bar) and owns whatever is genuinely
 * view-specific (the bulk action itself).
 *
 * Phase 4c: the expanded sender row is the full mechanism, not just a
 * shell. The scan already fetches every matched message's subject (no
 * server-side cap - see delete.py/archive.py/mark_read.py), so "Load more"
 * is a pure client-side reveal over already-downloaded data, no extra
 * network call. Per-message checkboxes are real: unchecking one excludes
 * that message ID from the next bulk action on its sender (see
 * getExcludedMessageIds()). The eye icon opens the message in the user's
 * real Gmail web UI in a new tab (a plain deep link using the message's
 * own ID) rather than fetching/rendering the body in-app - no extra Gmail
 * API cost, and no attacker-controlled HTML ever touches this app's origin.
 */

window.GmailCleaner = window.GmailCleaner || {};

// How many message sub-rows to reveal per "Load more" click, in an
// expanded sender row. Purely a rendering page size - the scan already
// has every matched message's subject/id in memory (see module comment
// above), so this doesn't gate what data exists, only how much of it is
// in the DOM at once.
const MESSAGE_PAGE_SIZE = 20;

// Shown once per page load (not once per tab) the first time any eye icon
// is clicked, across Delete/Archive/Mark-as-read alike - a reminder, not a
// blocking check, since a not-signed-in click just lands on Gmail's own
// account picker rather than failing silently.
let hasShownGmailSignInHint = false;

GmailCleaner.SenderList = {
    views: {},

    create(config) {
        const view = new SenderListView(config);
        this.views[config.prefix] = view;
        return view;
    },

    // Turns a backend-computed estimated_seconds (Phase 4a2's
    // quota.estimate_scan_seconds) into a human message with both a
    // relative duration and an absolute "come back around" clock time,
    // since a countdown alone doesn't tell you when to actually check back.
    //
    // readyAtMs, if given, is an already-fixed target timestamp (see
    // SenderListView._pollScanStatus) - without it, this would recompute
    // Date.now() + seconds on every call, and since this runs on every
    // 300ms poll tick during an active scan, the displayed "come back
    // around" time would keep creeping forward instead of staying fixed.
    formatEta(seconds, readyAtMs = null) {
        const minutes = Math.max(1, Math.round(seconds / 60));
        const duration = minutes === 1 ? 'about a minute' : `about ${minutes} minutes`;
        const readyAt = new Date(readyAtMs !== null ? readyAtMs : Date.now() + seconds * 1000);
        const readyAtText = readyAt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        return `This scan will take ${duration} to complete — come back around ${readyAtText} to see results.`;
    }
};

class SenderListView {
    constructor(config) {
        this.prefix = config.prefix;
        this.scanEndpoint = config.scanEndpoint;
        this.scanStatusEndpoint = config.scanStatusEndpoint;
        this.scanResultsEndpoint = config.scanResultsEndpoint;
        this.showUnsubscribe = !!config.showUnsubscribe;
        this.showSubjectPreview = !!config.showSubjectPreview;
        this.showUnreadFilter = config.showUnreadFilter !== false;
        this.emptyAfterScanTitle = config.emptyAfterScanTitle || 'No senders found';
        this.emptyAfterScanBody = config.emptyAfterScanBody || 'Try widening your filters and scanning again.';
        this.onSelectionChange = config.onSelectionChange || (() => {});
        this.onResultsChange = config.onResultsChange || (() => {});

        this.results = [];
        this.filters = {};
        this.expanded = new Set();
        this.litepicker = null;
        this.scanning = false;
        this._scanReadyAtMs = null;

        this._initialEmptyHtml = null;

        this._bindScan();
        this._bindDrawer();
        this._bindSelectAll();
    }

    id(suffix) {
        return document.getElementById(this.prefix + suffix);
    }

    // ----- Scan -----

    _bindScan() {
        this.id('ScanBtn')?.addEventListener('click', () => this.scan());
    }

    async scan() {
        if (this.scanning) return;
        this.scanning = true;

        const limitSelect = this.id('ScanLimit');
        const limit = limitSelect ? parseInt(limitSelect.value, 10) : 1000;

        const progress = this.id('ScanProgress');
        const progressText = this.id('ScanProgressText');
        const scanBtn = this.id('ScanBtn');
        const eta = this.id('ScanEta');
        const etaText = this.id('ScanEtaText');
        if (scanBtn) scanBtn.disabled = true;
        progress?.classList.remove('hidden');
        if (progressText) progressText.textContent = 'Scanning...';
        eta?.classList.add('hidden');
        this._scanReadyAtMs = null;

        try {
            await fetch(this.scanEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ limit, filters: this.filters })
            });
            await this._pollScanStatus(progressText, 0, eta, etaText);
            const resultsResp = await fetch(this.scanResultsEndpoint);
            this.results = await resultsResp.json();
            this.render();
            this.onResultsChange(this.results);
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error scanning: ' + error.message);
        } finally {
            this.scanning = false;
            if (scanBtn) scanBtn.disabled = false;
            progress?.classList.add('hidden');
            eta?.classList.add('hidden');
            this._scanReadyAtMs = null;
        }
    }

    async _pollScanStatus(progressText, attempts = 0, eta = null, etaText = null) {
        // Quota-aware scans (Phase 4a2) can legitimately pace themselves
        // across several minutes for a large limit - a scan near the
        // 5000-email preset needs roughly 17 minutes under Gmail's
        // 6,000-units/minute cap. This is a generous sanity ceiling (30
        // minutes), not a tight estimate - it exists to eventually catch a
        // genuinely hung request, not to bound normal large scans.
        const maxAttempts = 6000;
        const response = await fetch(this.scanStatusEndpoint);
        const status = await response.json();

        if (status.error) {
            throw new Error(status.error);
        }
        if (progressText && status.message) {
            progressText.textContent = status.message;
        }
        // Only worth surfacing a "come back later" message for a
        // meaningfully long wait - a fast scan doesn't need it.
        if (eta && etaText) {
            if (status.estimated_seconds && status.estimated_seconds > 30) {
                // Pin the target timestamp the first time it's shown for
                // this scan - recomputing Date.now() + estimated_seconds on
                // every 300ms poll tick would make the displayed time keep
                // creeping forward instead of staying fixed.
                if (this._scanReadyAtMs === null) {
                    this._scanReadyAtMs = Date.now() + status.estimated_seconds * 1000;
                }
                etaText.textContent = GmailCleaner.SenderList.formatEta(
                    status.estimated_seconds, this._scanReadyAtMs
                );
                eta.classList.remove('hidden');
            } else {
                eta.classList.add('hidden');
            }
        }
        if (status.done) return;
        if (attempts >= maxAttempts) {
            throw new Error('Scan timed out');
        }
        await new Promise(resolve => setTimeout(resolve, 300));
        return this._pollScanStatus(progressText, attempts + 1, eta, etaText);
    }

    // ----- Rendering -----

    render() {
        const rowsContainer = this.id('Rows');
        const emptyState = this.id('EmptyState');
        if (!rowsContainer) return;

        rowsContainer.innerHTML = '';
        this.expanded.clear();

        const selectAllBar = this.id('SelectAllBar');
        const selectAllCb = this.id('SelectAllCb');

        if (this.results.length === 0) {
            emptyState?.classList.remove('hidden');
            if (emptyState) {
                const h2 = emptyState.querySelector('h2');
                const p = emptyState.querySelector('p');
                if (h2) h2.textContent = this.emptyAfterScanTitle;
                if (p) p.textContent = this.emptyAfterScanBody;
            }
            selectAllBar?.classList.add('hidden');
        } else {
            emptyState?.classList.add('hidden');
            this.results.forEach(sender => {
                rowsContainer.appendChild(this._buildRow(sender));
            });
            selectAllBar?.classList.remove('hidden');
        }
        if (selectAllCb) {
            selectAllCb.checked = false;
            selectAllCb.indeterminate = false;
        }

        this._updateSelectionBar();
    }

    // Sets a sender row's own checkbox and cascades to its (possibly not
    // yet built) message checkboxes - the one place that does both, so
    // select-all (which sets .row-select-cb.checked programmatically, which
    // never fires 'change') and a direct click on the row checkbox (which
    // does) stay consistent with each other.
    _setRowSelected(row, checked) {
        const cb = row.querySelector('.row-select-cb');
        if (cb) cb.checked = checked;
        this._syncMessageCheckboxes(row, checked);
    }

    // "Select all" toggles every sender's row checkbox at once - e.g.
    // "delete everything except a few senders" otherwise means checking
    // dozens of boxes individually (PROGRESS.md backlog item 9).
    _bindSelectAll() {
        this.id('SelectAllCb')?.addEventListener('change', (e) => {
            const checked = e.target.checked;
            this.id('Rows').querySelectorAll('.sender-row').forEach(row => {
                this._setRowSelected(row, checked);
            });
            this._updateSelectionBar();
        });
    }

    // Keeps the header checkbox in sync with the individual rows: checked
    // when every row is selected, indeterminate when some but not all are,
    // unchecked when none are.
    _syncSelectAllCheckbox() {
        const selectAllCb = this.id('SelectAllCb');
        if (!selectAllCb) return;
        const rows = [...this.id('Rows').querySelectorAll('.row-select-cb')];
        const checkedCount = rows.filter(cb => cb.checked).length;
        selectAllCb.checked = rows.length > 0 && checkedCount === rows.length;
        selectAllCb.indeterminate = checkedCount > 0 && checkedCount < rows.length;
    }

    _buildRow(sender) {
        const esc = GmailCleaner.UI.escapeHtml;
        const row = document.createElement('div');
        row.className = 'row sender-row';
        row.dataset.email = sender.email;

        // total_count (Gmail's own resultSizeEstimate for sender+filters) is
        // the real number an action will affect - count is only how many
        // fell within the scanned window, which can badly understate it for
        // a sender with more mail than the scan looked through. Never show
        // a total below what the scan already confirmed exists.
        const total = Math.max(sender.total_count ?? sender.count, sender.count);
        const countLabel = total > sender.count
            ? `${sender.count} shown of ${total} total emails`
            : `${total} emails`;
        const subtitle = this.showSubjectPreview && sender.subjects && sender.subjects[0]
            ? `${countLabel} · ${esc(sender.subjects[0])}`
            : countLabel;

        let unsubHtml = '';
        if (this.showUnsubscribe) {
            const hasLink = !!sender.unsubscribe_link;
            const isOneClick = sender.unsubscribe_type === 'one-click';
            const badgeClass = hasLink ? (isOneClick ? 'badge-success' : 'badge-warning') : 'badge-muted';
            const badgeLabel = hasLink ? (isOneClick ? 'Auto' : 'Open link') : 'No unsubscribe link';
            const badgeTitle = hasLink
                ? (isOneClick
                    ? 'One-click unsubscribe - no extra confirmation needed'
                    : 'Opens the sender\'s unsubscribe page - you may need to confirm there')
                : 'No unsubscribe link was found in this sender\'s emails';
            unsubHtml = `
                <span class="badge ${badgeClass}" title="${GmailCleaner.UI.escapeHtml(badgeTitle)}">${badgeLabel}</span>
                <label class="unsub-toggle ${hasLink ? '' : 'disabled'}">
                    <input type="checkbox" class="unsub-cb" ${hasLink ? '' : 'disabled'}>Unsub
                </label>`;
        }

        row.innerHTML = `
            <div class="sender-row-header">
                <input type="checkbox" class="row-select-cb">
                <div class="sender-row-meta">
                    <div class="meta-title">${esc(sender.email)}</div>
                    <div class="meta-sub">${subtitle}</div>
                </div>
                ${unsubHtml}
                <i class="ti ti-tag sender-row-action-icon" data-action="label" title="Label"></i>
                <i class="ti ti-star sender-row-action-icon" data-action="important" title="Mark important"></i>
                <i class="ti ti-chevron-down row-chevron" data-action="toggle-expand"></i>
            </div>
            <div class="sender-row-detail hidden"></div>
        `;

        row.querySelector('.row-select-cb')?.addEventListener('change', (e) => {
            this._syncMessageCheckboxes(row, e.target.checked);
            this._updateSelectionBar();
        });
        // Note: this listener already handles the direct-click case;
        // _setRowSelected (used by select-all) covers the programmatic case,
        // since setting .checked directly doesn't fire 'change'.
        row.querySelector('.unsub-cb')?.addEventListener('change', () => this._updateSelectionBar());
        row.querySelector('[data-action="toggle-expand"]')?.addEventListener('click', () => this._toggleExpand(row, sender));
        row.querySelector('[data-action="label"]')?.addEventListener('click', () => this._openLabelPicker(row, sender));
        row.querySelector('[data-action="important"]')?.addEventListener('click', (e) => this._toggleImportant(row, sender, e.currentTarget));

        return row;
    }

    _toggleExpand(row, sender) {
        const detail = row.querySelector('.sender-row-detail');
        const chevron = row.querySelector('.row-chevron');
        const email = sender.email;

        if (this.expanded.has(email)) {
            this.expanded.delete(email);
            detail.classList.add('hidden');
            chevron.classList.remove('ti-chevron-up');
            chevron.classList.add('ti-chevron-down');
            return;
        }

        this.expanded.add(email);
        chevron.classList.remove('ti-chevron-down');
        chevron.classList.add('ti-chevron-up');
        detail.classList.remove('hidden');

        if (!detail.dataset.built) {
            detail.dataset.built = '1';
            detail.appendChild(this._buildMessageRows(sender, row));
        }
    }

    // Message checkboxes must obey the parent sender-row checkbox, not just
    // start checked regardless of it - a deselected sender shouldn't show
    // its messages as "included" when expanded, and toggling the parent
    // afterward must propagate to any already-built children too.
    _syncMessageCheckboxes(row, checked) {
        row.querySelectorAll('.message-cb').forEach(cb => { cb.checked = checked; });
    }

    _buildMessageRows(sender, row) {
        const wrap = document.createElement('div');
        wrap.innerHTML = '<div class="divider"></div>';

        const rowsContainer = document.createElement('div');
        rowsContainer.className = 'message-rows';
        wrap.appendChild(rowsContainer);

        const loadMoreBtn = document.createElement('button');
        loadMoreBtn.type = 'button';
        loadMoreBtn.className = 'message-row-load-more';
        rowsContainer.appendChild(loadMoreBtn);
        loadMoreBtn.addEventListener('click', () => this._revealMoreMessages(rowsContainer, sender, loadMoreBtn, row));

        this._revealMoreMessages(rowsContainer, sender, loadMoreBtn, row);

        return wrap;
    }

    // Reveals the next MESSAGE_PAGE_SIZE message rows into rowsContainer,
    // tracking how many are already shown via the container's own child
    // count (minus the "Load more" button itself) - no separate counter to
    // keep in sync. All data is already in `sender.subjects`/`message_ids`
    // (the scan fetched every matched message, see module comment) so this
    // never makes a network call. Newly-revealed rows are initialized to
    // the parent sender-row's *current* checkbox state, read fresh each
    // call - covers both the first reveal and a later "Load more" after
    // the parent's been toggled since expand.
    _revealMoreMessages(rowsContainer, sender, loadMoreBtn, row) {
        const subjects = sender.subjects || [];
        const messageIds = sender.message_ids || [];
        const parentChecked = row.querySelector('.row-select-cb')?.checked ?? true;
        const shown = rowsContainer.children.length - 1;
        const next = Math.min(shown + MESSAGE_PAGE_SIZE, subjects.length);
        for (let i = shown; i < next; i++) {
            rowsContainer.insertBefore(
                this._buildMessageRow(subjects[i], messageIds[i], parentChecked),
                loadMoreBtn
            );
        }
        const remaining = subjects.length - next;
        if (remaining > 0) {
            loadMoreBtn.textContent = `Load 20 more (${remaining} remaining)`;
            loadMoreBtn.classList.remove('hidden');
            return;
        }
        loadMoreBtn.classList.add('hidden');

        // The preview can only ever show what the scan actually fetched
        // (subjects.length === sender.count) - if the sender's real total
        // is bigger, say so explicitly instead of the list just stopping
        // with no explanation (Phase 4c round-2 finding: pairing an honest
        // "X shown of Y total" with an unexplained preview cutoff at X was
        // its own source of confusion).
        const total = Math.max(sender.total_count ?? sender.count, sender.count);
        if (total > subjects.length && !rowsContainer.querySelector('.message-row-preview-limit')) {
            const note = document.createElement('div');
            note.className = 'message-row-preview-limit';
            note.textContent = `That's everything from this sender in your last scan. ${total} total match your filters — rescan with a higher limit to preview more.`;
            rowsContainer.insertBefore(note, loadMoreBtn);
        }
    }

    _buildMessageRow(subject, messageId, checked = true) {
        const row = document.createElement('div');
        row.className = 'message-row';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'message-cb';
        checkbox.checked = checked;
        checkbox.dataset.messageId = messageId;
        checkbox.title = 'Uncheck to exclude this message from the next bulk action on this sender';

        const subjectSpan = document.createElement('span');
        subjectSpan.className = 'message-subject';
        // Subject lines are attacker-controlled (arbitrary email senders) -
        // set as a DOM property, never string-templated into HTML/attrs,
        // so stray quotes/HTML in a subject can't break out of markup.
        subjectSpan.textContent = subject;

        const eyeIcon = document.createElement('i');
        eyeIcon.className = 'ti ti-eye';
        eyeIcon.dataset.action = 'preview';
        eyeIcon.title = 'Open in Gmail (requires being signed into this account in your browser)';
        eyeIcon.addEventListener('click', () => this._openInGmail(messageId));

        const copyIcon = document.createElement('i');
        copyIcon.className = 'ti ti-copy copy-icon';
        copyIcon.dataset.action = 'copy';
        copyIcon.title = 'Copy subject';
        copyIcon.addEventListener('click', () => {
            navigator.clipboard?.writeText(subject).then(() => {
                GmailCleaner.UI.showSuccessToast('Subject copied to clipboard');
            }).catch(() => {});
        });

        row.append(checkbox, subjectSpan, eyeIcon, copyIcon);
        return row;
    }

    // Opens the real Gmail web UI on this exact message, in a new tab - no
    // Gmail API call, no email-body rendering in this app (see module
    // comment). Requires the user already be signed into that account in
    // their browser, same as clicking a Gmail link anywhere else.
    _openInGmail(messageId) {
        if (!messageId) return;
        const email = GmailCleaner.Auth?.currentEmail || '';
        if (!hasShownGmailSignInHint) {
            hasShownGmailSignInHint = true;
            GmailCleaner.UI.showInfoToast(
                email ? `Opening in Gmail — sign in as ${email} there if prompted.` : 'Opening in Gmail — sign in there if prompted.'
            );
        }
        const url = `https://mail.google.com/mail/?authuser=${encodeURIComponent(email)}#all/${encodeURIComponent(messageId)}`;
        window.open(url, '_blank', 'noopener');
    }

    // Flat list of message IDs whose per-message checkbox was unchecked,
    // across every currently-expanded sender row. Bulk actions treat this
    // as "exclude from an otherwise sender-wide action," not an include-list
    // (see delete.py's delete_emails_bulk_background for why) - an ID here
    // that doesn't belong to the sender(s) a given action targets is simply
    // a no-op for that action.
    getExcludedMessageIds() {
        return this._excludedMessageIdsWithin(this.id('Rows'));
    }

    _excludedMessageIdsWithin(container) {
        return [...container.querySelectorAll('.message-cb')]
            .filter(cb => !cb.checked)
            .map(cb => cb.dataset.messageId)
            .filter(Boolean);
    }

    async _toggleImportant(row, sender, iconEl) {
        const nowImportant = !iconEl.classList.contains('active');
        iconEl.classList.toggle('active', nowImportant);
        try {
            await fetch('/api/mark-important', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    senders: [sender.email],
                    important: nowImportant,
                    filters: this.filters
                })
            });
            GmailCleaner.UI.showSuccessToast(
                nowImportant ? `Marked ${sender.email} as important` : `Unmarked ${sender.email} as important`
            );
        } catch (error) {
            iconEl.classList.toggle('active', !nowImportant);
            GmailCleaner.UI.showErrorToast('Error updating important: ' + error.message);
        }
    }

    async _openLabelPicker(row, sender) {
        if (!this.expanded.has(sender.email)) {
            this._toggleExpand(row, sender);
        }
        const detail = row.querySelector('.sender-row-detail');
        let toolbar = detail.querySelector('.label-apply-toolbar');
        if (toolbar) {
            toolbar.classList.toggle('hidden');
            return;
        }

        toolbar = document.createElement('div');
        toolbar.className = 'label-apply-toolbar';
        toolbar.innerHTML = `
            <span style="font-size:11px;color:var(--text-secondary)">Apply label:</span>
            <select class="label-toolbar-select"></select>
            <button class="btn label-toolbar-apply">Apply</button>
        `;
        detail.insertBefore(toolbar, detail.firstChild);

        const select = toolbar.querySelector('.label-toolbar-select');
        const labels = await GmailCleaner.Labels.loadLabels();
        (labels?.user_labels || []).forEach(label => {
            const option = document.createElement('option');
            option.value = label.id;
            option.textContent = label.name;
            select.appendChild(option);
        });

        toolbar.querySelector('.label-toolbar-apply').addEventListener('click', async () => {
            const labelId = select.value;
            if (!labelId) return;
            try {
                await fetch('/api/apply-label', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        label_id: labelId,
                        senders: [sender.email],
                        filters: this.filters,
                        excluded_message_ids: this._excludedMessageIdsWithin(detail)
                    })
                });
                GmailCleaner.UI.showSuccessToast(`Label applied to ${sender.email}`);
            } catch (error) {
                GmailCleaner.UI.showErrorToast('Error applying label: ' + error.message);
            }
        });
    }

    // ----- Selection -----

    getSelectedSenderEmails() {
        return [...this.id('Rows').querySelectorAll('.sender-row')]
            .filter(row => row.querySelector('.row-select-cb')?.checked)
            .map(row => row.dataset.email);
    }

    getUnsubToggledSenders() {
        return [...this.id('Rows').querySelectorAll('.sender-row')]
            .filter(row => row.querySelector('.unsub-cb')?.checked)
            .map(row => ({
                email: row.dataset.email,
                link: this.results.find(r => r.email === row.dataset.email)?.unsubscribe_link
            }));
    }

    // Sums real totals (total_count), not the scanned sample (count) - a
    // bulk action affects every message matching sender+filters, not just
    // whatever fell within the scan's own window, so the selection summary
    // and confirm dialogs must reflect that real number, not understate it.
    getSelectedCount() {
        const emails = this.getSelectedSenderEmails();
        return this.results
            .filter(r => emails.includes(r.email))
            .reduce((sum, r) => sum + Math.max(r.total_count ?? r.count, r.count), 0);
    }

    removeSendersFromResults(emails) {
        this.results = this.results.filter(r => !emails.includes(r.email));
        emails.forEach(email => {
            this.id('Rows').querySelector(`.sender-row[data-email="${CSS.escape(email)}"]`)?.remove();
        });
        if (this.results.length === 0) {
            this.render();
        } else {
            this._updateSelectionBar();
        }
        this.onResultsChange(this.results);
    }

    _updateSelectionBar() {
        const emails = this.getSelectedSenderEmails();
        // The Unsub toggle is independent of the main row checkbox (a sender
        // can be queued to unsubscribe without being selected for delete, or
        // vice versa) - the actions bar must show for either, not just the
        // main checkbox, so "Unsubscribe selected" stays reachable.
        const unsubToggled = this.showUnsubscribe ? this.getUnsubToggledSenders() : [];
        const hasSelection = emails.length > 0 || unsubToggled.length > 0;
        this.id('ActionsBar')?.classList.toggle('hidden', !hasSelection);
        this._syncSelectAllCheckbox();
        this.onSelectionChange(emails, this.getSelectedCount(), unsubToggled);
    }

    // ----- Filter drawer -----

    _bindDrawer() {
        this.id('FiltersBtn')?.addEventListener('click', () => this.id('DrawerOverlay')?.classList.remove('hidden'));
        this.id('DrawerClose')?.addEventListener('click', () => this.id('DrawerOverlay')?.classList.add('hidden'));
        this.id('FilterClearBtn')?.addEventListener('click', () => this._clearDrawerFields());
        this.id('FilterApplyBtn')?.addEventListener('click', () => this._applyDrawer());

        this.id('FilterOlderThan')?.addEventListener('change', (e) => this._handleOlderThanChange(e));

        // Clicking the backdrop (outside the 280px drawer panel, but inside
        // the overlay that spans the view) closes without applying -
        // Clear/Apply are the only actions that touch filter state, same as
        // the drawer's own explicit Cancel-equivalent (the X icon).
        this.id('DrawerOverlay')?.addEventListener('click', (e) => {
            if (e.target === this.id('DrawerOverlay')) {
                this.id('DrawerOverlay').classList.add('hidden');
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !this.id('DrawerOverlay')?.classList.contains('hidden')) {
                this.id('DrawerOverlay').classList.add('hidden');
            }
        });
    }

    _handleOlderThanChange(event) {
        const group = this.id('DateRangeGroup');
        if (event.target.value === 'custom') {
            group?.classList.remove('hidden');
            if (!this.litepicker) {
                this._setupDateRangePicker();
            }
        } else {
            group?.classList.add('hidden');
        }
    }

    _setupDateRangePicker() {
        const input = this.id('DateRangePicker');
        if (!input) return;

        if (!window.Litepicker) {
            this._showDateRangeFallback();
            return;
        }

        try {
            const today = new Date();
            const sevenDaysAgo = new Date(today);
            sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);

            this.litepicker = new Litepicker({
                element: input,
                singleMode: false,
                format: 'YYYY-MM-DD',
                returnFormat: 'YYYY-MM-DD',
                startDate: sevenDaysAgo,
                endDate: today,
                showTooltip: true,
                minDate: new Date(1970, 0, 1),
                maxDate: today,
                autoApply: true,
                position: 'bottom',
                allowEmptyRange: true,
                lang: 'en',
            });

            input.removeAttribute('readonly');
            input.addEventListener('click', () => this.litepicker?.show());
            this._hideDateRangeFallback();
        } catch (error) {
            console.error('Error initializing Litepicker:', error);
            this._showDateRangeFallback();
        }
    }

    _showDateRangeFallback() {
        this.id('DateRangePicker')?.classList.add('hidden');
        this.id('DateRangeFallback')?.classList.remove('hidden');
    }

    _hideDateRangeFallback() {
        this.id('DateRangePicker')?.classList.remove('hidden');
        this.id('DateRangeFallback')?.classList.add('hidden');
    }

    _readDrawerFields() {
        const olderThanValue = this.id('FilterOlderThan')?.value || '';
        let olderThan = '';
        let afterDate = '';
        let beforeDate = '';

        if (olderThanValue === 'custom') {
            if (this.litepicker) {
                const value = this.id('DateRangePicker')?.value || '';
                const dates = value.split(' - ');
                if (dates.length === 2 && /^\d{4}-\d{2}-\d{2}$/.test(dates[0]) && /^\d{4}-\d{2}-\d{2}$/.test(dates[1])) {
                    afterDate = dates[0].trim().replace(/-/g, '/');
                    const endDate = new Date(dates[1].trim() + 'T00:00:00');
                    endDate.setDate(endDate.getDate() + 1);
                    beforeDate = endDate.toISOString().split('T')[0].replace(/-/g, '/');
                }
            } else {
                const startStr = this.id('DateRangeStart')?.value || '';
                const endStr = this.id('DateRangeEnd')?.value || '';
                if (/^\d{4}-\d{2}-\d{2}$/.test(startStr) && /^\d{4}-\d{2}-\d{2}$/.test(endStr)) {
                    afterDate = startStr.replace(/-/g, '/');
                    const endDate = new Date(endStr + 'T00:00:00');
                    endDate.setDate(endDate.getDate() + 1);
                    beforeDate = endDate.toISOString().split('T')[0].replace(/-/g, '/');
                }
            }
        } else {
            olderThan = olderThanValue;
        }

        return {
            older_than: olderThan,
            after_date: afterDate,
            before_date: beforeDate,
            larger_than: this.id('FilterLargerThan')?.value || '',
            category: this.id('FilterCategory')?.value || '',
            sender: this.id('FilterSender')?.value?.trim() || '',
            label: this.id('FilterLabel')?.value || '',
            unread_only: this.showUnreadFilter ? !!this.id('FilterUnreadOnly')?.checked : undefined,
            has_attachment: !!this.id('FilterHasAttachment')?.checked,
        };
    }

    _applyDrawer() {
        const raw = this._readDrawerFields();
        this.filters = Object.fromEntries(
            Object.entries(raw).filter(([, v]) => v !== '' && v !== false && v !== undefined)
        );
        this._renderChips(raw);
        this.id('DrawerOverlay')?.classList.add('hidden');
        this.scan();
    }

    _clearDrawerFields() {
        [this.id('FilterOlderThan'), this.id('FilterLargerThan'), this.id('FilterCategory'),
         this.id('FilterSender'), this.id('FilterLabel')].forEach(el => { if (el) el.value = ''; });
        if (this.id('FilterUnreadOnly')) this.id('FilterUnreadOnly').checked = false;
        if (this.id('FilterHasAttachment')) this.id('FilterHasAttachment').checked = false;
        if (this.litepicker) this.litepicker.setDateRange(null, null);
        if (this.id('DateRangeStart')) this.id('DateRangeStart').value = '';
        if (this.id('DateRangeEnd')) this.id('DateRangeEnd').value = '';
        this.id('DateRangeGroup')?.classList.add('hidden');
    }

    _renderChips(raw) {
        const chipsEl = this.id('FilterChips');
        const badge = this.id('FilterBadge');
        if (!chipsEl) return;

        const labels = [];
        const olderThanLabels = { '7d': '7 days', '30d': '30 days', '90d': '90 days', '180d': '180 days', '365d': '1 year' };
        if (raw.older_than && olderThanLabels[raw.older_than]) {
            labels.push(`Received within: ${olderThanLabels[raw.older_than]}`);
        } else if (raw.after_date || raw.before_date) {
            labels.push('Received within: custom range');
        }
        if (raw.category) labels.push(`Category: ${raw.category[0].toUpperCase()}${raw.category.slice(1)}`);
        if (raw.sender) labels.push(`Sender: ${raw.sender}`);
        if (raw.label) labels.push(`Label: ${raw.label}`);
        if (raw.larger_than) labels.push(`Larger than: ${raw.larger_than}`);
        if (raw.unread_only) labels.push('Unread only');
        if (raw.has_attachment) labels.push('Has attachment');

        chipsEl.innerHTML = labels.map(l => `<span class="chip">${GmailCleaner.UI.escapeHtml(l)}</span>`).join('');

        if (badge) {
            if (labels.length > 0) {
                badge.textContent = String(labels.length);
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
    }

    populateLabels(userLabels) {
        const select = this.id('FilterLabel');
        if (!select) return;
        select.innerHTML = '<option value="">All labels</option>';
        (userLabels || []).forEach(label => {
            const option = document.createElement('option');
            option.value = label.name;
            option.textContent = label.name;
            select.appendChild(option);
        });
    }

    reset() {
        this.results = [];
        this.render();
    }
}
