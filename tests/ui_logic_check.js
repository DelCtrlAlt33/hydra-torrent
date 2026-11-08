// Logic checks for the settings page's script (static/index.html): saving and
// clearing connection fields, showing the Plex/Jellyfin folders only when they
// apply, the same-drive folder suggestion, auto-fill of library folders, and the
// maximize/restore title-bar icon. It pulls the real functions out of the page
// and runs them against a tiny fake page, so it needs only Node, no browser.
//
//   node tests/ui_logic_check.js
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');
const script = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/)[1];
function slice(startMarker, endMarker) {
  const a = script.indexOf(startMarker), b = script.indexOf(endMarker, a);
  if (a < 0 || b < 0) throw new Error('marker not found: ' + startMarker + ' / ' + endMarker);
  return script.slice(a, b);
}
const saveSrc = slice('let _settingsLoaded = false;', 'async function loadSettings()')
              + slice('async function saveSettings(silent = false) {', 'async function browseDownloadDir()');
const autoSrc = slice('const _autoFillTried = {};', '// Debounced live check');
const uiSrc   = slice('// ── Title bar: the maximize button', '// True once the saved settings have been copied');
const loadSrc = slice('async function loadSettings() {', 'function toggleAcc(');

let pass = 0, fail = 0;
function check(name, cond) { if (cond) { pass++; console.log('  ok   ' + name); } else { fail++; console.log('  FAIL ' + name); } }

// A tiny fake page: elements with value / classList / style / textContent / innerHTML / title.
function fakeDoc(initial = {}) {
  const els = {};
  const get = id => els[id] || (els[id] = {
    value: initial[id] ?? '', checked: false, textContent: '', innerHTML: '', title: '', style: {},
    _classes: new Set(),
    classList: { toggle(c, on) { on ? get(id)._classes.add(c) : get(id)._classes.delete(c); }, contains(c) { return get(id)._classes.has(c); } },
  });
  return { els, doc: { getElementById: get, querySelector: sel => (sel === '.titlebar-btn.maximize' ? get('__maxbtn') : null) } };
}

