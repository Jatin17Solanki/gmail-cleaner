/**
 * Gmail Cleanup - Main Entry Point
 * Initializes the application and loads all modules
 */

// Global state
window.GmailCleaner = window.GmailCleaner || {};
Object.assign(GmailCleaner, {
    currentView: 'login'
});

document.addEventListener('DOMContentLoaded', () => {
    GmailCleaner.UI.setupNavigation();
    GmailCleaner.Auth.checkStatus();
});
