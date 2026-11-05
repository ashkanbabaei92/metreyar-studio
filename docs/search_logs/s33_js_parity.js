#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = '/home/user/webapp';
const htmlPath = path.join(ROOT, 'output/search/metreyar_search.html');
const s32ExpPath = path.join(ROOT, 'docs/search_logs/s32_expected.json');
const s33ExpPath = path.join(ROOT, 'docs/search_logs/s33_expected.json');

const html = fs.readFileSync(htmlPath, 'utf-8');
const s32Exp = JSON.parse(fs.readFileSync(s32ExpPath, 'utf-8'));
const s33Exp = JSON.parse(fs.readFileSync(s33ExpPath, 'utf-8'));

// Extract checkbox values from HTML
const cbRegex = /<input[^>]*class="grp-cb"[^>]*value="([^"]+)"/g;
const htmlCheckboxGroups = [];
let m;
while ((m = cbRegex.exec(html)) !== null) {
  htmlCheckboxGroups.push(m[1]);
}

// Extract scripts from HTML
const scriptRegex = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
const scripts = [];
while ((m = scriptRegex.exec(html)) !== null) {
  scripts.push(m[1]);
}

function makeElem(id, tag, val, checked) {
  const listeners = {};
  return {
    id: id || '',
    tagName: (tag || 'DIV').toUpperCase(),
    value: val || '',
    checked: !!checked,
    disabled: false,
    innerHTML: '',
    textContent: '',
    style: { display: '' },
    classList: {
      add: function() {},
      remove: function() {},
      toggle: function() {},
      contains: function() { return false; }
    },
    addEventListener: function(ev, fn) {
      if (!listeners[ev]) listeners[ev] = [];
      listeners[ev].push(fn);
    },
    dispatchEvent: function(evObj) {
      const ev = typeof evObj === 'string' ? evObj : (evObj && evObj.type);
      const arr = listeners[ev] || [];
      for (let i = 0; i < arr.length; i++) {
        arr[i].call(this, { target: this, type: ev });
      }
    }
  };
}

const elementsById = {
  qInput: makeElem('qInput', 'INPUT', '', false),
  noSpaceCb: makeElem('noSpaceCb', 'INPUT', '', false),
  wholeWordCb: makeElem('wholeWordCb', 'INPUT', '', false),
  wordStartCb: makeElem('wordStartCb', 'INPUT', '', false),
  basePath: makeElem('basePath', 'INPUT', '', false),
  statsBar: makeElem('statsBar', 'DIV', '', false),
  resultsList: makeElem('resultsList', 'DIV', '', false),
  pagesShowMoreWrap: makeElem('pagesShowMoreWrap', 'DIV', '', false),
  rowsStatsBar: makeElem('rowsStatsBar', 'DIV', '', false),
  rowsResultsList: makeElem('rowsResultsList', 'DIV', '', false),
  rowsShowMoreWrap: makeElem('rowsShowMoreWrap', 'DIV', '', false),
  rowsBrowserSection: makeElem('rowsBrowserSection', 'DIV', '', false),
  builtDate: makeElem('builtDate', 'SPAN', '', false),
  selftest: makeElem('selftest', 'SPAN', '', false),
  selftest_rows: makeElem('selftest_rows', 'SPAN', '', false)
};

const grpCbElems = htmlCheckboxGroups.map(function(g) {
  return makeElem('', 'INPUT', g, true);
});

