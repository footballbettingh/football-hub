/* Site behaviour: theme toggle, charts, sortable/filterable tables.
 * Edited directly — the build copies this file verbatim.
 * Reads its data from window.__PAGE__, injected per page by build.py.
 */
(function () {
  var D = window.__PAGE__ || {};

  // ---- theme ----------------------------------------------------------
  var btn = document.getElementById('theme');
  function effectiveTheme() {
    return document.documentElement.getAttribute('data-theme') ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
  function applyLabel() { if (btn) btn.textContent = effectiveTheme() === 'dark' ? 'Light' : 'Dark'; }
  try {
    var saved = localStorage.getItem('vb-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  } catch (e) {}
  applyLabel();
  if (btn) btn.addEventListener('click', function () {
    var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('vb-theme', next); } catch (e) {}
    applyLabel();
    redraw();
  });

  // ---- tooltip --------------------------------------------------------
  var tip = document.createElement('div');
  tip.className = 'tooltip';
  document.body.appendChild(tip);
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.style.opacity = '1';
    var r = tip.getBoundingClientRect();
    var left = x + 14, top = y - r.height - 10;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 16;
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  }
  function hideTip() { tip.style.opacity = '0'; }

  var NS = 'http://www.w3.org/2000/svg';
  function el(n, a) {
    var e = document.createElementNS(NS, n);
    for (var k in a) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
    return e;
  }
  function fmtMoney(v) { return (v >= 0 ? '+' : '') + v.toFixed(2); }
  function fmtPct(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  // Round ticks to 1/2/2.5/5 x 10^n and snap the domain, so axes read
  // 0/25/50/75 rather than wherever the data padding happened to land.
  function niceScale(min, max, target) {
    if (max === min) { max += 1; min -= 1; }
    var step = Math.pow(10, Math.floor(Math.log10((max - min) / target)));
    var norm = (max - min) / target / step;
    step *= norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
    var lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, ticks = [];
    for (var v = lo; v <= hi + step / 1e6; v += step) ticks.push(Math.abs(v) < step / 1e6 ? 0 : v);
    return { lo: lo, hi: hi, ticks: ticks, step: step };
  }

  // ---- equity curve ---------------------------------------------------
  function drawEquity() {
    var host = document.getElementById('equity');
    if (!host || !D.equity) return;
    host.innerHTML = '';
    var pts = D.equity;
    if (!pts.length) { host.innerHTML = '<div class="empty">No bets to plot.</div>'; return; }

    // Draw at the container's real width: forcing a wider viewBox and letting
    // CSS scale it shrinks the tick text too, which is how a chart ends up
    // unreadable on a phone.
    var W = Math.max(host.clientWidth || 860, 280), H = W < 480 ? 220 : 280;
    var M = { t: 14, r: 16, b: 30, l: W < 480 ? 42 : 56 };
    var iw = W - M.l - M.r, ih = H - M.t - M.b;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H,
      role: 'img', 'aria-label': 'Cumulative profit and loss over time' });

    var ys = pts.map(function (p) { return p.cum; }).concat([0]);
    var sc = niceScale(Math.min.apply(null, ys), Math.max.apply(null, ys), 4);
    var X = function (i) { return M.l + (pts.length < 2 ? iw / 2 : i / (pts.length - 1) * iw); };
    var Y = function (v) { return M.t + ih - (v - sc.lo) / (sc.hi - sc.lo) * ih; };

    sc.ticks.forEach(function (v) {
      var y = Y(v);
      svg.appendChild(el('line', { x1: M.l, x2: M.l + iw, y1: y, y2: y,
        class: v === 0 ? 'baseline' : 'gridline' }));
      var t = el('text', { x: M.l - 9, y: y + 4, class: 'tick', 'text-anchor': 'end' });
      t.textContent = (v > 0 ? '+' : '') + v;
      svg.appendChild(t);
    });
    [0, pts.length - 1].forEach(function (i, k) {
      var t = el('text', { x: X(i), y: H - 9, class: 'tick',
        'text-anchor': k === 0 ? 'start' : 'end' });
      t.textContent = pts[i].date;
      svg.appendChild(t);
    });

    svg.appendChild(el('path', {
      d: pts.map(function (p, i) { return (i ? 'L' : 'M') + X(i) + ' ' + Y(p.cum); }).join(' '),
      fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

    var cross = el('line', { y1: M.t, y2: M.t + ih, class: 'baseline', opacity: 0 });
    var dot = el('circle', { r: 4.5, fill: 'var(--series-1)', stroke: 'var(--surface-1)',
      'stroke-width': 2, opacity: 0 });
    svg.appendChild(cross); svg.appendChild(dot);

    var hit = el('rect', { x: M.l, y: M.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(hit);
    hit.addEventListener('mousemove', function (ev) {
      var r = svg.getBoundingClientRect();
      var sx = (ev.clientX - r.left) * (W / r.width);
      var i = Math.max(0, Math.min(pts.length - 1, Math.round((sx - M.l) / iw * (pts.length - 1))));
      var p = pts[i];
      cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.setAttribute('opacity', 1);
      dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(p.cum)); dot.setAttribute('opacity', 1);
      showTip('<div class="tt-h">' + esc(p.match) + '</div>' +
        '<div class="tt-r">' + esc(p.date) + ' &middot; bet ' + (i + 1) + ' of ' + pts.length + '</div>' +
        '<div class="tt-r">' + esc(p.outcome) + ' @ ' + p.odds.toFixed(2) + ' &middot; ' +
        (p.won ? 'won' : 'lost') + '</div>' +
        '<div class="tt-r">running P&amp;L ' + fmtMoney(p.cum) + '</div>', ev.clientX, ev.clientY);
    });
    hit.addEventListener('mouseleave', function () {
      hideTip(); cross.setAttribute('opacity', 0); dot.setAttribute('opacity', 0);
    });
    host.appendChild(svg);
  }

  // ---- ROI by period: diverging bars ----------------------------------
  function drawPeriods() {
    var host = document.getElementById('periods');
    if (!host || !D.periods) return;
    host.innerHTML = '';
    var rows = D.periods;
    if (!rows.length) { host.innerHTML = '<div class="empty">Not enough bets to split.</div>'; return; }

    var W = Math.max(host.clientWidth || 860, 280);
    var narrow = W < 480;
    var rowH = narrow ? 32 : 38;
    var M = { t: 8, r: narrow ? 46 : 62, b: 26, l: narrow ? 56 : 132 };
    var H = M.t + rows.length * rowH + M.b, iw = W - M.l - M.r;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H,
      role: 'img', 'aria-label': 'Return on investment by period' });

    var peak = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.roi); }));
    // Nice-step the half-range then mirror, so zero stays dead centre; stepping
    // the full width lands on a coarser step and over-pads the axis.
    var half = niceScale(0, Math.max(peak * 1.15, 5), 3);
    var step = half.ticks[1] - half.ticks[0], mx = half.hi;
    var zero = M.l + iw / 2;
    var X = function (v) { return zero + v / mx * (iw / 2); };

    for (var tv = -mx; tv <= mx + step / 1e6; tv += step) {
      var v = Math.abs(tv) < step / 1e6 ? 0 : tv, x = X(v);
      svg.appendChild(el('line', { x1: x, x2: x, y1: M.t, y2: M.t + rows.length * rowH,
        class: v === 0 ? 'baseline' : 'gridline' }));
      var t = el('text', { x: x, y: H - 8, class: 'tick', 'text-anchor': 'middle' });
      t.textContent = (v > 0 ? '+' : '') + Math.round(v) + '%';
      svg.appendChild(t);
    }

    rows.forEach(function (r, i) {
      var y = M.t + i * rowH + 7, h = rowH - 16;       // 2px+ surface gap between bars
      var x0 = X(0), w = Math.max(Math.abs(X(r.roi) - x0), 2), neg = r.roi < 0;
      svg.appendChild(el('rect', { x: neg ? x0 - w : x0, y: y, width: w, height: h,
        fill: neg ? 'var(--neg)' : 'var(--pos)', rx: 4, ry: 4 }));
      // square the baseline end so the bar reads as anchored to zero
      svg.appendChild(el('rect', { x: neg ? x0 - 4 : x0, y: y, width: 4, height: h,
        fill: neg ? 'var(--neg)' : 'var(--pos)' }));

      var lab = el('text', { x: M.l - 12, y: y + h / 2 + 4, class: 'tick', 'text-anchor': 'end' });
      lab.textContent = narrow ? r.label.replace('Period ', 'P') : r.label;
      svg.appendChild(lab);

      var val = el('text', { x: neg ? x0 - w - 8 : x0 + w + 8, y: y + h / 2 + 4,
        class: 'bar-label', 'text-anchor': neg ? 'end' : 'start' });
      val.textContent = fmtPct(r.roi);
      svg.appendChild(val);

      var hit = el('rect', { x: M.l, y: M.t + i * rowH, width: iw, height: rowH, fill: 'transparent' });
      svg.appendChild(hit);
      hit.addEventListener('mousemove', function (ev) {
        showTip('<div class="tt-h">' + esc(r.label) + '</div>' +
          '<div class="tt-r">' + esc(r.span) + '</div>' +
          '<div class="tt-r">' + r.n + ' bets &middot; ROI ' + fmtPct(r.roi) + '</div>' +
          '<div class="tt-r">P&amp;L ' + fmtMoney(r.pnl) + '</div>', ev.clientX, ev.clientY);
      });
      hit.addEventListener('mouseleave', hideTip);
    });
    host.appendChild(svg);
  }

  // ---- bets table -----------------------------------------------------
  var sortKey = 'date', sortDir = 1;
  function renderBets() {
    var tb = document.getElementById('bets-body');
    if (!tb || !D.bets) return;
    var q = (document.getElementById('f-q').value || '').toLowerCase();
    var res = document.getElementById('f-res').value;
    var out = document.getElementById('f-out').value;
    var comp = document.getElementById('f-comp');
    var compV = comp ? comp.value : 'all';
    var mkt = document.getElementById('f-market');
    var mktV = mkt ? mkt.value : 'all';

    var rows = D.bets.filter(function (b) {
      if (q && b.match.toLowerCase().indexOf(q) < 0) return false;
      if (res === 'won' && !b.won) return false;
      if (res === 'lost' && b.won) return false;
      if (out !== 'all' && b.outcome !== out) return false;
      if (compV !== 'all' && b.competition !== compV) return false;
      if (mktV !== 'all' && b.market !== mktV) return false;
      return true;
    });
    rows.sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (typeof x === 'string') return x.localeCompare(y) * sortDir;
      return (x - y) * sortDir;
    });

    var pnl = rows.reduce(function (s, b) { return s + b.pnl; }, 0);
    var roi = rows.length ? pnl / (rows.length * D.stake) * 100 : 0;
    document.getElementById('f-count').textContent =
      rows.length + ' of ' + D.bets.length + ' bets  ·  ROI ' + fmtPct(roi);

    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="10" class="empty">No bets match these filters.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(function (b) {
      var c = b.won ? 'var(--good)' : 'var(--critical)';
      return '<tr>' +
        '<td>' + esc(b.date) + '</td>' +
        '<td>' + esc(b.competition) + '</td>' +
        '<td>' + esc(b.market) + '</td>' +
        '<td>' + esc(b.match) + '</td>' +
        '<td>' + esc(b.score) + '</td>' +
        '<td>' + esc(b.outcome) + '</td>' +
        '<td class="num">' + b.odds.toFixed(2) + '</td>' +
        '<td class="num">' + (b.model_prob * 100).toFixed(1) + '%</td>' +
        '<td class="num">' + (b.edge * 100).toFixed(1) + '%</td>' +
        '<td class="num"><span class="pill"><span class="dot" style="background:' + c + '"></span>' +
        (b.won ? 'won' : 'lost') + ' ' + fmtMoney(b.pnl) + '</span></td></tr>';
    }).join('');
  }

  function wireSort() {
    Array.prototype.forEach.call(document.querySelectorAll('th.sortable'), function (th) {
      th.addEventListener('click', function () {
        var k = th.dataset.key;
        if (k === sortKey) sortDir *= -1; else { sortKey = k; sortDir = 1; }
        Array.prototype.forEach.call(document.querySelectorAll('th.sortable'), function (o) {
          o.classList.toggle('active', o === th);
          var a = o.querySelector('.arrow');
          if (a) a.textContent = o === th ? (sortDir > 0 ? '▲' : '▼') : '▴▾';
        });
        renderBets();
      });
    });
  }

  // ---- top picks: reader chooses how many ------------------------------
  function renderTopPicks() {
    var body = document.getElementById('topn-body');
    if (!body || !D.topPicks) return;
    var sel = document.getElementById('f-topn');
    var n = sel ? parseInt(sel.value, 10) : 10;
    var rows = D.topPicks.slice(0, n);
    var count = document.getElementById('topn-count');
    if (count) {
      var q = rows.filter(function (r) { return r.qualifies; }).length;
      count.textContent = 'showing ' + rows.length + ' of ' + D.topPicks.length +
        '  ·  ' + q + ' meet the criteria';
    }
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="11" class="empty">No priced fixtures.</td></tr>';
      return;
    }
    body.innerHTML = rows.map(function (p, i) {
      var flag = p.qualifies
        ? '<span class="pill"><span class="dot" style="background:var(--good)"></span>meets criteria</span>'
        : '<span style="color:var(--muted)">skipped</span>';
      var col = p.edge >= 0 ? 'var(--pos)' : 'var(--neg)';
      return '<tr' + (p.qualifies ? '' : ' class="muted"') + '>' +
        '<td>' + (i + 1) + '</td>' +
        '<td>' + esc(p.date) + '</td>' +
        '<td>' + esc(p.competition) + '</td>' +
        '<td>' + esc(p.match) + '</td>' +
        '<td>' + esc(p.market) + '</td>' +
        '<td>' + esc(p.pick) + '</td>' +
        '<td class="num">' + p.odds.toFixed(2) + '</td>' +
        '<td class="num">' + (p.prob * 100).toFixed(1) + '%</td>' +
        '<td class="num">' + (p.mkt * 100).toFixed(1) + '%</td>' +
        '<td class="num" style="color:' + col + '">' + (p.edge >= 0 ? '+' : '') +
          (p.edge * 100).toFixed(1) + '%</td>' +
        '<td>' + flag + '</td></tr>';
    }).join('');
  }

  var topSel = document.getElementById('f-topn');
  if (topSel) topSel.addEventListener('input', renderTopPicks);
  renderTopPicks();

  function redraw() { drawEquity(); drawPeriods(); }

  ['f-q', 'f-res', 'f-out', 'f-comp', 'f-market'].forEach(function (id) {
    var e = document.getElementById(id);
    if (e) e.addEventListener('input', renderBets);
  });
  wireSort();
  renderBets();
  redraw();
  var rt;
  addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(redraw, 120); });
})();
