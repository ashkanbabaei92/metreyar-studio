#!/usr/bin/env node
/**
 * S3 JS Parity Test (docs/search_logs/s3_js_parity.js)
 * اجرای اسکریپت‌های داخل output/search/metreyar_search.html در vm با DOM ساختگی
 * و مقایسهٔ خروجی window.__rowsSearch با docs/search_logs/s3_expected_rows.json
 * (که توسط docs/search_logs/s3_diag.py با شبیه‌ساز پایتون ساخته می‌شود).
 */

const fs = require('fs');
const vm = require('vm');

const htmlPath = 'output/search/metreyar_search.html';
const expectedPath = 'docs/search_logs/s3_expected_rows.json';

if (!fs.existsSync(htmlPath)) {
  console.error('HTML file not found:', htmlPath);
  process.exit(1);
}
if (!fs.existsSync(expectedPath)) {
  console.error('Expected JSON not found:', expectedPath);
  process.exit(1);
}

const html = fs.readFileSync(htmlPath, 'utf-8');
const expectedData = JSON.parse(fs.readFileSync(expectedPath, 'utf-8'));

// استخراج تمام بلوک‌های <script>...</script> از HTML
const scriptRegex = /<script>([\s\S]*?)<\/script>/gi;
const scripts = [];
let m;
while ((m = scriptRegex.exec(html)) !== null) {
  scripts.push(m[1]);
}

if (scripts.length < 2) {
  console.error('Expected at least 2 <script> blocks in HTML, found:', scripts.length);
  process.exit(1);
}

// ساخت DOM ساختگی حداقلی برای اجرای هر دو اسکریپت
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
     nextSibling: null,
    addEventListener: function() {},
    setAttribute: function() {},
    getAttribute: function() { return ''; }
  };
}

const elementsById = {
  qInput: makeEl('qInput'),
  noSpaceCb: makeEl('noSpaceCb'),
  basePath: makeEl('basePath'),
  statsBar: makeEl('statsBar'),
  resultsList: makeEl('resultsList'),
  builtDate: makeEl('builtDate'),
  docsTableBody: makeEl('docsTableBody'),
  selftest: makeEl('selftest'),
  selftest_rows: makeEl('selftest_rows'),
  rowsSection: makeEl('rowsSection'),
  rowsOuterContainer: makeEl('rowsOuterContainer'),
  rowsStatsBar: makeEl('rowsStatsBar'),
  rowsResultsList: makeEl('rowsResultsList')
};

const allGroups = expectedData.groups || [
  'فهرست‌بها — ابنیه',
  'فهرست‌بها — مکانیک',
  'فهرست‌بها — برق',
  'تعدیل',
  'شرایط عمومی پیمان'
];

const fakeGrpCbs = allGroups.map(function(g) {
  const cb = makeEl('');
  cb.checked = true;
  cb.value = g;
  return cb;
});

const sandbox = {
  window: {},
  document: {
    getElementById: function(id) {
      if (!elementsById[id]) {
        elementsById[id] = makeEl(id);
      }
      return elementsById[id];
    },
    querySelectorAll: function(sel) {
      if (sel === '.grp-cb') return fakeGrpCbs;
      return [];
    },
    createElement: function(tag) {
      return makeEl(tag);
    }
  },
  localStorage: {
    getItem: function() { return null; },
    setItem: function() {}
  },
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  console: console
};
sandbox.window = sandbox;

vm.createContext(sandbox);

for (let i = 0; i < scripts.length; i++) {
  vm.runInContext(scripts[i], sandbox, { filename: 'inline_script_' + i + '.js' });
}

let failCount = 0;

const stMain = elementsById.selftest.textContent || '';
const stRows = elementsById.selftest_rows.textContent || '';
console.log('selftest element:', stMain);
console.log('selftest_rows element:', stRows);

if (stMain !== 'خودآزمایی: OK') {
  console.error('FAIL: main selftest not OK:', stMain);
  failCount++;
}
if (stRows !== 'خودآزمایی ردیف‌ها: OK') {
  console.error('FAIL: rows selftest not OK:', stRows);
  failCount++;
}

if (typeof sandbox.window.__rowsSearch !== 'function') {
  console.error('FAIL: window.__rowsSearch is not a function');
  process.exit(1);
}

const queriesObj = expectedData.queries || {};
const queryList = Object.keys(queriesObj);
console.log('Testing', queryList.length, 'queries in normal & no_space modes...');

function sameGroupCounts(a, b) {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  for (const k of keys) {
    const va = (a && a[k]) || 0;
    const vb = (b && b[k]) || 0;
    if (va !== vb) return false;
  }
  return true;
}

for (const q of queryList) {
  const exp = queriesObj[q];
  for (const mode of ['normal', 'no_space']) {
    const ignoreSpaces = (mode === 'no_space');
    const jsRes = sandbox.window.__rowsSearch(q, ignoreSpaces, allGroups);
    const pyRes = exp[mode];

    const totalOk = (jsRes.total === pyRes.total);
    const grpOk = sameGroupCounts(jsRes.by_group, pyRes.by_group);
    const codesOk = JSON.stringify(jsRes.top_codes) === JSON.stringify(pyRes.top_codes);

    if (totalOk && grpOk && codesOk) {
      console.log(`  [OK] query="${q}" (${mode}): total=${jsRes.total} by_group=${JSON.stringify(jsRes.by_group)}`);
    } else {
      failCount++;
      console.log(`  [FAIL] query="${q}" (${mode}):`);
      console.log(`    JS: total=${jsRes.total} by_group=${JSON.stringify(jsRes.by_group)} top_codes=${JSON.stringify(jsRes.top_codes)}`);
      console.log(`    PY: total=${pyRes.total} by_group=${JSON.stringify(pyRes.by_group)} top_codes=${JSON.stringify(pyRes.top_codes)}`);
    }
  }
}

if (failCount === 0) {
  console.log('PARITY: OK');
} else {
  console.log('PARITY: FAIL ' + failCount);
  process.exitCode = 1;
}