const sandbox = {
  window: {},
  document: {
    getElementById: function(id) {
      if (!elementsById[id]) {
        elementsById[id] = makeElem(id, 'DIV', '', false);
      }
      return elementsById[id];
    },
    querySelectorAll: function(sel) {
      if (sel === '.grp-cb') return grpCbElems;
      return [];
    },
    querySelector: function() { return null; },
    createElement: function(tag) { return makeElem('', tag, '', false); }
  },
  localStorage: {
    getItem: function() { return null; },
    setItem: function() {}
  },
  console: console,
  setTimeout: function(fn) { fn(); return 1; },
  clearTimeout: function() {}
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
for (let i = 0; i < scripts.length; i++) {
  vm.runInContext(scripts[i], sandbox);
}

let failCount = 0;
const logs = [];
function log(msg) {
  console.log(msg);
  logs.push(msg);
}
function fail(msg) {
  failCount++;
  log('FAIL: ' + msg);
}

log('=== 1. HTML Checkbox Groups & Selftests ===');
log('Extracted groups: ' + JSON.stringify(htmlCheckboxGroups));
if (JSON.stringify(htmlCheckboxGroups) !== JSON.stringify(['ابنیه', 'مکانیک', 'برق', 'پیمان', 'تعدیل'])) {
  fail('Checkbox groups mismatch');
}
log('selftest: ' + elementsById.selftest.textContent + ' | selftest_rows: ' + elementsById.selftest_rows.textContent);
if (!elementsById.selftest.textContent.includes('OK')) {
  fail('selftest not OK: ' + elementsById.selftest.textContent);
}
if (!elementsById.selftest_rows.textContent.includes('OK')) {
  fail('selftest_rows not OK: ' + elementsById.selftest_rows.textContent);
}

log('=== 2. Selftest countOccurrences (normal / wordStart / wholeWord) ===');
const co = sandbox.__countOccurrences;
const nrm = sandbox.normalize;
const h1 = nrm('بتن و بتنی و آرماتوربندی و ترموباند و باند');
if (co(h1, nrm('بتن'), false, false) !== 2) fail('selftest بتن normal');
if (co(h1, nrm('بتن'), false, true) !== 2) fail('selftest بتن wordStart');
if (co(h1, nrm('بتن'), true, false) !== 1) fail('selftest بتن wholeWord');
if (co(h1, nrm('باند'), false, false) !== 2) fail('selftest باند normal');
if (co(h1, nrm('باند'), false, true) !== 1) fail('selftest باند wordStart (must reject ترموباند)');
if (co(h1, nrm('باند'), true, false) !== 1) fail('selftest باند wholeWord');
if (co(h1, nrm('بتن'), true, true) !== 1) fail('selftest precedence both true -> wholeWord');
log('Selftest finished, fails so far=' + failCount);

log('=== 3. Mutual Exclusion & noSpace Disabled Tests ===');
const wwCb = elementsById.wholeWordCb;
const wsCb = elementsById.wordStartCb;
const nsCb = elementsById.noSpaceCb;

wwCb.checked = true;
wwCb.dispatchEvent('change');
if (!wwCb.checked || wsCb.checked) fail('Checking wholeWord should leave wholeWord=true, wordStart=false');

wsCb.checked = true;
wsCb.dispatchEvent('change');
if (!wsCb.checked || wwCb.checked) fail('Checking wordStart should uncheck wholeWord');

wwCb.checked = true;
wwCb.dispatchEvent('change');
if (!wwCb.checked || wsCb.checked) fail('Checking wholeWord should uncheck wordStart');

nsCb.checked = true;
nsCb.dispatchEvent('change');
if (!wwCb.disabled || !wsCb.disabled) fail('Checking noSpace should disable both wholeWordCb and wordStartCb');

nsCb.checked = false;
wwCb.checked = false;
wsCb.checked = false;
nsCb.dispatchEvent('change');
if (wwCb.disabled || wsCb.disabled) fail('Unchecking noSpace should enable both wholeWordCb and wordStartCb');
log('UI state tests finished, fails so far=' + failCount);

log('=== 4. Parity across 11 queries x 6 groups x 3 modes ===');
const queries = s33Exp.queries;
const groupsList = ['ALL'].concat(htmlCheckboxGroups);

function objEq(a, b) {
  const ka = Object.keys(a || {}).sort();
  const kb = Object.keys(b || {}).sort();
  if (ka.length !== kb.length) return false;
  for (let i = 0; i < ka.length; i++) {
    if (ka[i] !== kb[i] || a[ka[i]] !== b[kb[i]]) return false;
  }
  return true;
}

function arrEq(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

for (let qi = 0; qi < queries.length; qi++) {
  const q = queries[qi];
  for (let gi = 0; gi < groupsList.length; gi++) {
    const g = groupsList[gi];
    const gArg = g === 'ALL' ? null : [g];

    // normal & whole_word vs s32Exp
    const modes32 = [
      ['normal', false, false],
      ['whole_word', true, false]
    ];
    for (let mi = 0; mi < modes32.length; mi++) {
      const mName = modes32[mi][0];
      const ww = modes32[mi][1];
      const ws = modes32[mi][2];
      const expNode = s32Exp.cases[q][g][mName];
      const rJs = sandbox.__rowsSearch(q, false, gArg, ww, ws);
      const pJs = sandbox.__pagesSearch(q, false, gArg, ww, ws);

      if (rJs.total !== expNode.rows.total || !objEq(rJs.by_group, expNode.rows.by_group) || !arrEq(rJs.top_codes, expNode.rows.top_codes)) {
        fail(`rows ${mName} mismatch q=${q} g=${g}: js=${JSON.stringify(rJs)} vs exp=${JSON.stringify(expNode.rows)}`);
      }
      if (pJs.total !== expNode.pages.total || !objEq(pJs.by_doc, expNode.pages.by_doc) || !objEq(pJs.by_group, expNode.pages.by_group) || !arrEq(pJs.top_keys, expNode.pages.top_keys)) {
        fail(`pages ${mName} mismatch q=${q} g=${g}: js=${JSON.stringify(pJs)} vs exp=${JSON.stringify(expNode.pages)}`);
      }
    }

    // word_start vs s33Exp
    const expWs = s33Exp.cases[q][g].word_start;
    const rWs = sandbox.__rowsSearch(q, false, gArg, false, true);
    const pWs = sandbox.__pagesSearch(q, false, gArg, false, true);
    if (rWs.total !== expWs.rows.total || !objEq(rWs.by_group, expWs.rows.by_group) || !arrEq(rWs.top_codes, expWs.rows.top_codes)) {
      fail(`rows word_start mismatch q=${q} g=${g}: js=${JSON.stringify(rWs)} vs exp=${JSON.stringify(expWs.rows)}`);
    }
    if (pWs.total !== expWs.pages.total || !objEq(pWs.by_doc, expWs.pages.by_doc) || !objEq(pWs.by_group, expWs.pages.by_group) || !arrEq(pWs.top_keys, expWs.pages.top_keys)) {
      fail(`pages word_start mismatch q=${q} g=${g}: js=${JSON.stringify(pWs)} vs exp=${JSON.stringify(expWs.pages)}`);
    }
  }
}
log('Parity check across all cases finished, fails so far=' + failCount);

log('=== 5. Show-More in word_start mode ===');
for (let i = 0; i < grpCbElems.length; i++) grpCbElems[i].checked = true;
nsCb.checked = false;
wwCb.checked = false;
wsCb.checked = true;

// Rows show-more on 'بتن'
elementsById.qInput.value = 'بتن';
wsCb.dispatchEvent('change');
let rSt = sandbox.__getRowsState();
const rSeq = [`k=0: ${rSt.shown}/${rSt.total}`];
let k = 0;
if (rSt.shown !== Math.min(rSt.total, 100 * (k + 1))) {
  fail(`Rows show-more initial mismatch: ${rSt.shown}/${rSt.total}`);
}
while (rSt.shown < rSt.total) {
  if (elementsById.rowsShowMoreWrap.innerHTML === '') {
    fail('rowsShowMoreWrap innerHTML empty before reaching total');
  }
  sandbox.__rowsShowMore();
  k++;
  rSt = sandbox.__getRowsState();
  rSeq.push(`k=${k}: ${rSt.shown}/${rSt.total}`);
  const expectedShown = Math.min(rSt.total, 100 * (k + 1));
  if (rSt.shown !== expectedShown) {
    fail(`Rows show-more step k=${k} mismatch: got ${rSt.shown}, expected ${expectedShown}`);
  }
}
if (elementsById.rowsShowMoreWrap.innerHTML !== '') {
  fail('rowsShowMoreWrap innerHTML should be empty string when shown === total');
}
log('Rows word_start show-more (بتن): ' + rSeq.join(' -> ') + ' | final wrap innerHTML==="" : ' + (elementsById.rowsShowMoreWrap.innerHTML === ''));

// Pages show-more: first query from ['راه', 'پیمان'] with total > 200 in word_start mode
const pageCandidates = ['راه', 'پیمان'];
let chosenPageQ = null;
for (let i = 0; i < pageCandidates.length; i++) {
  const cq = pageCandidates[i];
  const pCheck = sandbox.__pagesSearch(cq, false, null, false, true);
  log(`Candidate page query '${cq}' in word_start ALL: total=${pCheck.total}`);
  if (pCheck.total > 200 && !chosenPageQ) {
    chosenPageQ = cq;
  }
}
if (!chosenPageQ) {
  log('Pages word_start show-more from [راه, پیمان] with total>200: نیازمند بررسی');
} else {
  elementsById.qInput.value = chosenPageQ;
  wsCb.dispatchEvent('change');
  let pSt = sandbox.__getPagesState();
  let pk = 0;
  const pSeq = [`k=0: ${pSt.shown}/${pSt.total}`];
  if (pSt.shown !== Math.min(pSt.total, 200 + 100 * pk)) {
    fail(`Pages show-more initial mismatch: ${pSt.shown}/${pSt.total}`);
  }
  while (pSt.shown < pSt.total) {
    if (elementsById.pagesShowMoreWrap.innerHTML === '') {
      fail('pagesShowMoreWrap innerHTML empty before reaching total');
    }
    sandbox.__pagesShowMore();
    pk++;
    pSt = sandbox.__getPagesState();
    pSeq.push(`k=${pk}: ${pSt.shown}/${pSt.total}`);
    const expP = Math.min(pSt.total, 200 + 100 * pk);
    if (pSt.shown !== expP) {
      fail(`Pages show-more step k=${pk} mismatch: got ${pSt.shown}, expected ${expP}`);
    }
  }
  if (elementsById.pagesShowMoreWrap.innerHTML !== '') {
    fail('pagesShowMoreWrap innerHTML should be empty string when shown === total');
  }
  log(`Pages word_start show-more (${chosenPageQ}): ` + pSeq.join(' -> ') + ' | final wrap innerHTML==="" : ' + (elementsById.pagesShowMoreWrap.innerHTML === ''));
}

if (failCount === 0) {
  log('PARITY: OK');
  process.exit(0);
} else {
  log('PARITY: FAIL ' + failCount);
  process.exit(1);
}
