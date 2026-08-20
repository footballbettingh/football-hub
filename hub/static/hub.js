/* Interactive behaviour: the control strip, the card, the fixture list.
 *
 * Plain ES5 in an IIFE, same as charts.js — no build step, and the file works
 * unchanged whether it is served by the local server or opened from disk. The
 * only difference between those two worlds is whether #control exists; every
 * data-driven part below reads window.__PAGE__ and does not care.
 */
(function () {
  var D = window.__PAGE__ || {};

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }
  function pct(v, d) { return v === null || v === undefined ? '—' : (v * 100).toFixed(d === undefined ? 1 : d) + '%'; }
  function num(v, d) { return v === null || v === undefined ? '—' : v.toFixed(d === undefined ? 2 : d); }

  // ---- control strip ---------------------------------------------------
  (function control() {
    var root = $('control');
    if (!root) return;
    var log = $('joblog'), running = $('job-running');
    var buttons = [].slice.call(root.querySelectorAll('button.run'));
    var cursor = 0, timer = null, wasRunning = null;

    function setBusy(job) {
      buttons.forEach(function (b) { b.disabled = !!job; });
      if (!running) return;
      running.innerHTML = job
        ? '<span class="dot"></span>' + esc(job) + ' — running. You can leave this page open.'
        : '';
    }

    function appendLines(lines) {
      if (!lines.length) return;
      if (log.hidden) log.hidden = false;
      lines.forEach(function (line) {
        var span = document.createElement('span');
        if (line.indexOf('!!!') === 0) span.className = 'bad';
        span.textContent = line + '\n';
        log.appendChild(span);
      });
      log.scrollTop = log.scrollHeight;
    }

    function poll() {
      fetch('/api/status?since=' + cursor).then(function (r) { return r.json(); })
        .then(function (s) {
          cursor = s.next;
          appendLines(s.lines || []);
          setBusy(s.label);
          if (wasRunning && !s.running) {
            // The artifacts on disk just changed; the page is now stale.
            appendLines(['=== reloading the page with the new data']);
            setTimeout(function () { location.reload(); }, 900);
            return;
          }
          wasRunning = s.running;
          timer = setTimeout(poll, s.running ? 900 : 4000);
        })
        .catch(function () { timer = setTimeout(poll, 5000); });
    }

    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        var job = button.getAttribute('data-job');
        var cost = button.classList.contains('danger');
        if (cost && !confirm(button.textContent.trim() + '\n\nThis spends real API '
            + 'credits. Continue?')) return;
        setBusy(button.textContent);
        fetch('/api/run/' + job, { method: 'POST' })
          .then(function (r) { return r.json(); })
          .then(function (res) {
            if (!res.ok) appendLines(['!!! ' + res.message]);
            clearTimeout(timer);
            poll();
          });
      });
    });
    poll();
  })();

  // ---- the card --------------------------------------------------------
  (function card() {
    var body = $('card-body');
    if (!body || !D.card) return;
    var rows = D.card.rows || [];
    var chosen = {};

    var controls = {
      q: $('f-q'), comp: $('f-comp'), group: $('f-group'), min: $('f-min'),
      odds: $('f-odds'), one: $('f-one'), unval: $('f-unval'),
      priced: $('f-priced'),
    };

    function visible() {
      var q = (controls.q.value || '').toLowerCase();
      var comp = controls.comp.value, group = controls.group.value;
      var minProb = parseInt(controls.min.value, 10) / 100;
      var minOdds = parseInt(controls.odds.value, 10) / 100;
      var seen = {};
      return rows.filter(function (r) {
        if (r.prob < minProb) return false;
        if (r.fair_odds === null || r.fair_odds < minOdds) return false;
        // Only 1X2 and the totals lines are quoted anywhere, so this is also
        // the filter for "show me the ones I can compare against a price".
        if (controls.priced && controls.priced.checked && r.odds === null) return false;
        if (!controls.unval.checked && !r.validated) return false;
        if (comp !== 'all' && r.competition !== comp) return false;
        if (group !== 'all' && r.group !== group) return false;
        if (q && r.match.toLowerCase().indexOf(q) < 0) return false;
        if (controls.one.checked) {
          if (seen[r.match]) return false;
          seen[r.match] = 1;
        }
        return true;
      });
    }

    function render() {
      $('f-min-v').textContent = controls.min.value + '%';
      $('f-odds-v').textContent = (parseInt(controls.odds.value, 10) / 100).toFixed(2);

      var list = visible();
      $('f-count').textContent = list.length + ' of ' + rows.length + ' selections';
      if (!list.length) {
        body.innerHTML = '<tr><td colspan="10"><div class="empty">Nothing at this '
          + 'confidence. Lower the slider.</div></td></tr>';
        acca();
        return;
      }
      body.innerHTML = list.map(function (r) {
        var key = r.match + '|' + r.key;
        var edge = r.edge === null ? '—'
          : '<span style="color:' + (r.edge > 0 ? 'var(--pos)' : 'var(--neg)') + '">'
            + (r.edge >= 0 ? '+' : '') + (r.edge * 100).toFixed(1) + '%</span>';
        var band = r.hit_rate === null ? '—'
          : pct(r.hit_rate) + ' <span class="tag">n=' + (r.hit_rate_n || 0).toLocaleString() + '</span>';
        var flags = (r.new_team ? '<span class="tag warn">new team</span>' : '')
          + (r.validated ? '' : '<span class="tag warn">unverified</span>');
        // The col-* classes are what let a narrow window drop columns instead
        // of scrolling sideways; they must match the <th> classes in pages.py.
        return '<tr class="' + (r.validated ? '' : 'unvalidated') + '">'
          + '<td class="pickbox"><input type="checkbox" data-key="' + esc(key) + '"'
            + (chosen[key] ? ' checked' : '') + '></td>'
          + '<td class="nowrap">' + esc(r.date) + '</td>'
          + '<td class="col-league">' + esc(r.competition_name || r.competition) + '</td>'
          + '<td>' + esc(r.match) + flags + '</td>'
          + '<td>' + esc(r.selection) + '</td>'
          + '<td class="num conf"><span class="confbar"><i style="width:'
            + (r.prob * 100).toFixed(0) + '%"></i></span>' + pct(r.prob) + '</td>'
          + '<td class="num">' + num(r.fair_odds) + '</td>'
          + '<td class="num col-offered">' + num(r.odds) + '</td>'
          + '<td class="num col-edge">' + edge + '</td>'
          + '<td class="col-band">' + band + '</td></tr>';
      }).join('');

      [].slice.call(body.querySelectorAll('input[type=checkbox]')).forEach(function (box) {
        box.addEventListener('change', function () {
          var key = box.getAttribute('data-key');
          if (box.checked) {
            chosen[key] = byKey(key);
          } else {
            delete chosen[key];
          }
          acca();
        });
      });
      acca();
    }

    function byKey(key) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].match + '|' + rows[i].key === key) return rows[i];
      }
      return null;
    }

    function acca() {
      var tray = $('acca');
      if (!tray) return;
      var legs = Object.keys(chosen).map(function (k) { return chosen[k]; });
      if (!legs.length) { tray.hidden = true; return; }
      tray.hidden = false;

      var probability = 1, offered = 1, haveOffered = true, matches = {}, duplicate = false;
      legs.forEach(function (leg) {
        probability *= leg.prob;
        if (leg.odds === null) haveOffered = false; else offered *= leg.odds;
        if (matches[leg.match]) duplicate = true;
        matches[leg.match] = 1;
      });

      $('acca-legs').textContent = legs.length + ' leg' + (legs.length > 1 ? 's' : '') + ': '
        + legs.map(function (l) { return l.match + ' — ' + l.selection; }).join('  •  ');
      $('acca-prob').textContent = pct(probability);
      $('acca-fair').textContent = num(1 / probability);
      $('acca-offered').textContent = haveOffered ? num(offered) : '—';

      var warn = $('acca-warn');
      warn.hidden = !duplicate;
      if (duplicate) {
        warn.textContent = 'Two legs come from the same fixture. They are not '
          + 'independent, so the combined chance above is overstated — usually badly.';
      }
    }

    Object.keys(controls).forEach(function (name) {
      if (controls[name]) controls[name].addEventListener('input', render);
    });
    var clear = $('acca-clear');
    if (clear) clear.addEventListener('click', function () { chosen = {}; render(); });
    render();
  })();

  // ---- accumulator pick: same slip, different number of legs -----------
  (function accumulatorPick() {
    var body = $('acca-body');
    if (!body || !D.accumulators) return;
    var select = $('acca-legs'), summary = $('acca-summary'), totals = $('acca-totals');

    function render() {
      var acca = D.accumulators[select.value];
      if (!acca) {
        body.innerHTML = '<tr><td colspan="7"><div class="empty">No accumulator '
          + 'of this size clears the target.</div></td></tr>';
        totals.innerHTML = '';
        summary.textContent = '';
        return;
      }
      body.innerHTML = acca.selections.map(function (leg) {
        return '<tr><td class="nowrap">' + esc(leg.date) + '</td>'
          + '<td class="col-league">' + esc(leg.competition_name || leg.competition) + '</td>'
          + '<td>' + esc(leg.match) + '</td>'
          + '<td>' + esc(leg.selection) + '</td>'
          + '<td class="num conf">' + pct(leg.prob) + '</td>'
          + '<td class="num">' + num(leg.fair_odds) + '</td>'
          + '<td class="num col-offered">' + num(leg.odds) + '</td></tr>';
      }).join('');

      summary.textContent = 'every leg pays ' + num(acca.min_leg_odds) + ' or better';
      totals.innerHTML =
        '<div class="n"><div class="k">Combined chance</div><div class="v">'
          + pct(acca.probability) + '</div></div>'
        + '<div class="n"><div class="k">Fair odds</div><div class="v">'
          + num(acca.fair_odds) + '</div></div>'
        + '<div class="n"><div class="k">Offered</div><div class="v">'
          + num(acca.offered_odds) + '</div></div>'
        + '<div class="n"><div class="k">Weakest leg</div><div class="v">'
          + pct(acca.weakest_leg) + '</div></div>';
    }

    select.addEventListener('input', render);
    render();
  })();

  // ---- fixtures: search, and expand a row into every market ------------
  (function fixtures() {
    var table = document.querySelector('table.fixtures');
    if (!table || !D.card) return;
    var rows = [].slice.call(table.querySelectorAll('tbody tr'));
    var search = $('fx-q'), count = $('fx-count');

    function filter() {
      var q = (search.value || '').toLowerCase();
      var shown = 0;
      rows.forEach(function (tr) {
        var hit = !q || tr.getAttribute('data-match').toLowerCase().indexOf(q) >= 0;
        tr.hidden = !hit;
        if (hit) shown++;
        var detail = tr.nextElementSibling;
        if (detail && detail.classList.contains('detail')) detail.hidden = !hit || detail.hidden;
      });
      count.textContent = shown + ' of ' + rows.length + ' fixtures';
    }

    rows.forEach(function (tr) {
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', function () {
        var next = tr.nextElementSibling;
        if (next && next.classList.contains('detail')) {
          next.parentNode.removeChild(next);
          return;
        }
        var match = tr.getAttribute('data-match');
        var mine = (D.card.rows || []).filter(function (r) { return r.match === match; })
          .sort(function (a, b) { return b.prob - a.prob; });
        var detail = document.createElement('tr');
        detail.className = 'detail';
        detail.innerHTML = '<td colspan="10"><div class="tablewrap"><table>'
          + '<thead><tr><th>Selection</th><th class="num">Confidence</th>'
          + '<th class="num">Fair odds</th><th>Market</th></tr></thead><tbody>'
          + mine.map(function (r) {
            return '<tr' + (r.validated ? '' : ' class="unvalidated"') + '><td>'
              + esc(r.selection) + '</td><td class="num conf">' + pct(r.prob)
              + '</td><td class="num">' + num(r.fair_odds) + '</td><td>'
              + esc((D.card.groups || {})[r.group] || r.group) + '</td></tr>';
          }).join('') + '</tbody></table></div></td>';
        tr.parentNode.insertBefore(detail, tr.nextSibling);
      });
    });

    if (search) search.addEventListener('input', filter);
    filter();
  })();

  // ---- reliability: one market at a time --------------------------------
  (function reliability() {
    var body = $('rel-body');
    if (!body || !D.reliability) return;
    var scope = $('rel-scope'), count = $('rel-count');

    function render() {
      var chosen = scope.value;
      var list = D.reliability.filter(function (r) { return r.scope === chosen; });
      count.textContent = list.reduce(function (t, r) { return t + r.n; }, 0)
        .toLocaleString() + ' graded selections';
      body.innerHTML = list.map(function (r) {
        var gap = r.actual - r.predicted;
        // Overstating is the failure that matters; understating is modesty.
        var colour = gap < -0.02 ? 'var(--critical)'
          : (Math.abs(gap) > 0.02 ? 'var(--warning)' : 'var(--text-secondary)');
        return '<tr><td>' + esc(r.band) + '</td><td class="num">' + r.n.toLocaleString()
          + '</td><td class="num">' + pct(r.predicted) + '</td><td class="num">'
          + pct(r.actual) + '</td><td class="num" style="color:' + colour + '">'
          + (gap >= 0 ? '+' : '') + (gap * 100).toFixed(2) + 'pp</td><td>'
          + pct(r.ci_low) + ' – ' + pct(r.ci_high) + '</td></tr>';
      }).join('');
    }

    scope.addEventListener('input', render);
    render();
  })();
})();
