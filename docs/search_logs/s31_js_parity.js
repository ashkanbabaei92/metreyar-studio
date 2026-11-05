#!/usr/bin/env node
/**
 * S3.1 JS Parity Test (docs/search_logs/s31_js_parity.js)
 * Evaluates output/search/metreyar_search.html scripts inside node vm with mock DOM,
 * extracts checkbox group values dynamically via regex from HTML (no hardcoded group names),
 * tests window.__rowsSearch across (a) all groups and (b) each single group alone
 * against docs/search_logs/s3_expected_rows.json.
 * Note: This test only verifies Python-JS parity and does not prove semantic correctness.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '..', '..');
const htmlPath = path.join(rootDir, 'output', 'search', 'metreyar_search.html');
const expectedPath = path.join(rootDir, 'docs', 'search_logs', 's3_expected_rows.json');

const html = fs.readFileSync(htmlPath, 'utf8');
const expected = JSON.parse(fs.readFileSync(expectedPath, 'utf8'));

// Extract group checkbox values from HTML via regex (never hardcode group names)
const cbRegex = /<input[^>]*class="grp-cb"[^>]*value="([^"]+)"/g;
const htmlGroups = [];
let mCb;
while ((mCb = cbRegex.exec(html)) !== null) {
  htmlGroups.push(mCb[1]);
}

console.log('=== S3.1 JS Parity Test (s31_parity.txt) ===');
console.log('Extracted checkbox groups from HTML via regex:', JSON.stringify(htmlGroups));

// Extract all <script>...</script> blocks
const scriptRegex = /<script[^>]*>([\s\S]*?)<\/script>/gi;
const scripts = [];
let mScr;
while ((mScr = scriptRegex.exec(html)) !== null) {
  scripts.push(mScr[1]);
}
console.log('Extracted script blocks count:', scripts.length);

// Minimal mock DOM environment
const elementsById = {};
function makeEl(id) {
  return {
    id: id || '',
    value: '',
    checked: false,
    textContent: '',
    innerHTML: '',
    style: {},
    children: [],
    parentNode: {
      insertBefore: function() {},
      removeChild: function() {}
    },
    addEventListener: function() {},
    appendChild: function(c) { this.children.push(c); }
  };
}

const knownIds = [
  'qInput', 'noSpaceCb', 'basePath', 'statsBar', 'resultsList',
  'builtDate', 'docsTableBody', 'selftest',
  'rowsSection', 'rowsOuterContainer', 'selftest_rows',
  'rowsStatsBar', 'rowsResultsList'
];
for (const id of knownIds) {
  elementsById[id] = makeEl(id);
}

const grpCheckboxes = htmlGroups.map(val => ({
  value: val,
  checked: true,
  addEventListener: function() {}
}));

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
  vm.runInContext(scripts[i], sandbox, { filename: `script_${i}.js`, timeout: 30000 });
}

let failCount = 0;

// Check selftest and selftest_rows
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

if (typeof sandbox.window.__rowsSearch !== 'function') {
  console.log('FAIL: window.__rowsSearch is not a function');
  console.log('PARITY: FAIL 1');
  process.exit(1);
}

// Build query list from requirements + sample codes in s3_expected_rows.json
const sampleCodes = expected.sample_codes || {};
const queries = [
  'بتن',
  'فولاد',
  'شیشه',
  'لوله',
  'کابل',
  'قالب بندی',
  sampleCodes['ابنیه_6digit'],
  sampleCodes['مکانیک_8digit'],
  sampleCodes['برق_8digit']
];

function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function compareByGroup(jsByGrp, pyByGrp) {
  const keys = new Set([...Object.keys(jsByGrp || {}), ...Object.keys(pyByGrp || {})]);
  for (const k of keys) {
    if ((jsByGrp[k] || 0) !== (pyByGrp[k] || 0)) return false;
  }
  return true;
}

for (const q of queries) {
  const qExp = expected.queries[q];
  if (!qExp) {
    console.log(`FAIL: Query '${q}' missing in s3_expected_rows.json`);
    failCount++;
    continue;
  }
  for (const mode of ['normal', 'no_space']) {
    const ignoreSpaces = (mode === 'no_space');
    const modeExp = qExp[mode];

    // (a) All groups selected
    const resAll = sandbox.window.__rowsSearch(q, ignoreSpaces, htmlGroups);
    const okTotalAll = (resAll.total === modeExp.total);
    const okByGrpAll = compareByGroup(resAll.by_group, modeExp.by_group);
    const okTopAll = sameJson(resAll.top_codes, modeExp.top_codes);

    if (!okTotalAll || !okByGrpAll || !okTopAll) {
      failCount++;
      console.log(`FAIL [ALL GROUPS] q='${q}' mode=${mode}: JS=${JSON.stringify(resAll)} vs PY=${JSON.stringify({total: modeExp.total, by_group: modeExp.by_group, top_codes: modeExp.top_codes})}`);
    } else {
      console.log(`OK [ALL GROUPS] q='${q}' mode=${mode}: total=${resAll.total}, by_group=${JSON.stringify(resAll.by_group)}, top3=${JSON.stringify(resAll.top_codes.slice(0, 3))}`);
    }

    // (b) Each group alone
    for (const grp of htmlGroups) {
      const resSingle = sandbox.window.__rowsSearch(q, ignoreSpaces, [grp]);
      const expSingleTotal = (modeExp.by_group && modeExp.by_group[grp]) ? modeExp.by_group[grp] : 0;
      const singleDetail = modeExp.single_group && modeExp.single_group[grp];
      const okSingleTotal = (resSingle.total === expSingleTotal);
      let okSingleTop = true;
      if (singleDetail) {
        okSingleTop = sameJson(resSingle.top_codes, singleDetail.top_codes);
      }
      // Verify no other group leaked into by_group
      const otherGroupsLeaked = Object.keys(resSingle.by_group || {}).some(k => k !== grp && resSingle.by_group[k] > 0);
      if (!okSingleTotal || !okSingleTop || otherGroupsLeaked) {
        failCount++;
        console.log(`FAIL [SINGLE GROUP='${grp}'] q='${q}' mode=${mode}: JS=${JSON.stringify(resSingle)} vs expTotal=${expSingleTotal}`);
      }
    }
    console.log(`  OK [SINGLE GROUPS ${JSON.stringify(htmlGroups)}] q='${q}' mode=${mode}`);
  }
}

if (failCount === 0) {
  console.log('PARITY: OK');
  process.exit(0);
} else {
  console.log(`PARITY: FAIL ${failCount}`);
  process.exit(1);
}
