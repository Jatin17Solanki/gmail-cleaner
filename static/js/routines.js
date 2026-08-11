/**
 * Gmail Cleanup - Routines Module (Phase 4b)
 *
 * Saved, named presets: a sender list, an age threshold, and one or more
 * actions to apply. Manual trigger only for this build - running a
 * Routine always shows a preview/confirm step before executing (PRD
 * Section 6, Phase 4b - "never executes silently on click").
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Routines = {
    ACTION_LABELS: {
        delete: 'Delete',
        archive: 'Archive',
        mark_read: 'Mark as read',
        label: 'Label',
    },

    senders: [],
    pendingRunRoutineId: null,

    init() {
        document.getElementById('routinesNewBtn').addEventListener('click', () => this.openForm());
        document.getElementById('routineCancelBtn').addEventListener('click', () => this.closeForm());
        document.getElementById('routineSaveBtn').addEventListener('click', () => this.save());
        document.getElementById('routineAddSenderBtn').addEventListener('click', () => this.addSenderFromInput());
        document.getElementById('routineSenderInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.addSenderFromInput();
            }
        });
        document.querySelectorAll('.routine-action-checkbox').forEach((cb) => {
            cb.addEventListener('change', () => this.updateLabelPickerVisibility());
        });
        document.getElementById('routineConfirmCancelBtn').addEventListener('click', () => this.closeConfirm());
        document.getElementById('routineConfirmRunBtn').addEventListener('click', () => this.confirmRun());
    },

    async loadRoutines() {
        const list = document.getElementById('routinesList');
        const empty = document.getElementById('routinesEmpty');
        if (!list) return;

        try {
            const response = await fetch('/api/routines');
            const routines = await response.json();
            this.render(routines);
        } catch (error) {
            console.error('Error loading routines:', error);
            list.innerHTML = '';
            empty.classList.remove('hidden');
            empty.querySelector('p').textContent = 'Failed to load routines.';
        }
    },

    render(routines) {
        const list = document.getElementById('routinesList');
        const empty = document.getElementById('routinesEmpty');
        if (!list || !empty) return;

        list.innerHTML = '';

        if (!routines || routines.length === 0) {
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');

        routines.forEach((routine) => {
            const row = document.createElement('div');
            row.className = 'row';

            const info = document.createElement('div');
            info.style.flex = '1';

            const title = document.createElement('div');
            title.className = 'meta-title';
            title.textContent = routine.name;

            const sub = document.createElement('div');
            sub.className = 'meta-sub';
            sub.textContent = this.describe(routine);

            info.appendChild(title);
            info.appendChild(sub);

            const lastRun = document.createElement('span');
            lastRun.style.fontSize = '11px';
            lastRun.style.color = 'var(--text-muted)';
            lastRun.textContent = routine.last_run_at
                ? `Last run: ${new Date(routine.last_run_at).toLocaleString()}`
                : 'Never run';

            const runBtn = document.createElement('button');
            runBtn.className = 'btn';
            runBtn.innerHTML = '<i class="ti ti-player-play"></i> Run';
            runBtn.addEventListener('click', () => this.openConfirm(routine.id));

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'btn';
            deleteBtn.title = 'Delete routine';
            deleteBtn.innerHTML = '<i class="ti ti-trash"></i>';
            deleteBtn.addEventListener('click', () => this.deleteRoutine(routine.id));

            row.appendChild(info);
            row.appendChild(lastRun);
            row.appendChild(runBtn);
            row.appendChild(deleteBtn);
            list.appendChild(row);
        });
    },

    describe(routine) {
        const senderCount = routine.senders.length;
        const senderNoun = senderCount === 1 ? 'sender' : 'senders';
        const days = routine.older_than.replace(/d$/, '');
        const actionLabels = routine.actions.map((a) => this.ACTION_LABELS[a] || a).join(', ');
        return `${senderCount} ${senderNoun} · older than ${days} days · ${actionLabels}`;
    },

    openForm() {
        this.senders = [];
        document.getElementById('routineNameInput').value = '';
        document.getElementById('routineSenderInput').value = '';
        document.getElementById('routineOlderThanSelect').value = '7d';
        document.querySelectorAll('.routine-action-checkbox').forEach((cb) => { cb.checked = false; });
        document.getElementById('routineFormError').classList.add('hidden');
        this.renderSenderChips();
        this.updateLabelPickerVisibility();
        this.populateLabelSelect();

        document.getElementById('routinesListSection').classList.add('hidden');
        document.getElementById('routinesFormSection').classList.remove('hidden');
    },

    closeForm() {
        document.getElementById('routinesFormSection').classList.add('hidden');
        document.getElementById('routinesListSection').classList.remove('hidden');
    },

    async populateLabelSelect() {
        const select = document.getElementById('routineLabelSelect');
        if (!select) return;
        let labels = GmailCleaner.Labels.labels.user;
        if (!labels || labels.length === 0) {
            const loaded = await GmailCleaner.Labels.loadLabels();
            labels = loaded ? loaded.user_labels : [];
        }
        select.innerHTML = (labels || [])
            .map((l) => `<option value="${l.id}">${GmailCleaner.UI.escapeHtml(l.name)}</option>`)
            .join('');
    },

    updateLabelPickerVisibility() {
        const labelChecked = document.querySelector('.routine-action-checkbox[value="label"]').checked;
        document.getElementById('routineLabelPickerRow').classList.toggle('hidden', !labelChecked);
    },

    addSenderFromInput() {
        const input = document.getElementById('routineSenderInput');
        const value = input.value.trim();
        if (!value) return;
        if (!this.senders.includes(value)) {
            this.senders.push(value);
            this.renderSenderChips();
        }
        input.value = '';
    },

    removeSender(sender) {
        this.senders = this.senders.filter((s) => s !== sender);
        this.renderSenderChips();
    },

    renderSenderChips() {
        const container = document.getElementById('routineSenderChips');
        container.innerHTML = '';
        this.senders.forEach((sender) => {
            const chip = document.createElement('span');
            chip.className = 'chip';
            chip.textContent = sender + ' ';
            const remove = document.createElement('i');
            remove.className = 'ti ti-x chip-remove';
            remove.addEventListener('click', () => this.removeSender(sender));
            chip.appendChild(remove);
            container.appendChild(chip);
        });
    },

    showFormError(message) {
        const el = document.getElementById('routineFormError');
        el.textContent = message;
        el.classList.remove('hidden');
    },

    async save() {
        const name = document.getElementById('routineNameInput').value.trim();
        const olderThan = document.getElementById('routineOlderThanSelect').value;
        const actions = Array.from(document.querySelectorAll('.routine-action-checkbox:checked')).map((cb) => cb.value);
        const labelId = document.getElementById('routineLabelSelect').value;

        if (!name) return this.showFormError('Name is required');
        if (this.senders.length === 0) return this.showFormError('Add at least one sender');
        if (actions.length === 0) return this.showFormError('Select at least one action');
        if (actions.includes('label') && !labelId) return this.showFormError('Choose a label to apply');

        try {
            const response = await fetch('/api/routines', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    senders: this.senders,
                    older_than: olderThan,
                    actions,
                    label_id: actions.includes('label') ? labelId : null,
                }),
            });
            if (!response.ok) {
                const error = await response.json();
                return this.showFormError(error.detail || 'Failed to save routine');
            }
            this.closeForm();
            GmailCleaner.UI.showSuccessToast(`Routine "${name}" saved`);
            this.loadRoutines();
        } catch (error) {
            this.showFormError('Failed to save routine: ' + error.message);
        }
    },

    async deleteRoutine(routineId) {
        try {
            const response = await fetch(`/api/routines/${routineId}`, { method: 'DELETE' });
            const result = await response.json();
            if (result.success) {
                GmailCleaner.UI.showSuccessToast('Routine deleted');
            } else {
                GmailCleaner.UI.showErrorToast(result.message || 'Failed to delete routine');
            }
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Failed to delete routine: ' + error.message);
        }
        this.loadRoutines();
    },

    async openConfirm(routineId) {
        this.pendingRunRoutineId = routineId;
        try {
            const response = await fetch(`/api/routines/${routineId}/preview`, { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to preview routine');
            }
            const preview = await response.json();
            this.renderConfirm(preview);
            document.getElementById('routineConfirmOverlay').classList.remove('hidden');
        } catch (error) {
            GmailCleaner.UI.showErrorToast(error.message);
        }
    },

    renderConfirm(preview) {
        const actionLabels = preview.actions.map((a) => this.ACTION_LABELS[a] || a).join(', ');
        document.getElementById('routineConfirmTitle').textContent = `Run "${preview.name}"?`;
        document.getElementById('routineConfirmSummary').textContent =
            `This will apply "${actionLabels}" to ${preview.total} email${preview.total === 1 ? '' : 's'}:`;

        const perSenderEl = document.getElementById('routineConfirmPerSender');
        perSenderEl.innerHTML = '';
        preview.per_sender.forEach((entry) => {
            const row = document.createElement('div');
            row.style.display = 'flex';
            row.style.justifyContent = 'space-between';
            const sender = document.createElement('span');
            sender.textContent = entry.sender;
            const count = document.createElement('span');
            count.textContent = entry.count;
            row.appendChild(sender);
            row.appendChild(count);
            perSenderEl.appendChild(row);
        });

        const runBtn = document.getElementById('routineConfirmRunBtn');
        runBtn.disabled = preview.total === 0;
        runBtn.classList.toggle('hidden', preview.total === 0);
    },

    closeConfirm() {
        document.getElementById('routineConfirmOverlay').classList.add('hidden');
        this.pendingRunRoutineId = null;
    },

    async confirmRun() {
        const routineId = this.pendingRunRoutineId;
        if (!routineId) return;
        this.closeConfirm();

        try {
            await fetch(`/api/routines/${routineId}/run`, { method: 'POST' });
            await this.pollRun();
            const status = await (await fetch('/api/routines/run-status')).json();
            if (status.error) {
                GmailCleaner.UI.showErrorToast(status.error);
            } else {
                GmailCleaner.UI.showSuccessToast(status.message || 'Routine finished');
            }
        } catch (error) {
            GmailCleaner.UI.showErrorToast('Error running routine: ' + error.message);
        }
        this.loadRoutines();
    },

    async pollRun(attempts = 0) {
        const response = await fetch('/api/routines/run-status');
        const status = await response.json();
        if (status.done) return;
        // Generous 30-minute sanity ceiling, same reasoning as
        // archive.js's pollArchive - quota pacing (Phase 4a2) means a large
        // run can legitimately take several minutes.
        if (attempts > 6000) throw new Error('Routine run timed out');
        await new Promise((resolve) => setTimeout(resolve, 300));
        return this.pollRun(attempts + 1);
    },
};

document.addEventListener('DOMContentLoaded', () => {
    GmailCleaner.Routines.init();
});
