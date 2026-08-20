/**
 * Eastern Time formatting helper.
 *
 * All DB timestamps arrive as UTC strings like '2026-06-30 11:30:00'.
 * We convert to Eastern (EDT/EST) in the browser for consistent display.
 */

const EASTERN = Intl.DateTimeFormat().resolvedOptions().timeZone === 'US/Eastern'
  ? undefined
  : new Intl.DateTimeFormat(undefined, {
      timeZone: 'America/New_York',
    });

/**
 * Format a DB UTC timestamp string to Eastern display string.
 * Input: "2026-06-30 11:30:00" or ISO format (with or without +00:00)
 * Output: "6/30/2026, 7:30:00 AM" (local-browser style but in ET)
 */
export function formatEastern(ts) {
  if (!ts || ts === 'None') return '-';
  try {
    let iso = String(ts);
    // Only add +00:00 if not already present
    if (!iso.includes('+') && !iso.endsWith('Z')) {
      iso = iso.replace(' ', 'T') + '+00:00';
    } else {
      iso = iso.replace(' ', 'T');
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: '2-digit', second: '2-digit',
      timeZone: 'America/New_York',
    });
  } catch {
    return '-';
  }
}

/**
 * Format for short display (MM/DD h:SS AM/PM) — used in tables.
 * Input may be raw UTC or pre-converted EDT from the backend.
 */
export function formatEasternShort(ts) {
  if (!ts || ts === 'None') return '-';
  try {
    let s = String(ts).trim();
    // Already in EDT format (has AM/PM) — just format cleanly
    if (/\d{1,2}:\d{2}:\d{2}\s*[AP]M/i.test(s)) {
      return s;
    }
    let iso = s;
    // Only add +00:00 if not already present
    if (!iso.includes('+') && !iso.endsWith('Z')) {
      iso = iso.replace(' ', 'T') + '+00:00';
    } else {
      iso = iso.replace(' ', 'T');
    }
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleString(undefined, {
      month: '2-digit', day: '2-digit', year: 'numeric',
      hour: 'numeric', minute: '2-digit', second: '2-digit',
      timeZone: 'America/New_York',
    });
  } catch {
    return '-';
  }
}
