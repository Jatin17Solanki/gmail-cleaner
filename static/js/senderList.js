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
 * Expand/collapse ships as a shell only (Phase 3), not the full Phase 4c
 * mechanism: message sub-rows show real subjects (the scan already widens
 * its subject cap to ~20 for this), but the eye icon is inert and
 * per-message checkboxes don't yet scope the bulk action - seePROGRESS.md.
 */

window.GmailCleaner = window.GmailCleaner || {};

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

        if (this.results.length === 0) {
            emptyState?.classList.remove('hidden');
            if (emptyState) {
                const h2 = emptyState.querySelector('h2');
                const p = emptyState.querySelector('p');
                if (h2) h2.textContent = this.emptyAfterScanTitle;
                if (p) p.textContent = this.emptyAfterScanBody;
            }
        } else {
            emptyState?.classList.add('hidden');
            this.results.forEach(sender => {
                rowsContainer.appendChild(this._buildRow(sender));
            });
        }

        this._updateSelectionBar();
    }

    _buildRow(sender) {
        const esc = GmailCleaner.UI.escapeHtml;
        const row = document.createElement('div');
        row.className = 'row sender-row';
        row.dataset.email = sender.email;

        const subtitle = this.showSubjectPreview && sender.subjects && sender.subjects[0]
            ? `${sender.count} emails · ${esc(sender.subjects[0])}`
            : `${sender.count} emails`;

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

        row.querySelector('.row-select-cb')?.addEventListener('change', () => this._updateSelectionBar());
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
            detail.appendChild(this._buildMessageRows(sender));
        }
    }

    _buildMessageRows(sender) {
        const wrap = document.createElement('div');
        const subjects = sender.subjects || [];

        wrap.innerHTML = '<div class="divider"></div>';
        const rowsContainer = document.createElement('div');
        rowsContainer.className = 'message-rows';

        subjects.forEach(subject => {
            const row = document.createElement('div');
            row.className = 'message-row';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'message-cb';
            checkbox.checked = true;
            // Phase 4c ships the actual per-message inclusion mechanism
            // (bulk actions accepting an explicit included-message-ID list
            // per sender); until then, leaving this checkbox interactive
            // implies unchecking it excludes that message from "Delete
            // selected"/"Unsubscribe selected"/etc, which isn't true yet -
            // disable it so the UI doesn't promise something it can't do.
            checkbox.disabled = true;
            checkbox.title = 'Per-message selection is coming in a future update - actions currently apply to this sender as a whole';

            const subjectSpan = document.createElement('span');
            subjectSpan.className = 'message-subject';
            // Subject lines are attacker-controlled (arbitrary email senders) -
            // set as a DOM property, never string-templated into HTML/attrs,
            // so stray quotes/HTML in a subject can't break out of markup.
            subjectSpan.textContent = subject;

            const eyeIcon = document.createElement('i');
            eyeIcon.className = 'ti ti-eye disabled';
            eyeIcon.dataset.action = 'preview';
            eyeIcon.title = 'Full preview - coming in a future phase';
            eyeIcon.addEventListener('click', () => {
                GmailCleaner.UI.showInfoToast('Full email preview is coming in a future phase');
            });

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
            rowsContainer.appendChild(row);
        });

        const moreCount = sender.count - subjects.length;
        if (moreCount > 0) {
            const more = document.createElement('div');
            more.className = 'message-row-more';
            more.textContent = `+${moreCount} more not shown`;
            rowsContainer.appendChild(more);
        }

        wrap.appendChild(rowsContainer);
        return wrap;
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
                        filters: this.filters
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

    getSelectedCount() {
        const emails = this.getSelectedSenderEmails();
        return this.results
            .filter(r => emails.includes(r.email))
            .reduce((sum, r) => sum + r.count, 0);
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
