(function () {
  'use strict';

  /* ── Install tabs ─────────────────────────────────────────── */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function selectTab(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { selectTab(tab, false); });
    tab.addEventListener('keydown', function (e) {
      var j = null;
      if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') j = 0;
      else if (e.key === 'End') j = tabs.length - 1;
      if (j !== null) { e.preventDefault(); selectTab(tabs[j], true); }
    });
  });
  // Pre-select the tab that matches the visitor's system.
  var ua = (navigator.userAgent || '') + ' ' + (navigator.platform || '');
  if (/Win/i.test(ua) && !/Windows Phone/i.test(ua)) selectTab(document.getElementById('tab-win'), false);

  /* ── Installer URLs follow wherever the site is hosted ───── */
  if (/^https?:$/.test(location.protocol)) {
    var base = location.href.replace(/[#?].*$/, '').replace(/[^\/]*$/, '');
    document.querySelectorAll('code[data-url]').forEach(function (el) {
      var url = base + el.getAttribute('data-url');
      el.textContent = el.getAttribute('data-tpl').replace('{url}', url);
    });
  }

  /* ── Copy buttons ─────────────────────────────────────────── */
  var status = document.getElementById('copy-status');
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', '');
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  document.querySelectorAll('.copy').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.parentNode.querySelector('code').textContent;
      var done = function (ok) {
        btn.textContent = ok ? 'Copied' : 'Press Ctrl+C';
        btn.classList.toggle('done', ok);
        status.textContent = ok ? 'Command copied to clipboard.' : 'Copy failed. Select the command and copy it.';
        setTimeout(function () { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 1800);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallbackCopy(text)); });
      } else {
        done(fallbackCopy(text));
      }
    });
  });

  /* ── "Try a hit" case file (invented examples) ───────────── */
  var CASES = [
    {
      cust: [['Name', 'Marcus Hale'], ['Born', '12 April 2001', 1], ['Country', 'United Kingdom'], ['Record', 'KYC-20417']],
      hit:  [['Name', 'Marcus Hale'], ['Born', '1965 or 1966', 1], ['Country', 'United Kingdom'], ['Record', 'SR-8831']],
      tone: 'clear', title: 'Cleared', decision: 'DIFFERENT_ENTITY', code: 'DOB_MISMATCH',
      reason: 'The customer was born in 2001, but the hit’s search results put its birth year at 1965 or 1966. Same name, different person.',
      evidence: ['KYC-20417', 'SR-8831']
    },
    {
      cust: [['Name', 'Lena Voss'], ['Born', '3 September 1979', 1], ['Country', 'Germany', 1], ['Record', 'CUS-30418']],
      hit:  [['Name', 'Lena Voss'], ['Born', '3 September 1979', 1], ['Country', 'Germany', 1], ['Record', 'H-90211']],
      tone: 'match', title: 'Matched', decision: 'SAME_ENTITY', code: 'NAME_DOB_LOCATION_MATCH',
      reason: 'Name and date of birth agree, and both are tied to Germany. Nothing contradicts them, so the hit goes to compliance.',
      evidence: ['CUS-30418', 'H-90211']
    },
    {
      cust: [['Name', 'Omar Haddad'], ['Born', 'Not on file', 1], ['Country', 'Jordan'], ['Record', 'CUS-51276']],
      hit:  [['Name', 'Omar Haddad'], ['Born', '1972'], ['Country', 'Jordan'], ['Record', 'H-66042']],
      tone: 'review', title: 'Review', decision: 'REVIEW', code: 'INSUFFICIENT_DATA',
      reason: 'The names agree, but no date of birth is on file for the customer. Nothing confirms or contradicts the match, so an analyst decides.',
      evidence: ['CUS-51276', 'H-66042']
    }
  ];

  var verdict = document.getElementById('verdict');
  function facts(el, rows, tone) {
    el.textContent = '';
    rows.forEach(function (r) {
      var row = document.createElement('div');
      if (r[2]) { row.className = 'decisive'; row.style.setProperty('--tone', 'var(--' + tone + ')'); }
      var dt = document.createElement('dt'); dt.textContent = r[0];
      var dd = document.createElement('dd'); dd.textContent = r[1];
      row.appendChild(dt); row.appendChild(dd); el.appendChild(row);
    });
  }
  function show(i, animate) {
    var c = CASES[i];
    facts(document.getElementById('cust'), c.cust, c.tone);
    facts(document.getElementById('hit'), c.hit, c.tone);
    verdict.setAttribute('data-tone', c.tone);
    document.getElementById('v-title').textContent = c.title;
    document.getElementById('v-decision').textContent = c.decision;
    document.getElementById('v-code').textContent = c.code;
    document.getElementById('v-reason').textContent = c.reason;
    var ev = document.getElementById('v-evidence'); ev.textContent = '';
    c.evidence.forEach(function (id) { var s = document.createElement('span'); s.textContent = id; ev.appendChild(s); });
    verdict.classList.remove('pop');
    if (animate) { void verdict.offsetWidth; verdict.classList.add('pop'); }
  }
  document.querySelectorAll('input[name="case"]').forEach(function (r) {
    r.addEventListener('change', function () { show(parseInt(r.value, 10), true); });
  });
  show(0, true);
})();
