/**
 * S3.2b — آزمون کامل نمایش بیشتر (فراتر از ۲۰۰) و ریست در ردیف‌ها و صفحه‌ها
 * با DOM ساختگی و HTML موجود بدون تغییر در هیچ فایل موجود.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '../..');
const HTML_PATH = path.join(ROOT, 'output', 'search', 'metreyar_search.html');

function main() {
  let failCount = 0;
  const failures = [];
  function recordCheck(cond, desc, actualInfo) {
    if (!cond) {
      failCount++;
      const msg = `[FAIL #${failCount}] ${desc} | مقدار واقعی: ${actualInfo}`;
      failures.push(msg);
      console.log('  ' + msg);
    } else {
      console.log(`  [OK] ${desc} (${actualInfo})`);
    }
  }

  try {
    const html = fs.readFileSync(HTML_PATH, 'utf8');

    const cbRegex = /<input[^>]*class="grp-cb"[^>]*value="([^"]+)"/g;
    const htmlGroups = [];
    let mCb;
    while ((mCb = cbRegex.exec(html)) !== null) {
      htmlGroups.push(mCb[1]);
    }

    console.log('=== S3.2b JS Show-More & Reset Test (s32b_showmore.txt) ===');
    console.log('گروه‌های استخراج‌شده از HTML:', JSON.stringify(htmlGroups));

    const scriptRegex = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
    const scripts = [];
    let mScript;
    while ((mScript = scriptRegex.exec(html)) !== null) {
      scripts.push(mScript[1]);
    }
    console.log(`تعداد بلاک‌های <script> در HTML: ${scripts.length}`);

    const elementsById = {};
    function makeEl(id) {
      const listeners = {};
      return {
        id: id,
        value: '',
        checked: false,
        textContent: '',
        innerHTML: '',
        className: '',
        style: { display: '' },
        dataset: {},
        children: [],
        appendChild: function(ch) { this.children.push(ch); return ch; },
        addEventListener: function(ev, fn) {
          if (!listeners[ev]) listeners[ev] = [];
          listeners[ev].push(fn);
        },
        trigger: function(ev, eventObj) {
          const fns = listeners[ev] || [];
          for (const fn of fns) {
            fn.call(this, Object.assign({ target: this, preventDefault: function(){} }, eventObj || {}));
          }
        },
        hasListener: function(ev) {
          return Boolean(listeners[ev] && listeners[ev].length > 0);
        },
        focus: function() {},
        select: function() {}
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
      clearTimeout: clearTimeout,
      encodeURI: encodeURI,
      RegExp: RegExp,
      Array: Array,
      Object: Object,
      String: String,
      Number: Number,
      Boolean: Boolean,
      Math: Math,
      JSON: JSON,
      Error: Error
    };
    sandbox.window = sandbox;

    vm.createContext(sandbox);
    for (let i = 0; i < scripts.length; i++) {
      vm.runInContext(scripts[i], sandbox, { filename: `script_${i}.js`, timeout: 60000 });
    }

    const w = sandbox.window;

    // ---------------------------------------------------------
    // بخش ۱: ردیف‌ها — پرسش 'بتن'، همهٔ گروه‌ها، بی‌فاصله خاموش، کلیک‌های پیاپی تا shown == total
    // ---------------------------------------------------------
    console.log('\n--- بخش ۱: ردیف‌ها (پرسش "بتن"، همهٔ گروه‌ها، بی‌فاصله خاموش، کلمهٔ کامل خاموش) ---');
    grpCheckboxes.forEach(cb => { cb.checked = true; });
    elementsById['noSpaceCb'].checked = false;
    elementsById['wholeWordCb'].checked = false;
    elementsById['qInput'].value = 'بتن';
    elementsById['qInput'].trigger('keydown', { key: 'Enter' });

    const rState0 = w.__getRowsState();
    const initialTotal = rState0.total;
    const rowsSequence = [];
    rowsSequence.push(`k=0 (اولیه): ${rState0.shown}/${rState0.total}`);

    const rCards0 = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
    recordCheck(
      rState0.shown === Math.min(initialTotal, 100) && rCards0 === rState0.shown,
      'ردیف‌ها وضعیت اولیه k=0: shown == min(total, 100) و برابر شمار کارت‌های DOM',
      `shown=${rState0.shown}, total=${rState0.total}, domCards=${rCards0}`
    );

    let k = 0;
    while (w.__getRowsState().shown < w.__getRowsState().total && k < 50) {
      k++;
      w.__rowsShowMore();
      const st = w.__getRowsState();
      const domCards = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
      const expectedShown = Math.min(initialTotal, 100 * (k + 1));
      rowsSequence.push(`k=${k}: ${st.shown}/${st.total}`);
      recordCheck(
        st.total === initialTotal && st.shown === expectedShown && domCards === st.shown,
        `ردیف‌ها بعد از کلیک k=${k}: shown == min(total, 100*(k+1))=${expectedShown} و total ثابت=${initialTotal}`,
        `shown=${st.shown}, total=${st.total}, domCards=${domCards}`
      );
    }

    const finalRState = w.__getRowsState();
    recordCheck(
      finalRState.shown === finalRState.total && elementsById['rowsShowMoreWrap'].style.display === 'none',
      'ردیف‌ها در پایان حلقه‌ٔ نمایش بیشتر: shown == total و دکمهٔ نمایش بیشتر مخفی شده',
      `shown=${finalRState.shown}, total=${finalRState.total}, wrapDisplay='${elementsById['rowsShowMoreWrap'].style.display}'`
    );
    console.log('دنبالهٔ کامل shown/total ردیف‌ها برای «بتن»:\n  ' + rowsSequence.join(' -> '));

    // ---------------------------------------------------------
    // بخش ۲: ریست ردیف‌ها پس از تغییر پرسش ('فولاد') و تغییر تیک «کلمهٔ کامل»
    // ---------------------------------------------------------
    console.log('\n--- بخش ۲: آزمون ریست ردیف‌ها پس از تغییر پرسش و تغییر تیک کلمهٔ کامل ---');
    console.log(
      `وضعیت شنونده‌ها در DOM ساختگی: qInput(keydown)=${elementsById['qInput'].hasListener('keydown')}, ` +
      `qInput(input)=${elementsById['qInput'].hasListener('input')}, ` +
      `wholeWordCb(change)=${elementsById['wholeWordCb'].hasListener('change')}, ` +
      `noSpaceCb(change)=${elementsById['noSpaceCb'].hasListener('change')}`
    );

    // الف) تغییر پرسش به 'فولاد' با رویداد واقعی ثبت‌شده روی qInput
    elementsById['qInput'].value = 'فولاد';
    if (elementsById['qInput'].hasListener('keydown')) {
      elementsById['qInput'].trigger('keydown', { key: 'Enter' });
      console.log('  رویداد واقعی keydown(Enter) روی qInput اجرا شد.');
    } else if (elementsById['qInput'].hasListener('input')) {
      elementsById['qInput'].trigger('input');
      console.log('  رویداد واقعی input روی qInput اجرا شد.');
    } else {
      w.__renderRowsUI();
      console.log('  شنونده آزموده نشد (فراخوانی مستقیم __renderRowsUI).');
    }
    const rStateFoolad = w.__getRowsState();
    const domCardsFoolad = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
    recordCheck(
      rStateFoolad.shown === Math.min(rStateFoolad.total, 100) && domCardsFoolad === rStateFoolad.shown,
      'ریست ردیف‌ها پس از تغییر پرسش به «فولاد»: shown == min(total, 100)',
      `shown=${rStateFoolad.shown}, total=${rStateFoolad.total}, domCards=${domCardsFoolad}`
    );

    // یک بار نمایش بیشتر روی «فولاد» بزنیم تا shown > 100 شود و سپس ریست تیک کلمهٔ کامل را بسنجیم
    if (rStateFoolad.total > 100) {
      w.__rowsShowMore();
      const stAfterMore = w.__getRowsState();
      console.log(`  پس از یک کلیک __rowsShowMore روی «فولاد»: shown=${stAfterMore.shown}/${stAfterMore.total}`);
    }

    // ب) تغییر تیک «کلمهٔ کامل» با رویداد change روی wholeWordCb
    elementsById['wholeWordCb'].checked = true;
    if (elementsById['wholeWordCb'].hasListener('change')) {
      elementsById['wholeWordCb'].trigger('change');
      console.log('  رویداد واقعی change روی wholeWordCb اجرا شد.');
    } else {
      w.__renderRowsUI();
      console.log('  شنونده آزموده نشد (فراخوانی مستقیم __renderRowsUI).');
    }
    const rStateWW = w.__getRowsState();
    const domCardsWW = (elementsById['rowsResultsList'].innerHTML.match(/class="row-card"/g) || []).length;
    recordCheck(
      rStateWW.shown === Math.min(rStateWW.total, 100) && domCardsWW === rStateWW.shown,
      'ریست ردیف‌ها پس از روشن کردن تیک «کلمهٔ کامل» (change): shown == min(total, 100)',
      `shown=${rStateWW.shown}, total=${rStateWW.total}, domCards=${domCardsWW}`
    );

    // بازگرداندن تیک کلمهٔ کامل به خاموش روی پرسش پرتعداد ('بتن') برای اثبات ریست به دقیقا 100 از حالت shown>100
    elementsById['qInput'].value = 'بتن';
    elementsById['qInput'].trigger('keydown', { key: 'Enter' });
    w.__rowsShowMore(); // الان shown=200
    const beforeWWReset = w.__getRowsState();
    console.log(`  قبل از تغییر تیک wholeWordCb روی «بتن»: shown=${beforeWWReset.shown}/${beforeWWReset.total}`);
    elementsById['wholeWordCb'].checked = false;
    if (elementsById['wholeWordCb'].hasListener('change')) {
      elementsById['wholeWordCb'].trigger('change');
    } else {
      w.__renderRowsUI();
      console.log('  شنونده آزموده نشد');
    }
    const afterWWReset = w.__getRowsState();
    recordCheck(
      afterWWReset.shown === Math.min(afterWWReset.total, 100),
      'ریست ردیف‌ها از shown=200 به min(total,100)=100 با رویداد change روی wholeWordCb در «بتن»',
      `shown=${afterWWReset.shown}, total=${afterWWReset.total}`
    );

    // ---------------------------------------------------------
    // بخش ۳: صفحه‌ها — اولین پرسش از ['راه','پیمان'] که __pagesSearch(...).total > 200 باشد
    // ---------------------------------------------------------
    console.log('\n--- بخش ۳: صفحه‌ها (جستجوی اولین پرسش از ["راه", "پیمان"] با total > 200) ---');
    const candidateQueries = ['راه', 'پیمان'];
    let chosenPageQuery = null;
    for (const cq of candidateQueries) {
      const pRes = w.__pagesSearch(cq, false, htmlGroups, false);
      console.log(`  بررسی __pagesSearch("${cq}", false, ALL, false): total=${pRes.total}`);
      if (chosenPageQuery === null && pRes.total > 200) {
        chosenPageQuery = cq;
      }
    }

    if (chosenPageQuery === null) {
      console.log('نیازمند بررسی: هیچ پرسش بالای ۲۰۰ نبود');
    } else {
      console.log(`  پرسش انتخاب‌شده برای صفحه‌ها: «${chosenPageQuery}»`);
      elementsById['noSpaceCb'].checked = false;
      elementsById['wholeWordCb'].checked = false;
      elementsById['qInput'].value = chosenPageQuery;

      // اجرای runSearch از طریق گیرندهٔ رویداد موجود (keydown Enter روی qInput یا change روی noSpaceCb)
      if (elementsById['noSpaceCb'].hasListener('change')) {
        elementsById['noSpaceCb'].trigger('change');
        console.log('  runSearch از طریق گیرندهٔ رویداد change روی noSpaceCb فراخوانی شد.');
      } else if (elementsById['qInput'].hasListener('keydown')) {
        elementsById['qInput'].trigger('keydown', { key: 'Enter' });
        console.log('  runSearch از طریق گیرندهٔ رویداد keydown(Enter) روی qInput فراخوانی شد.');
      } else {
        console.log('نیازمند بررسی: هیچ گیرندهٔ رویدادی برای اجرای runSearch یافت نشد');
      }

      const pState0 = w.__getPagesState();
      const pInitialTotal = pState0.total;
      const pagesSequence = [];
      pagesSequence.push(`k=0 (اولیه): ${pState0.shown}/${pState0.total}`);
      const pDom0 = (elementsById['resultsList'].innerHTML.match(/class="card"/g) || []).length;

      recordCheck(
        pState0.shown === Math.min(pInitialTotal, 200) && pDom0 === pState0.shown,
        `صفحه‌ها («${chosenPageQuery}») وضعیت اولیه k=0: shown == min(total, 200) و برابر کارت‌های DOM`,
        `shown=${pState0.shown}, total=${pState0.total}, domCards=${pDom0}`
      );

      let pk = 0;
      while (w.__getPagesState().shown < w.__getPagesState().total && pk < 50) {
        pk++;
        w.__pagesShowMore();
        const pst = w.__getPagesState();
        const pDom = (elementsById['resultsList'].innerHTML.match(/class="card"/g) || []).length;
        const expectedPShown = Math.min(pInitialTotal, 200 + 100 * pk);
        pagesSequence.push(`k=${pk}: ${pst.shown}/${pst.total}`);
        recordCheck(
          pst.total === pInitialTotal && pst.shown === expectedPShown && pDom === pst.shown,
          `صفحه‌ها («${chosenPageQuery}») بعد از کلیک k=${pk}: shown == min(total, 200+100*k)=${expectedPShown} و total ثابت=${pInitialTotal}`,
          `shown=${pst.shown}, total=${pst.total}, domCards=${pDom}`
        );
      }

      const finalPState = w.__getPagesState();
      recordCheck(
        finalPState.shown === finalPState.total && elementsById['pagesShowMoreWrap'].style.display === 'none',
        `صفحه‌ها («${chosenPageQuery}») در پایان نمایش بیشتر: shown == total و مخفی شدن دکمه`,
        `shown=${finalPState.shown}, total=${finalPState.total}, wrapDisplay='${elementsById['pagesShowMoreWrap'].style.display}'`
      );
      console.log(`دنبالهٔ کامل shown/total صفحه‌ها برای «${chosenPageQuery}»:\n  ` + pagesSequence.join(' -> '));

      // آزمون ریست صفحه‌ها پس از تغییر پرسش (مثلاً به 'پیمان' یا 'بتن')
      const nextPageQuery = chosenPageQuery === 'راه' ? 'پیمان' : 'راه';
      elementsById['qInput'].value = nextPageQuery;
      elementsById['noSpaceCb'].trigger('change');
      const pStateReset = w.__getPagesState();
      const pDomReset = (elementsById['resultsList'].innerHTML.match(/class="card"/g) || []).length;
      recordCheck(
        pStateReset.shown === Math.min(pStateReset.total, 200) && pDomReset === pStateReset.shown,
        `ریست صفحه‌ها پس از تغییر پرسش به «${nextPageQuery}»: shown == min(total, 200)`,
        `shown=${pStateReset.shown}, total=${pStateReset.total}, domCards=${pDomReset}`
      );
    }

    console.log('\n============================================================');
    if (failCount === 0) {
      console.log('SHOWMORE: OK');
    } else {
      for (const f of failures) {
        console.log(f);
      }
      console.log(`SHOWMORE: FAIL ${failCount}`);
      process.exit(1);
    }
  } catch (err) {
    console.error('EXCEPTION in s32b_js_showmore.js:', err && err.stack ? err.stack : err);
    console.log(`SHOWMORE: FAIL ${failCount + 1}`);
    process.exit(1);
  }
}

main();
