/**
 * Gmail Cleanup - Authentication Module
 */

window.GmailCleaner = window.GmailCleaner || {};

GmailCleaner.Auth = {
    async checkStatus() {
        try {
            const response = await fetch('/api/auth-status');
            const status = await response.json();
            this.updateUI(status);
        } catch (error) {
            console.error('Error checking auth status:', error);
            GmailCleaner.UI.showView('login');
        }
    },

    updateUI(authStatus) {
        const userSection = document.getElementById('userSection');

        if (authStatus.logged_in && authStatus.email) {
            const safeEmail = GmailCleaner.UI.escapeHtml(authStatus.email);
            userSection.innerHTML = `
                <span class="account-email"><i class="ti ti-user-circle"></i>${safeEmail}</span>
                <button class="btn" id="signOutBtn">Sign out</button>
            `;
            document.getElementById('signOutBtn')?.addEventListener('click', () => this.signOut());

            GmailCleaner.UI.showView('delete');
            this.loadLabelsForFilters();
        } else {
            userSection.innerHTML = '';
            // Reset the sign-in button before showing the login view - it may
            // still be stuck disabled/"Signing in..." from a previous sign-in
            // click, since a *successful* sign-in never resets it (it just
            // hides behind the logged-in view instead).
            this.resetSignInButton();
            GmailCleaner.UI.showView('login');
        }
    },

    async loadLabelsForFilters() {
        try {
            const labels = await GmailCleaner.Labels.loadLabels();
            if (labels && labels.user_labels) {
                Object.values(GmailCleaner.SenderList.views).forEach(view => {
                    view.populateLabels(labels.user_labels);
                });
            }
        } catch (error) {
            console.error('Error loading labels for filters:', error);
        }
    },

    async signIn() {
        const signInBtn = document.getElementById('signInBtn');

        if (signInBtn) {
            signInBtn.disabled = true;
            signInBtn.innerHTML = '<span>Signing in...</span>';
        }
        this.setStatus('Starting sign-in...');

        try {
            const statusResp = await fetch('/api/web-auth-status');
            const status = await statusResp.json();

            if (!status.has_credentials) {
                this.resetSignInButton();
                alert('credentials.json not found!\n\nSetup instructions:\n1. Go to https://console.cloud.google.com/\n2. Create project → Enable Gmail API\n3. Create OAuth credentials (Desktop app)\n4. Download JSON → rename to credentials.json\n5. Put credentials.json in the app folder\n6. Restart the app');
                return;
            }

            if (status.web_auth_mode) {
                const msg = `Docker detected! To sign in:

1. Check Docker logs for the authorization URL:
   docker logs cleanup_email-gmail-cleaner-1

2. Copy the URL and open it in your browser

3. After authorizing, you'll be signed in automatically.

(Or generate token.json locally and mount it)`;
                alert(msg);
            }

            const signInResp = await fetch('/api/sign-in', { method: 'POST' });
            const signInResult = await signInResp.json();

            if (signInResult.error) {
                // e.g. "a previous attempt is still pending, ~Ns remaining" -
                // surfaced as a toast (not a blocking alert) since the user
                // hasn't done anything wrong here, they just need to wait a
                // moment and try again.
                this.resetSignInButton();
                GmailCleaner.UI.showErrorToast(signInResult.error);
                return;
            }

            this.setStatus('A browser tab should have opened for Google sign-in. Complete it there - this page will update automatically.');
            this.pollStatus();
        } catch (error) {
            this.clearStatus();
            GmailCleaner.UI.showErrorToast('Error signing in: ' + error.message);
            this.resetSignInButton();
        }
    },

    async pollStatus(attempts = 0) {
        const maxAttempts = 120;
        const signInBtn = document.getElementById('signInBtn');

        try {
            const response = await fetch('/api/auth-status');
            const status = await response.json();

            if (status.logged_in) {
                this.clearStatus();
                this.updateUI(status);
            } else if (attempts < maxAttempts) {
                if (signInBtn) {
                    // Count down to when polling gives up, not up from zero -
                    // an increasing number here reads as "still stuck", not
                    // "still waiting, N seconds left before we give up".
                    const remaining = maxAttempts - attempts;
                    signInBtn.innerHTML = `<span>Signing in... (${remaining}s)</span>`;
                }
                if (attempts === 10) {
                    this.setStatus("Still waiting - check for a Google sign-in tab that may have opened (it can be hidden behind this window, or blocked as a pop-up).");
                }
                setTimeout(() => this.pollStatus(attempts + 1), 1000);
            } else {
                this.resetSignInButton();
                GmailCleaner.UI.showErrorToast('Sign-in timed out after 2 minutes. If you closed the browser tab, wait a moment and try again.');
            }
        } catch (error) {
            console.error('Error polling auth status:', error);
            setTimeout(() => this.pollStatus(attempts + 1), 1000);
        }
    },

    resetSignInButton() {
        const signInBtn = document.getElementById('signInBtn');
        if (signInBtn) {
            signInBtn.disabled = false;
            signInBtn.innerHTML = '<i class="ti ti-brand-google"></i>Sign in with Google';
        }
        this.clearStatus();
    },

    setStatus(message) {
        const statusEl = document.getElementById('signInStatus');
        if (statusEl) {
            statusEl.textContent = message;
            statusEl.classList.remove('hidden');
        }
    },

    clearStatus() {
        const statusEl = document.getElementById('signInStatus');
        if (statusEl) {
            statusEl.textContent = '';
            statusEl.classList.add('hidden');
        }
    },

    async signOut() {
        if (!confirm('Sign out of your Gmail account?')) return;

        try {
            await fetch('/api/sign-out', { method: 'POST' });
            Object.values(GmailCleaner.SenderList.views).forEach(view => view.reset());
            this.checkStatus();
        } catch (error) {
            alert('Error signing out: ' + error.message);
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('signInBtn')?.addEventListener('click', () => GmailCleaner.Auth.signIn());
});
