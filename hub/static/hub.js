/* Interactive behaviour: the card, the fixture list, the reliability table,
 * and the pages of every table long enough to have them.
 *
 * Plain ES5 in an IIFE, same as charts.js — no build step, and the file works
 * unchanged whether it is served by the local server or opened from disk.
 * Everything below reads the page's JSON block and nothing talks to the
 * server, so the two worlds behave identically.
 */
(function () {
  // charts.js has usually parsed it already; this is for a page without it.
  var D = window.__PAGE__ = window.__PAGE__ || (function () {
    var block = document.getElementById('page-data');
    try { return block ? JSON.parse(block.textContent) : {}; } catch (e) { return {}; }
  })();

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }
  // The mark for "no number here", matching NONE in pages.py. An en-dash, not
  // a hyphen: the Edge column carries signed values, so a hyphen here would be
  // the same glyph as the minus in the row above it.
  function pct(v, d) { return v === null || v === undefined ? '–' : (v * 100).toFixed(d === undefined ? 1 : d) + '%'; }
  function num(v, d) { return v === null || v === undefined ? '–' : v.toFixed(d === undefined ? 2 : d); }
  // The phone layout's classes, as `c.table` in components.py writes them:
  // `s-*` places a cell in the stacked row, `s-label` prints the column name
  // in front of it, and a cell with nothing in it is left out of the row.
  // Returns the end of a class attribute, closing quote and label included.
  function stack(roles, label, value) {
    return ' ' + roles + (arguments.length > 2 && (value === null || value === undefined)
      ? ' s-hide' : '') + '"' + (label ? ' data-label="' + label + '"' : '');
  }

  // ---- times: the reader's own clock ------------------------------------
  // pages.py writes every time in UTC, because the page is built in one time
  // zone and read in another; only the browser knows the reader's. English
  // words for the day and month, like the rest of the page; the reader's
  // clock for the hour.
  var WHEN = { weekday: 'short', day: 'numeric', month: 'short',
               hour: '2-digit', minute: '2-digit' };
  var STALE_MS = 2 * 86400000;     // a price older than this is marked
  function localWhen(iso) {
    var d = iso ? new Date(iso) : null;
    return d && !isNaN(d) ? d.toLocaleString('en-GB', WHEN) : null;
  }
  function ago(iso) {
    var ms = iso ? Date.now() - new Date(iso) : NaN;
    if (!(ms >= 0)) return null;
    var minutes = ms / 60000;
    if (minutes < 60) return Math.max(1, Math.round(minutes)) + ' min ago';
    if (minutes < 48 * 60) return Math.round(minutes / 60) + ' h ago';
    return Math.round(minutes / 1440) + ' days ago';
  }
  [].forEach.call(document.querySelectorAll('time[data-when]'), function (t) {
    var text = localWhen(t.getAttribute('datetime'));
    if (text) t.textContent = text;
  });
  [].forEach.call(document.querySelectorAll('time[data-ago]'), function (t) {
    var iso = t.getAttribute('datetime'), text = ago(iso);
    if (!text) return;
    t.textContent = text;
    t.title = localWhen(iso) || '';
    if (Date.now() - new Date(iso) > STALE_MS) t.classList.add('stale');
  });

  // ---- pages: a long table, one page at a time -------------------------
  // pages.py puts every row on the page, marks each with its page, hides all
  // but the first and draws the buttons in that state; this only moves
  // between them. Past seven numbered pages the buttons collapse to the
  // first, the last and the two either side of the current, so a ledger
  // thousands of rows long still has a pager one line high. Named pages (the
  // fixture days) never collapse: pages.py leaves `data-collapse` off them.
  //
  // `apply(page)` decides which rows show; by default, that page's. The
  // fixtures pass their own, because a search there looks across every day.
  function pager(box, apply) {
    if (box.pager) return box.pager;
    var nav = box.querySelector('nav.pager');
    if (!nav) return null;
    var buttons = [].slice.call(nav.querySelectorAll('button[data-page]'));
    var prev = nav.querySelector('button[data-step="-1"]');
    var next = nav.querySelector('button[data-step="1"]');
    var rows = [].slice.call(box.querySelectorAll('tr[data-page]'));
    var at = 0;
    apply = apply || function (page) {
      rows.forEach(function (tr) { tr.hidden = tr.getAttribute('data-page') !== page; });
    };

    function go(index, scroll) {
      at = Math.max(0, Math.min(buttons.length - 1, index));
      var last = buttons.length - 1;
      var many = nav.hasAttribute('data-collapse') && buttons.length > 7;
      [].slice.call(nav.querySelectorAll('.gap')).forEach(function (gap) {
        nav.removeChild(gap);
      });
      buttons.forEach(function (button, i) {
        if (i === at) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
        button.hidden = many && i > 0 && i < last && Math.abs(i - at) > 1;
      });
      buttons.forEach(function (button, i) {
        if (i > 0 && !button.hidden && buttons[i - 1].hidden) {
          var gap = document.createElement('span');
          gap.className = 'gap';
          gap.textContent = '…';
          nav.insertBefore(gap, button);
        }
      });
      prev.disabled = at === 0;
      next.disabled = at === last;
      apply(buttons[at].getAttribute('data-page'));
      // Paging from the buttons under a table would otherwise leave you at the
      // foot of the new page, looking at its last row rather than its first.
      if (scroll && box.getBoundingClientRect().top < 0) box.scrollIntoView();
    }

    nav.addEventListener('click', function (event) {
      var button = event.target.closest('button');
      if (!button || button.disabled) return;
      var step = button.getAttribute('data-step');
      go(step ? at + parseInt(step, 10) : buttons.indexOf(button), true);
      // An arrow that has just reached the end goes disabled, and would drop
      // the keyboard focus onto the body with it.
      if (button.disabled) buttons[at].focus({ preventScroll: true });
    });
    box.pager = { nav: nav };
    go(0);
    return box.pager;
  }

  // ---- unpacking the card ----------------------------------------------
  // The card arrives as arrays against a column list, with the strings that
  // repeat sent once and referenced by index — see `_card_payload` in
  // pages.py for why. Everything downstream wants the row objects it has
  // always had, so they get rebuilt here, once, and nothing else changes.
  (function expandCard() {
    var card = D.card;
    if (!card || !card.columns || !card.rows) return;
    var cols = card.columns, tables = card.tables || {}, comps = card.competitions || {};
    var flag = {};
    (card.flags || []).forEach(function (name) { flag[name] = 1; });

    card.rows = card.rows.map(function (packed) {
      var row = {};
      for (var i = 0; i < cols.length; i++) {
        var name = cols[i], value = packed[i];
        if (tables[name]) value = tables[name][value];
        else if (flag[name]) value = !!value;
        row[name] = value === undefined ? null : value;
      }
      // Carried by the competition code rather than repeated on every row.
      row.competition_name = comps[row.competition] || row.competition;
      return row;
    });
  })();

  // ---- the card --------------------------------------------------------
  (function card() {
    var body = $('card-body');
    if (!body || !D.card) return;
    var rows = D.card.rows || [];
    var chosen = {};
    // A card at the height of the season runs to thousands of rows, and every
    // step of a slider rebuilt all of them and hung a listener on each of
    // their checkboxes. Now a slider redraws at most once a frame, a page of
    // rows at a time with a button under them for the next, and one listener
    // on the table answers every checkbox in it.
    var PAGE = 200, limit = PAGE, pending = false;
    var more = $('card-more');
    var index = {};
    rows.forEach(function (r) { index[r.match + '|' + r.key] = r; });

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
      var showing = Math.min(list.length, limit);
      $('f-count').textContent = list.length + ' of ' + rows.length + ' selections'
        + (showing < list.length ? ', the first ' + showing + ' showing' : '');
      if (more) {
        more.hidden = showing >= list.length;
        more.textContent = 'Show ' + Math.min(PAGE, list.length - showing) + ' more';
      }
      if (!list.length) {
        body.innerHTML = '<tr><td colspan="10"><div class="empty">Nothing at this '
          + 'confidence. Lower the slider.</div></td></tr>';
        acca();
        return;
      }
      body.innerHTML = list.slice(0, showing).map(function (r) {
        var key = r.match + '|' + r.key;
        var edge = r.edge === null ? '–'
          : '<span style="color:' + (r.edge > 0 ? 'var(--pos)' : 'var(--neg)') + '">'
            + (r.edge >= 0 ? '+' : '') + (r.edge * 100).toFixed(1) + '%</span>';
        var band = r.hit_rate === null ? '–'
          : pct(r.hit_rate) + ' <span class="tag">n=' + (r.hit_rate_n || 0).toLocaleString() + '</span>';
        var flags = (r.new_team ? '<span class="tag warn">new team</span>' : '')
          + (r.validated ? '' : '<span class="tag warn">unverified</span>');
        // The col-* classes are what let a narrow window drop columns instead
        // of scrolling sideways; they must match the <th> classes in pages.py.
        return '<tr class="' + (r.validated ? '' : 'unvalidated') + '">'
          + '<td class="pickbox"><input type="checkbox" data-key="' + esc(key) + '"'
            + ' aria-label="' + esc('Add to the slip: ' + r.match + ', ' + r.selection) + '"'
            + (chosen[key] ? ' checked' : '') + '></td>'
          + '<td class="nowrap' + stack('s-meta') + '>' + esc(localWhen(r.kickoff) || r.date) + '</td>'
          + '<td class="col-league' + stack('s-meta') + '>'
            + esc(r.competition_name || r.competition) + '</td>'
          + '<td class="' + stack('s-title') + '>' + esc(r.match) + flags + '</td>'
          + '<td class="' + stack('s-sub') + '>' + esc(r.selection) + '</td>'
          + '<td class="num conf' + stack('s-end') + '><span class="confbar"><i style="width:'
            + (r.prob * 100).toFixed(0) + '%"></i></span>' + pct(r.prob) + '</td>'
          + '<td class="num' + stack('s-end2 s-label', 'Fair') + '>' + num(r.fair_odds) + '</td>'
          + '<td class="num col-offered' + stack('s-meta s-label', 'Offered', r.odds) + '>'
            + num(r.odds) + '</td>'
          + '<td class="num col-edge' + stack('s-meta s-label', 'Edge', r.edge) + '>'
            + edge + '</td>'
          + '<td class="col-band' + stack('s-meta s-label', 'Band record', r.hit_rate) + '>'
            + band + '</td></tr>';
      }).join('');
      acca();
    }

    // A filter that changes starts the list from the top again; however many
    // times it fires before the next frame, the table is drawn once.
    function schedule() {
      limit = PAGE;
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () { pending = false; render(); });
    }

    body.addEventListener('change', function (event) {
      var box = event.target;
      if (!box.matches('input[type=checkbox]')) return;
      var key = box.getAttribute('data-key');
      if (box.checked) chosen[key] = index[key];
      else delete chosen[key];
      acca();
    });
    if (more) more.addEventListener('click', function () {
      var from = limit;
      limit += PAGE;
      render();
      // The last page hides the button, and a hidden button drops the focus
      // onto the page itself; it goes to the first of the rows just added.
      if (more.hidden) {
        var first = body.querySelectorAll('input[type=checkbox]')[from];
        if (first) first.focus();
      }
    });

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
        + legs.map(function (l) { return l.match + ' - ' + l.selection; }).join('  •  ');
      $('acca-prob').textContent = pct(probability);
      $('acca-fair').textContent = num(1 / probability);
      $('acca-offered').textContent = haveOffered ? num(offered) : '–';

      var warn = $('acca-warn');
      warn.hidden = !duplicate;
      if (duplicate) {
        warn.textContent = 'Two legs come from the same fixture. They are not '
          + 'independent, so the combined chance above is overstated — usually badly.';
      }
    }

    Object.keys(controls).forEach(function (name) {
      if (controls[name]) controls[name].addEventListener('input', schedule);
    });
    var clear = $('acca-clear');
    if (clear) clear.addEventListener('click', function () { chosen = {}; render(); });
    render();
  })();

  // ---- accumulator pick: same slip, different number of legs -----------
  (function accumulatorPick() {
    var body = $('acca-body');
    if (!body || !D.accumulators) return;
    // `acca-size` on purpose, not `acca-legs`: the tray lower down the same
    // page owns that id, and getElementById returns whichever comes first.
    // While both were called it, ticking one pick wrote the slip summary into
    // this <select>, wiping every option out of it until a reload.
    var select = $('acca-size'), summary = $('acca-summary'), totals = $('acca-totals');

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
        return '<tr><td class="nowrap' + stack('s-meta') + '>' + esc(localWhen(leg.kickoff) || leg.date) + '</td>'
          + '<td class="col-league' + stack('s-meta') + '>'
            + esc(leg.competition_name || leg.competition) + '</td>'
          + '<td class="' + stack('s-title') + '>' + esc(leg.match) + '</td>'
          + '<td class="' + stack('s-sub') + '>' + esc(leg.selection) + '</td>'
          + '<td class="num conf' + stack('s-end') + '>' + pct(leg.prob) + '</td>'
          + '<td class="num' + stack('s-end2 s-label', 'Fair') + '>' + num(leg.fair_odds) + '</td>'
          + '<td class="num col-offered' + stack('s-meta s-label', 'Offered', leg.odds) + '>'
            + num(leg.odds) + '</td></tr>';
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

  // ---- history: one book at a time ------------------------------------
  // The single picks by price band, the accumulators by slip size. Every book
  // is already on the page, rendered by pages.py; this only picks which one is
  // showing, so a static export behaves the same with or without it — the
  // default is the one left visible.
  function books(id, selector, attribute) {
    var select = $(id);
    if (!select) return;
    var all = [].slice.call(document.querySelectorAll(selector));

    function show() {
      all.forEach(function (book) {
        book.hidden = book.getAttribute(attribute) !== select.value;
      });
    }

    select.addEventListener('input', show);
    show();
  }
  books('pick-book-band', '.pick-book', 'data-band');
  books('acca-book-size', '.acca-book', 'data-legs');

  // ---- fixtures: a day at a time, search, and expand a row -------------
  (function fixtures() {
    var table = document.querySelector('table.fixtures');
    if (!table || !D.card) return;
    var rows = [].slice.call(table.querySelectorAll('tbody tr'));
    var search = $('fx-q'), count = $('fx-count');
    var day = '0';
    var days = pager(table.closest('.paged'), function (page) { day = page; filter(); });

    function filter() {
      var q = (search.value || '').toLowerCase();
      // A search looks across every day — the team you are after may not play
      // on the one showing — so the day buttons stand aside while it does.
      if (days) days.nav.hidden = !!q;
      var shown = 0;
      rows.forEach(function (tr) {
        var hit = q ? tr.getAttribute('data-match').toLowerCase().indexOf(q) >= 0
          : tr.getAttribute('data-page') === day;
        tr.hidden = !hit;
        if (hit) shown++;
        // An open row's markets go and come back with it.
        var detail = tr.nextElementSibling;
        if (detail && detail.classList.contains('detail')) detail.hidden = !hit;
      });
      count.textContent = shown + ' of ' + rows.length + ' fixtures';
    }

    rows.forEach(function (tr, index) {
      tr.style.cursor = 'pointer';
      // The match name is a <button>: Enter or Space on it arrives here as a
      // click, and it says whether the row is open.
      var opener = tr.querySelector('button.fx-open');
      tr.addEventListener('click', function () {
        var next = tr.nextElementSibling;
        if (next && next.classList.contains('detail')) {
          next.parentNode.removeChild(next);
          if (opener) opener.setAttribute('aria-expanded', 'false');
          return;
        }
        var match = tr.getAttribute('data-match');
        var mine = (D.card.rows || []).filter(function (r) { return r.match === match; })
          .sort(function (a, b) { return b.prob - a.prob; });
        var detail = document.createElement('tr');
        detail.className = 'detail';
        detail.id = 'fx-detail-' + index;
        if (opener) {
          opener.setAttribute('aria-expanded', 'true');
          opener.setAttribute('aria-controls', detail.id);
        }
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
        // As `_band_gap` in pages.py: the colour, and past two points a word,
        // for a reader who cannot tell the colours apart. Overstating is the
        // failure that matters; understating is modesty.
        var state = gap < -0.02 ? 'critical' : (gap > 0.02 ? 'warning' : null);
        var text = (gap >= 0 ? '+' : '') + (gap * 100).toFixed(2) + 'pp';
        var cell = state
          ? '<span style="color:var(--' + state + ')">' + text + '</span><span class="tag '
            + state + '">' + (state === 'critical' ? 'overstated' : 'understated') + '</span>'
          : '<span style="color:var(--text-secondary)">' + text + '</span>';
        return '<tr><td>' + esc(r.band) + '</td><td class="num">' + r.n.toLocaleString()
          + '</td><td class="num">' + pct(r.predicted) + '</td><td class="num">'
          + pct(r.actual) + '</td><td class="num">' + cell + '</td><td>'
          + pct(r.ci_low) + ' – ' + pct(r.ci_high) + '</td></tr>';
      }).join('');
    }

    scope.addEventListener('input', render);
    render();
  })();

  // ---- every other paged table: the History books ----------------------
  // Last, so a page with its own rules has already claimed its table.
  [].slice.call(document.querySelectorAll('.paged')).forEach(function (box) {
    pager(box);
  });
})();
