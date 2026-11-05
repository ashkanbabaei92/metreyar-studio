#!/usr/bin/env node
/**
 * S3.2 JS Parity Test (docs/search_logs/s32_js_parity.js)
 * - Extracts group checkbox values dynamically from HTML via regex (no hardcoded group names).
 * - Verifies #selftest and #selftest_rows are OK.
 * - Compares JS window.__rowsSearch and window.__pagesSearch against docs/search_logs/s32_expected.json
 *   for both normal (wholeWord=false) and whole_word (wholeWord=true) across ALL groups and each single group.
 * - Verifies noSpace=true disables wholeWordCb and ignores wholeWord in search.
 * - Verifies "Show More" (نمایش بیشتر) simulated click for rows (100 -> 200, total constant, stats text "نمایش X از Y")
 *   and pages (200 -> 300, total constant, stats text "نمایش X از Y").
 * - Ends with PARITY: OK or PARITY: FAIL n.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

try {
  const rootDir = path.resolve(__dirname, '..', '..');
  const htmlPath = path.join(rootDir, 'output', 'search', 'metreyar_search.html');
  const expectedPath = path.join(rootDir, 'docs', 'search_logs', 's32_expected.json');

  const html = fs.readFileSync(htmlPath, 'utf8');
  const expected = JSON.parse(fs.readFileSync(expectedPath, 'utf8'));

  // Extract group checkbox values from HTML via regex
  const cbRegex = /<input[^>]*class="grp-cb"[^>]*value="([^"]+)"/g;
  const htmlGroups = [];
  let mCb;
  while ((mCb = cbRegex.exec(html)) !== null) {
    htmlGroups.push(mCb[1]);
  }

  console.log('=== S3.2 JS Parity Test (s32_parity.txt) ===');
  console.log('Extracted checkbox groups from HTML via regex:', JSON.stringify(htmlGroups));

  const scriptRegex = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  const scripts = [];
  let mScript;
  while ((mScript = scriptRegex.exec(html)) !== null) {
    scripts.push(mScript[1]);
  }
  console.log(`Found ${scripts.length} <script> blocks in HTML.`);

  const elementsById = {};
  function makeEl(id) {
    const listeners = {};
    return {
      id: id,
      value: '',
      checked: false,
      disabled: false,
      textContent: '',
      innerHTML: '',
      style: {},
      children: [],
      parentNode: {
        insertBefore: function() {},
        removeChild: function() {}
      },
      addEventListener: function(ev, fn) {
        if (!listeners[ev]) listeners[ev] = [];
        listeners[ev].push(fn);
      },
      trigger: function(ev, arg) {
        const arr = listeners[ev] || [];
        for (let i = 0; i < arr.length; i++) {
          arr[i](arg || {});
        }
      },
      appendChild: function(c) { this.children.push(c); }
    };
  }

  const knownIds = [
    'qInput', 'noSpaceCb', 'wholeWordCb', 'basePath', 'statsBar', 'resultsList',
    'pagesShowMoreWrap', 'pagesShowMoreBtn',
    'builtDate', 'docsTableBody', 'selftest',
    'rowsSection', 'rowsOuterContainer', 'selftest_rows',
    'rowsStatsBar', 'rowsResultsList', 'rowsShowMoreWrap', 'rowsShowMoreBtn'
  ];
  for (const id of knownIds) {
    elementsById[id] = makeEl(id);
  }

  const grpCheckboxes = htmlGroups.map(val => {
    const el = makeEl('grp_' + val);
    el.value = val;
    el.checked = true;
    return el;
  });

  const sandbox = {
    window: {},
    document: {
      getElementById: function(id) {
        if (!elementsById[id]) elementsById[id] = makeEl(id);
        return elementsById[id];
      },
      querySelectorAll: function(sel) {
        if (sel === '.grp-cb') return grpCheckboxes;
        return [];
      },
      createElement: function(tag) {
        return makeEl(tag);
      }
    },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  for (let i = 0; i < scripts.length; i++) {
    vm.runInContext(scripts[i], sandbox, { filename: `script_${i}.js`, timeout: 60000 });
  }

  let failCount = 0;
  const diffs = [];

  const stText = elementsById['selftest'] ? elementsById['selftest'].textContent : '';
  const stRowsText = elementsById['selftest_rows'] ? elementsById['selftest_rows'].textContent : '';
  console.log('DOM #selftest:', stText);
  console.log('DOM #selftest_rows:', stRowsText);

  if (!stText.includes('OK') || stText.includes('FAIL')) {
    console.log('FAIL: #selftest is not OK:', stText);
    failCount++;
  }
  if (!stRowsText.includes('OK') || stRowsText.includes('FAIL')) {
    console.log('FAIL: #selftest_rows is not OK:', stRowsText);
    failCount++;
  }

  function compareMap(a, b) {
    const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
    for (const k of keys) {
      const va = (a && a[k]) || 0;
      const vb = (b && b[k]) || 0;
      if (va !== vb) return false;
    }
    return true;
  }

  const queries = expected.queries || [];
  const groupKeys = ['ALL', ...htmlGroups];
  let checkedCases = 0;

  for (const q of queries) {
    const qCases = expected.cases[q];
    if (!qCases) {
      failCount++;
      diffs.push(`MISSING_QUERY_IN_EXPECTED: ${q}`);
      continue;
    }
    for (const gKey of groupKeys) {
      const gArr = gKey === 'ALL' ? htmlGroups : [gKey];
      const expCase = qCases[gKey];
      if (!expCase) {
        failCount++;
        diffs.push(`MISSING_GROUP_IN_EXPECTED: q=${q} grp=${gKey}`);
        continue;
      }

      for (const mode of ['normal', 'whole_word']) {
        const wwFlag = (mode === 'whole_word');
        const expR = expCase[mode].rows;
        const expP = expCase[mode].pages;

        const jsR = sandbox.window.__rowsSearch(q, false, gArr, wwFlag);
        const jsP = sandbox.window.__pagesSearch(q, false, gArr, wwFlag);
        checkedCases++;

        if (
          jsR.total !== expR.total ||
          !compareMap(jsR.by_group, expR.by_group) ||
          JSON.stringify(jsR.top_codes) !== JSON.stringify(expR.top_codes)
        ) {
          failCount++;
          diffs.push(`ROWS_PARITY_FAIL q=${q} grp=${gKey} mode=${mode}: js=${JSON.stringify(jsR)} exp=${JSON.stringify(expR)}`);
        }

        if (
          jsP.total !== expP.total ||
          !compareMap(jsP.by_group, expP.by_group) ||
          JSON.stringify(jsP.top_keys) !== JSON.stringify(expP.top_keys)
        ) {
          failCount++;
          diffs.push(`PAGES_PARITY_FAIL q=${q} grp=${gKey} mode=${mode}: js_tot=${jsP.total} exp_tot=${expP.total}`);
        }
      }
    }
  }
  console.log(`Checked ${checkedCases} query×group×mode cases for rows and pages.`);

  // Verify noSpace=true disables wholeWordCb and ignores wholeWord
  const jsR_nosp_wwFalse = sandbox.window.__rowsSearch('بتن', true, htmlGroups, false);
  const jsR_nosp_wwTrue = sandbox.window.__rowsSearch('بتن', true, htmlGroups, true);
  if (JSON.stringify(jsR_nosp_wwFalse) !== JSON.stringify(jsR_nosp_wwTrue)) {
    failCount++;
    diffs.push('NOSPACE_WHOLEWORD_IGNORE_FAIL on rowsSearch');
  }

  // Test UI Show More (نمایش بیشتر) for Rows and Pages on query 'بتن' (all groups, normal mode)
  for (const cb of grpCheckboxes) cb.checked = true;
  elementsById['noSpaceCb'].checked = false;
  elementsById['wholeWordCb'].checked = false;
  elementsById['qInput'].value = 'بتن';
  elementsById['qInput'].trigger('keydown', { key: 'Enter' });

  const rState1 = sandbox.window.__getRowsState();
  const rStats1 = elementsById['rowsStatsBar'].innerHTML;
  const rCards1 = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
  console.log(`Rows ShowMore before click: shown=${rState1.shown}, total=${rState1.total}, domCards=${rCards1}`);

  const rBtn = elementsById['rowsShowMoreBtn'];
  rBtn.trigger('click');

  const rState2 = sandbox.window.__getRowsState();
  const rStats2 = elementsById['rowsStatsBar'].innerHTML;
  const rCards2 = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
  console.log(`Rows ShowMore after 1st click: shown=${rState2.shown}, total=${rState2.total}, domCards=${rCards2}`);

  if (
    rState1.shown !== 100 ||
    rCards1 !== 100 ||
    rState2.shown !== 200 ||
    rCards2 !== 200 ||
    rState1.total !== rState2.total ||
    !rStats1.includes(`نمایش 100 از ${rState1.total}`) ||
    !rStats2.includes(`نمایش 200 از ${rState2.total}`)
  ) {
    failCount++;
    diffs.push(`ROWS_SHOW_MORE_FAIL: state1=${JSON.stringify(rState1)} state2=${JSON.stringify(rState2)}`);
  }

  // Test Pages Show More on 'بتن'
  const pState1 = sandbox.window.__getPagesState();
  const pStats1 = elementsById['statsBar'].innerHTML;
  const pCards1 = (elementsById['resultsList'].innerHTML.match(/class="card"/g) || []).length;
  console.log(`Pages ShowMore before click: shown=${pState1.shown}, total=${pState1.total}, domCards=${pCards1}`);

  const pBtn = elementsById['pagesShowMoreBtn'];
  pBtn.trigger('click');

  const pState2 = sandbox.window.__getPagesState();
  const pStats2 = elementsById['statsBar'].innerHTML;
  const pCards2 = (elementsById['resultsList'].innerHTML.match(/class="card"/g) || []).length;
  console.log(`Pages ShowMore after 1st click: shown=${pState2.shown}, total=${pState2.total}, domCards=${pCards2}`);

  if (
    pState1.shown !== 200 ||
    pCards1 !== 200 ||
    pState2.shown !== 300 ||
    pCards2 !== 300 ||
    pState1.total !== pState2.total ||
    !pStats1.includes(`نمایش 200 از ${pState1.total}`) ||
    !pStats2.includes(`نمایش 300 از ${pState2.total}`)
  ) {
    failCount++;
    diffs.push(`PAGES_SHOW_MORE_FAIL: state1=${JSON.stringify(pState1)} state2=${JSON.stringify(pState2)}`);
  }

  // Test reset on new search and disabled state when noSpaceCb checked
  elementsById['noSpaceCb'].checked = true;
  elementsById['noSpaceCb'].trigger('change');
  const rStateReset = sandbox.window.__getRowsState();
  const pStateReset = sandbox.window.__getPagesState();
  const wwDisabled = elementsById['wholeWordCb'].disabled;
  console.log(`After new search (noSpace=true): rowsShown=${rStateReset.shown}, pagesShown=${pStateReset.shown}, wholeWordCb.disabled=${wwDisabled}`);
  if (rStateReset.shown !== 100 || pStateReset.shown !== 200 || wwDisabled !== true) {
    failCount++;
    diffs.push(`RESET_OR_DISABLED_FAIL: rShown=${rStateReset.shown} pShown=${pStateReset.shown} wwDisabled=${wwDisabled}`);
  }

  if (failCount === 0) {
    console.log('PARITY: OK');
    process.exit(0);
  } else {
    const diffPath = path.join(rootDir, 'docs', 'search_logs', 's32_diff.txt');
    fs.writeFileSync(diffPath, diffs.slice(0, 10).join('\n') + '\n', 'utf8');
    console.log(`PARITY: FAIL ${failCount}`);
    process.exit(1);
  }
} catch (err) {
  console.error('FATAL ERROR in s32_js_parity.js:', err);
  console.log('PARITY: FAIL 1');
  process.exit(1);
}
