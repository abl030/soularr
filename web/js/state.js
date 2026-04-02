// @ts-check

/**
 * Shared application state and toast notification.
 * All modules import state from here instead of using bare globals.
 */

/** @type {{ browseSearchType: string, browseArtist: {id:string, name:string}|null, browseSubView: string, browseCache: Object, pipelineData: Object|null, pipelineFilter: string, recentsCounts: {all:number, imported:number, rejected:number}, recentsFilter: string, dsConstants: Object|null, dsLoaded: boolean, disambData: Object|null, searchTimer: number|null }} */
export const state = {
  browseSearchType: 'artist',
  browseArtist: null,
  browseSubView: 'discography',
  browseCache: {},
  pipelineData: null,
  pipelineFilter: 'wanted',
  recentsCounts: { all: 0, imported: 0, rejected: 0 },
  recentsFilter: 'all',
  dsConstants: null,
  dsLoaded: false,
  disambData: null,
  searchTimer: null,
};

export const API = '';

/**
 * Show a toast notification.
 * @param {string} msg
 * @param {boolean} [isError]
 */
export function toast(msg, isError) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'toast' + (isError ? ' error' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}
