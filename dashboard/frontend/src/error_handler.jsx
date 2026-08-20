// Global error handler — writes console errors to /tmp/dashboard_error_log.json
window.__dashboardErrors = [];
const origConsoleError = console.error;
console.error = function(...args) {
  window.__dashboardErrors.push({ time: new Date().toISOString(), args: args.map(String) });
  origConsoleError.apply(console, args);
};
window.onerror = function(msg, url, line, col, err) {
  window.__dashboardErrors.push({ type: 'unhandled', msg, url, line, col, err: err?.stack });
};

// Export a helper to read errors via API (for debugging)
export function getDashboardErrors() { return [...window.__dashboardErrors]; }
