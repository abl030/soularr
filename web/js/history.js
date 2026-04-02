// @ts-check
import { awstDateTime, esc } from './util.js';

/**
 * Render a single download history item.
 * @param {Object} h - Download history entry from the API
 * @returns {string} HTML string
 */
export function renderDownloadHistoryItem(h) {
  const outcome = h.outcome || '?';
  const color = outcome === 'success' ? '#6d6' : outcome === 'rejected' ? '#d88'
    : outcome === 'force_import' ? '#6af' : '#aa8';
  const user = h.soulseek_username || '?';
  const date = awstDateTime(h.created_at || '');

  let html = `<div class="p-hist-header">
    <span style="color:${color};">${outcome === 'force_import' ? 'force imported' : outcome}</span>
    <span style="color:#888;">${esc(user)}</span>
    <span style="color:#555;">${date}</span>
  </div>`;

  const rows = [];

  if (h.downloaded_label) {
    rows.push(['Downloaded', h.downloaded_label]);
  }

  if (h.spectral_grade) {
    const sgColor = h.spectral_grade === 'genuine' ? '#6d6' : h.spectral_grade === 'suspect' ? '#d66' : '#aa8';
    let sgLabel = h.spectral_grade;
    if (h.spectral_bitrate && h.spectral_grade !== 'genuine') {
      sgLabel += ` (~${h.spectral_bitrate}kbps)`;
    }
    rows.push(['Spectral', `<span style="color:${sgColor};">${sgLabel}</span>`]);
  }

  const existBr = h.existing_min_bitrate || h.existing_spectral_bitrate;
  if (existBr) {
    const existLabel = h.existing_spectral_bitrate
      ? `~${h.existing_spectral_bitrate}kbps (spectral)`
      : `${h.existing_min_bitrate}kbps`;
    rows.push(['On disk (before)', existLabel]);
  }

  if (h.beets_distance != null) {
    rows.push(['Distance', parseFloat(h.beets_distance).toFixed(3)]);
  }

  for (const [label, value] of rows) {
    html += `<div class="p-hist-row"><span class="p-hist-label">${label}</span> ${value}</div>`;
  }

  const verdict = h.verdict || h.beets_scenario || '';
  if (verdict) {
    html += `<div class="p-hist-verdict">${esc(verdict)}</div>`;
  }

  return `<div class="p-hist-item">${html}</div>`;
}
