// Minimal site interactivity: BibTeX copy + leaderboard sort.
// No build step, no dependencies.

(function () {
  'use strict';

  // --- BibTeX copy button ---
  document.querySelectorAll('.copy-btn[data-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = document.querySelector(btn.getAttribute('data-target'));
      if (!target) return;
      var text = target.innerText;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { flashCopied(btn); });
      } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); flashCopied(btn); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  });

  function flashCopied(btn) {
    var orig = btn.textContent;
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(function () {
      btn.textContent = orig;
      btn.classList.remove('copied');
    }, 1400);
  }

  // --- Leaderboard sortable tables ---
  var tables = document.querySelectorAll('.lb-table');
  tables.forEach(function (table) {
    var headers = table.querySelectorAll('thead th');
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var state = { key: 'success', dir: 'desc' };

    headers.forEach(function (th) {
      th.addEventListener('click', function () {
        var key = th.getAttribute('data-key');
        if (!key) return;
        if (state.key === key) {
          state.dir = state.dir === 'asc' ? 'desc' : 'asc';
        } else {
          state.key = key;
          state.dir = (key === 'rank' || key === 'agent' || key === 'model' || key === 'browser') ? 'asc' : 'desc';
        }
        sortRows();
      });
    });

    function sortRows() {
      var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
      rows.sort(function (a, b) {
        var av = a.getAttribute('data-' + state.key) || '';
        var bv = b.getAttribute('data-' + state.key) || '';
        var an = parseFloat(av), bn = parseFloat(bv);
        var cmp;
        if (!isNaN(an) && !isNaN(bn)) {
          cmp = an - bn;
        } else {
          cmp = av.localeCompare(bv);
        }
        return state.dir === 'asc' ? cmp : -cmp;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      headers.forEach(function (th) {
        th.removeAttribute('data-sort-active');
        th.style.removeProperty('--sort-arrow');
        if (th.getAttribute('data-key') === state.key) {
          th.setAttribute('data-sort-active', '1');
          th.style.setProperty('--sort-arrow', state.dir === 'asc' ? '"▴"' : '"▾"');
        }
      });
    }
  });
})();