(async () => {
  // ---- saveSettings ---------------------------------------------------------
  console.log('saveSettings');
  function makeSave(values) {
    const { doc } = fakeDoc(values);
    let sent = null;
    const factory = new Function('document', 'fetch', 'BASE', 'HEADERS', 'toast', 'updateAccordionSummaries', 'loadHealth', 'updateLibraryFolders',
      saveSrc + '; return { saveSettings, setLoaded: v => { _settingsLoaded = v; } };');
    const api = factory(doc, async (u, o) => { sent = JSON.parse(o.body); return { ok: true, json: async () => ({ saved: Object.keys(sent) }) }; },
                        '', {}, () => {}, () => {}, () => {}, () => {});
    return { api, sent: () => sent };
  }
  let t = makeSave({ 'cfg-download-dir': 'C:\\dl', 'cfg-plex-url': '', 'cfg-plex-token': '', 'cfg-jackett-key': '', 'cfg-indexer-url': '' });
  await t.api.saveSettings(true);
  check('before settings have loaded, empty boxes are NOT sent (cannot wipe saved values)',
        !('plex_url' in t.sent()) && !('jackett_api_key' in t.sent()) && t.sent().download_dir_incomplete === 'C:\\dl');
  t = makeSave({ 'cfg-download-dir': 'C:\\dl', 'cfg-plex-url': '', 'cfg-plex-token': '', 'cfg-jackett-key': '', 'cfg-indexer-url': '',
                 'cfg-jellyfin-url': '', 'cfg-jellyfin-key': '', 'cfg-movies-path': '', 'cfg-tv-path': '', 'cfg-download-dir-other': '' });
  t.api.setLoaded(true);
  await t.api.saveSettings(true);
  const s = t.sent();
  check('after load, a cleared Plex URL/token IS sent as empty (so health says Not configured)', s.plex_url === '' && s.plex_token === '');
  check('after load, a cleared Jackett URL/key IS sent as empty', s.indexing_server === '' && s.jackett_api_key === '');
  check('after load, cleared Jellyfin and Movies/TV folders are sent as empty',
        s.jellyfin_url === '' && s.jellyfin_api_key === '' && s.media_dir_movies === '' && s.media_dir_tv === '');
  check('a blank download folder is never sent (cannot be cleared)', !('download_dir_incomplete' in s) || s.download_dir_incomplete !== '');
  check('the hidden "other downloads" value is not wiped when blank', !('download_dir_other' in s));
  t = makeSave({ 'cfg-download-dir': 'C:\\dl', 'cfg-plex-url': 'http://p:32400', 'cfg-plex-token': 'tok', 'cfg-movies-path': 'Z:\\movies', 'cfg-download-dir-other': 'D:\\old' });
  t.api.setLoaded(true);
  await t.api.saveSettings(true);
  check('filled values are sent unchanged',
        t.sent().plex_url === 'http://p:32400' && t.sent().plex_token === 'tok' && t.sent().media_dir_movies === 'Z:\\movies' && t.sent().download_dir_other === 'D:\\old');

  // ---- updateLibraryFolders -------------------------------------------------
  console.log('updateLibraryFolders (Plex folders only when they apply)');
  function makeLib(values) {
    const { els, doc } = fakeDoc(values);
    const f = new Function('document', uiSrc + '; return { updateLibraryFolders, _HINT_PLAIN, _HINT_PLEX };')(doc);
    return { els, ...f };
  }
  let L = makeLib({});
  L.updateLibraryFolders();
  check('nothing configured: library folders hidden, link shown, plain hint',
        !L.els['library-folders'].classList.contains('show') && L.els['library-link-row'].style.display === '' && L.els['download-dir-hint'].textContent === L._HINT_PLAIN);
  L = makeLib({ 'cfg-plex-url': 'http://p:32400' });
  L.updateLibraryFolders();
  check('Plex set up: library folders shown, link hidden, hint explains the working folder',
        L.els['library-folders'].classList.contains('show') && L.els['library-link-row'].style.display === 'none' && L.els['download-dir-hint'].textContent === L._HINT_PLEX);
  L = makeLib({ 'cfg-jellyfin-url': 'http://j:8096' }); L.updateLibraryFolders();
  check('Jellyfin set up: shown', L.els['library-folders'].classList.contains('show'));
  L = makeLib({ 'cfg-movies-path': 'Z:\\movies' }); L.updateLibraryFolders();
  check('a Movies folder already set: shown', L.els['library-folders'].classList.contains('show'));
  L = makeLib({}); L.updateLibraryFolders(true);
  check('clicking the link shows them', L.els['library-folders'].classList.contains('show') && L.els['library-link-row'].style.display === 'none');
  L = makeLib({ 'cfg-plex-url': 'http://p:32400' }); L.updateLibraryFolders();
  L.els['cfg-plex-url'].value = '';
  L.updateLibraryFolders();
  check('once shown they stay shown even if Plex is cleared (no flicker)', L.els['library-folders'].classList.contains('show'));

  // ---- _suggestDownloadFolder -----------------------------------------------
  console.log('_suggestDownloadFolder (same-drive incomplete folder)');
  function makeSuggest(values) {
    const { els, doc } = fakeDoc(values);
    const f = new Function('document', uiSrc + '; return { _suggestDownloadFolder };')(doc);
    return { els, ...f };
  }
  let S = makeSuggest({ 'cfg-movies-path': '/mnt/plex/movies', 'cfg-download-dir': '' });
  check('Linux: suggests a sibling "incomplete" folder', S._suggestDownloadFolder() === true && S.els['cfg-download-dir'].value === '/mnt/plex/incomplete');
  S = makeSuggest({ 'cfg-movies-path': 'Z:\\media\\movies', 'cfg-download-dir': '' });
  check('Windows path: uses backslashes', S._suggestDownloadFolder() === true && S.els['cfg-download-dir'].value === 'Z:\\media\\incomplete');
  S = makeSuggest({ 'cfg-movies-path': '/mnt/plex/movies/', 'cfg-download-dir': '' });
  S._suggestDownloadFolder();
  check('a trailing slash does not break it', S.els['cfg-download-dir'].value === '/mnt/plex/incomplete');
  S = makeSuggest({ 'cfg-movies-path': '/mnt/plex/movies', 'cfg-download-dir': 'C:\\Users\\me\\dl' });
  check('never overwrites a folder the user already chose', S._suggestDownloadFolder() === false && S.els['cfg-download-dir'].value === 'C:\\Users\\me\\dl');
  S = makeSuggest({ 'cfg-movies-path': '/movies', 'cfg-download-dir': '' });
  check('a library at the top level has no parent to sit beside: no suggestion', S._suggestDownloadFolder() === false && S.els['cfg-download-dir'].value === '');
  S = makeSuggest({ 'cfg-movies-path': '', 'cfg-download-dir': '' });
  check('no Movies folder: no suggestion', S._suggestDownloadFolder() === false);

  // ---- syncMaximizeIcon -----------------------------------------------------
  console.log('syncMaximizeIcon (restore icon while maximized)');
  function makeIcon(isMax, withApi = true) {
    const { els, doc } = fakeDoc({});
    const win = withApi ? { pywebview: { api: { is_maximized: async () => isMax.v } } } : {};
    const f = new Function('document', 'window', uiSrc + '; return { syncMaximizeIcon };')(doc, win);
    return { btn: () => els['__maxbtn'], ...f };
  }
  const st = { v: false };
  const I = makeIcon(st);
  await I.syncMaximizeIcon();
  check('normal window: maximize icon and "Maximize" tooltip', I.btn().title === 'Maximize' && I.btn().innerHTML.includes('<rect') && !I.btn().innerHTML.includes('<path'));
  st.v = true;
  await I.syncMaximizeIcon();
  check('maximized: swaps to the restore icon (two overlapping squares) and "Restore" tooltip', I.btn().title === 'Restore' && I.btn().innerHTML.includes('<path') && I.btn().innerHTML.includes('<rect'));
  st.v = false;
  await I.syncMaximizeIcon();
  check('restored again: swaps back', I.btn().title === 'Maximize' && !I.btn().innerHTML.includes('<path'));
  const nb = makeIcon({ v: true }, false);
  await nb.syncMaximizeIcon();
  const untouched = nb.btn(); check('in a plain browser tab (no desktop API): does nothing and does not throw', !untouched || (untouched.title === '' && untouched.innerHTML === ''));

  // ---- loadSettings checks the saved folders ----------------------------------
  console.log('loadSettings (warn about unreachable saved folders straight away)');
  async function runLoad(cfg) {
    const { els, doc } = fakeDoc({});
    const checked = [];
    const factory = new Function('document', 'fetch', 'BASE', 'HEADERS', 'toast', 'updateAccordionSummaries', 'updateLibraryFolders', '_checkLibraryPath',
      'let _settingsLoaded = false;' + loadSrc + '; return { loadSettings, loaded: () => _settingsLoaded };');
    const api = factory(doc, async () => ({ ok: true, json: async () => cfg }), '', {}, () => {}, () => {}, () => {}, (w) => checked.push(w));
    await api.loadSettings();
    return { els, checked, loaded: api.loaded() };
  }
  let ld = await runLoad({ media_dir_movies: '/data/movies', media_dir_tv: '/data/tv', download_dir_incomplete: 'C:\\dl' });
  check('saved Movies and TV folders are checked as soon as settings load', ld.checked.includes('movies') && ld.checked.includes('tv'));
  check('the boxes are filled from the saved settings', ld.els['cfg-movies-path'].value === '/data/movies' && ld.els['cfg-tv-path'].value === '/data/tv');
  check('settings are marked as loaded (so clearing a box can be saved)', ld.loaded === true);
  // ---- autoFillLibraries ----------------------------------------------------
  console.log('autoFillLibraries');
  function makeAuto({ movies = '', tv = '', libs, exists, loaded = true }) {
    const els = { 'cfg-movies-path': { value: movies }, 'cfg-tv-path': { value: tv } };
    const calls = { saved: 0, toasts: [], suggest: 0, shown: 0 };
    const factory = new Function('document', 'fetch', 'BASE', 'HEADERS', 'toast', 'saveSettings', '_checkLibraryPath', '_suggestDownloadFolder', 'updateLibraryFolders',
      'let _settingsLoaded = ' + loaded + ';' + autoSrc + '; return { autoFillLibraries, _maybeAutoFill, tried: _autoFillTried };');
    const fetchStub = async (url) => {
      if (url.includes('/libraries')) return { ok: true, json: async () => ({ libraries: libs }) };
      const p = decodeURIComponent(url.split('path=')[1]);
      return { ok: true, json: async () => ({ exists: !!exists[p] }) };
    };
    const api = factory({ getElementById: id => els[id] }, fetchStub, '', {}, (m) => calls.toasts.push(m),
                        () => { calls.saved++; }, () => {}, () => { calls.suggest++; }, () => { calls.shown++; });
    return { api, els, calls };
  }
  const libs = [{ type: 'movie', name: 'Movies', path: '/data/movies' }, { type: 'show', name: 'TV', path: '/data/tv' }];
  let a = makeAuto({ libs, exists: { '/data/movies': true, '/data/tv': false } });
  await a.api.autoFillLibraries('plex');
  check('fills the Movies box with a folder this PC can open', a.els['cfg-movies-path'].value === '/data/movies');
  check('leaves the TV box empty when that folder is not reachable (no scary warning)', a.els['cfg-tv-path'].value === '');
  check('saves, suggests the same-drive folder, reveals the section, tells the user once',
        a.calls.saved === 1 && a.calls.suggest === 1 && a.calls.shown === 1 && a.calls.toasts.length === 1);
  a = makeAuto({ libs, exists: {} });
  await a.api.autoFillLibraries('plex');
  check('Plex reporting only paths this PC cannot open: nothing filled, saved, suggested or shown',
        a.els['cfg-movies-path'].value === '' && a.els['cfg-tv-path'].value === '' && a.calls.saved === 0 && a.calls.suggest === 0 && a.calls.toasts.length === 0);
  a = makeAuto({ movies: 'Z:\\mine', libs, exists: { '/data/movies': true, '/data/tv': true } });
  await a.api.autoFillLibraries('plex');
  check('never overwrites a folder the user already set', a.els['cfg-movies-path'].value === 'Z:\\mine');
  check('still fills the other empty box', a.els['cfg-tv-path'].value === '/data/tv');
  a = makeAuto({ movies: 'Z:\\m', tv: 'Z:\\t', libs, exists: { '/data/movies': true } });
  await a.api.autoFillLibraries('plex');
  check('does nothing at all when both boxes are already filled', a.calls.saved === 0 && a.els['cfg-movies-path'].value === 'Z:\\m');
  a = makeAuto({ libs, exists: { '/data/movies': true }, loaded: false });
  await a.api.autoFillLibraries('plex');
  check('does nothing before the saved settings have loaded', a.els['cfg-movies-path'].value === '');
  a = makeAuto({ libs, exists: { '/data/movies': true } });
  await a.api.autoFillLibraries('plex'); await a.api.autoFillLibraries('plex');
  check('only tries once per connection (no repeated requests)', a.calls.saved === 1);
  a.api._maybeAutoFill({ plex: { reachable: false }, jellyfin: { reachable: false } });
  check('tries again after the service was disconnected', a.api.tried.plex === false);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();