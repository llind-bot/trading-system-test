import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// Global error catcher — shows visible overlay on crash
window.__dashboard_errors = [];
const origError = console.error;
console.error = function(...args) {
  window.__dashboard_errors.push(args);
  origError.apply(console, args);
};
window.addEventListener('error', (e) => {
  window.__dashboard_errors.push({ msg: e.message, filename: e.filename, lineno: e.lineno });
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
