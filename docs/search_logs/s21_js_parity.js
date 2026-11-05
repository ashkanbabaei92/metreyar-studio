#!/usr/bin/env node
"use strict";

const fs = require("fs");
const vm = require("vm");

function main() {
  const htmlPath = "output/search/metreyar_search.html";
  const expectedPath = "docs/search_logs/s21_expected_counts.json";

  if (!fs.existsSync(htmlPath)) {
    console.log("اجرا نشد: فایل " + htmlPath + " یافت نشد");
    process.exit(1);
  }
  if (!fs.existsSync(expectedPath)) {
    console.log("اجرا نشد: فایل " + expectedPath + " یافت نشد");
    process.exit(1);
  }

  const html = fs.readFileSync(htmlPath, "utf8");
  const expectedData = JSON.parse(fs.readFileSync(expectedPath, "utf8"));

  const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!scriptMatch) {
    console.log("اجرا نشد: تگ <script> در HTML یافت نشد");
    process.exit(1);
  }
  const scriptCode = scriptMatch[1];

  const groupsOrder = expectedData.groups_order || ["ابنیه", "مکانیک", "برق", "پیمان", "تعدیل"];

  // Mock DOM elements used by search_template.html
  const elements = {
    qInput: { value: "", addEventListener: function () {} },
    noSpaceCb: { checked: false, addEventListener: function () {} },
    basePath: { value: "", addEventListener: function () {} },
    statsBar: { innerHTML: "" },
    resultsList: { innerHTML: "" },
    builtDate: { textContent: "" },
    docsTableBody: { innerHTML: "" },
    selftest: { textContent: "" },
  };

  const grpCheckboxes = groupsOrder.map(function (g) {
    return {
      value: g,
      checked: true,
      addEventListener: function () {},
    };
  });

  const sandbox = {
    document: {
      getElementById: function (id) {
        if (!elements[id]) {
          elements[id] = { value: "", innerHTML: "", textContent: "", addEventListener: function () {} };
        }
        return elements[id];
      },
      querySelectorAll: function (sel) {
        if (sel === ".grp-cb") return grpCheckboxes;
        return [];
      },
    },
    localStorage: {
      getItem: function () { return null; },
      setItem: function () {},
    },
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    encodeURIComponent: encodeURIComponent,
    console: console,
  };

  vm.createContext(sandbox);
  try {
    vm.runInContext(scriptCode, sandbox, { timeout: 30000 });
  } catch (err) {
    console.log("اجرا نشد: خطای اجرای اسکریپت در vm: " + (err && err.stack ? err.stack : String(err)));
    process.exit(1);
  }

  if (typeof sandbox.runSearch !== "function") {
    console.log("اجرا نشد: تابع runSearch در زمینه vm یافت نشد");
    process.exit(1);
  }

  console.log("=== S2.1 JS vs PYTHON PARITY REPORT ===");
  console.log("DOM selftest status: " + elements.selftest.textContent);

  function extractTotalFromStatsHtml(statsHtml) {
    const m = String(statsHtml).match(/<strong>(\d+)\s+صفحه یافت شد<\/strong>/);
    return m ? parseInt(m[1], 10) : 0;
  }

  function runJsQuery(qStr, noSpace, activeGroupSet) {
    elements.qInput.value = qStr;
    elements.noSpaceCb.checked = !!noSpace;
    for (let i = 0; i < grpCheckboxes.length; i++) {
      grpCheckboxes[i].checked = activeGroupSet.has(grpCheckboxes[i].value);
    }
    sandbox.runSearch();
    return extractTotalFromStatsHtml(elements.statsBar.innerHTML);
  }

  let failCount = 0;
  const queries = expectedData.queries || [];

  for (let qi = 0; qi < queries.length; qi++) {
    const qObj = queries[qi];
    const qStr = qObj.query;

    const modes = [
      { key: "normal", label: "عادی", noSpace: false },
      { key: "no_space", label: "بی‌فاصله", noSpace: true },
    ];

    for (let mi = 0; mi < modes.length; mi++) {
      const mode = modes[mi];
      const pyInfo = qObj[mode.key];
      const pyTotal = pyInfo.total;
      const pyByGroup = pyInfo.by_group;

      // 1) All groups active -> total count
      const allGroupSet = new Set(groupsOrder);
      const jsTotal = runJsQuery(qStr, mode.noSpace, allGroupSet);

      // 2) Each group individually active -> per-group count via real runSearch()
      const jsByGroup = {};
      for (let gi = 0; gi < groupsOrder.length; gi++) {
        const gName = groupsOrder[gi];
        jsByGroup[gName] = runJsQuery(qStr, mode.noSpace, new Set([gName]));
      }

      let rowMatch = (jsTotal === pyTotal);
      for (let gi = 0; gi < groupsOrder.length; gi++) {
        const gName = groupsOrder[gi];
        if (jsByGroup[gName] !== pyByGroup[gName]) {
          rowMatch = false;
        }
      }

      if (!rowMatch) {
        failCount++;
        console.log(
          "[MISMATCH] query='" + qStr + "' mode=" + mode.label +
          " | PY total=" + pyTotal + " " + JSON.stringify(pyByGroup) +
          " | JS total=" + jsTotal + " " + JSON.stringify(jsByGroup)
        );
      } else {
        console.log(
          "[OK] query='" + qStr + "' (" + mode.label + ")" +
          " | total=" + jsTotal +
          " | groups=" + JSON.stringify(jsByGroup)
        );
      }
    }
  }

  // Also print sample .doc label from resultsList for query 'پیمانکار' in group 'پیمان'
  runJsQuery("پیمانکار", false, new Set(["پیمان"]));
  const sampleDocBadgeMatch = elements.resultsList.innerHTML.match(/4311-word\.txt[\s\S]{0,250}?without_page|<span class="badge">([^<]*4311-word\.txt[^<]*)<\/span>/);
  if (sampleDocBadgeMatch) {
    console.log("Sample .doc card badge in DOM: " + (sampleDocBadgeMatch[1] || sampleDocBadgeMatch[0]));
  }

  if (failCount === 0 && elements.selftest.textContent === "خودآزمایی: OK") {
    console.log("PARITY: OK");
  } else {
    if (elements.selftest.textContent !== "خودآزمایی: OK") failCount++;
    console.log("PARITY: FAIL " + failCount);
    process.exit(1);
  }
}

main();
