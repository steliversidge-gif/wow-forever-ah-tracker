'use strict';
// Static dashboard: reads the JSON files written by `forever-ah run`. No server, no build step.

const $ = (s, root = document) => root.querySelector(s);
const view = $('#view'), tip = $('#tip');
const S = { meta: null, houseId: null, cache: {} };
const F = { budget: '', minProfit: 10, risk: 'HIGH', play: '', q: '', sort: 'profit' };   // buy list filters
const M = { q: '', sort: 'sold', cls: '' };                                               // market filters

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function money(c) {
  if (c == null) return '–';
  c = Math.round(c);
  const neg = c < 0; c = Math.abs(c);
  const g = Math.floor(c / 10000), s = Math.floor(c % 10000 / 100), k = c % 100, p = [];
  if (g) p.push(g.toLocaleString() + 'g');
  if (s || (g && k)) p.push(s + 's');
  if (k || !p.length) p.push(k + 'c');
  return (neg ? '-' : '') + p.join(' ');
}
const shortMoney = c => c >= 10000 ? (c / 10000).toFixed(c >= 100000 ? 0 : 1) + 'g' : c >= 100 ? Math.round(c / 100) + 's' : Math.round(c) + 'c';
const pct = (x, signed) => x == null ? '–' : (signed && x > 0 ? '+' : '') + (x * 100).toFixed(Math.abs(x) < 0.1 ? 1 : 0) + '%';
const num = (x, d = 0) => x == null ? '–' : Number(x).toLocaleString(undefined, { maximumFractionDigits: d });
const clock = e => new Date(e * 1000).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
function ago(e) {
  const m = Math.max(0, Math.round((Date.now() / 1000 - e) / 60));
  return m < 90 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
}
function sellsIn(d) {
  if (d == null) return '–';
  if (d === 0) return 'instant';
  return d < 1 ? `~${Math.max(1, Math.round(d * 24))} h` : `~${d.toFixed(d < 10 ? 1 : 0)} d`;
}

function getJSON(path) {
  if (!S.cache[path]) {
    S.cache[path] = fetch(path, { cache: 'no-cache' }).then(r => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
  }
  return S.cache[path];
}
async function loadHouse(id) {
  const d = await getJSON(`data/house-${id}.json`);
  if (!d.items) {
    const c = d.market.cols;
    d.items = d.market.rows.map(r => Object.fromEntries(c.map((k, i) => [k, r[i]])));
    for (const x of d.items) if (x.n_snaps < S.meta.min_history_snaps) x.ref_price = null;
    d.byId = new Map(d.items.map(x => [x.variant_id, x]));
  }
  return d;
}
const houseMeta = id => S.meta.houses.find(h => h.id === id);
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const houseLabel = h => cap(h.faction) + (h.pinned ? '' : ` #${h.id} (candidate)`);
const qClass = q => 'q-' + String(q || 'common').toLowerCase();
const itemLink = x => `<a class="${qClass(x.quality)}" href="#/item/${x.variant_id}">${esc(x.name)}</a>`;

const RISK = {
  NONE: ['good', '', 'None'], LOW: ['good', '', 'Low'],
  MEDIUM: ['warning', 'sq', 'Medium'], HIGH: ['critical', 'di', 'High'],
};
const RISK_RANK = { NONE: 0, LOW: 1, MEDIUM: 2, HIGH: 3 };
function riskBadge(level) {
  const [color, shape, label] = RISK[level] || RISK.HIGH;
  return `<span class="badge ${shape}"><i style="background:var(--${color})"></i>${label}</span>`;
}
const PLAY = { VENDOR: 'Sell to a vendor', FLIP: 'Relist under the next price', DIP: 'Hold for recovery' };

// ---------------------------------------------------------------- buy list --
async function viewBuy() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  view.innerHTML = `
    <h1>Buy list — ${esc(houseLabel(h))}</h1>
    <p class="lead">Each row is one trade inside this auction house: what to buy, the most to pay per unit,
      what to relist it for, and the profit after the house cut. Risk reflects how fast the item sells,
      how much history backs the estimate, and where the price is heading.</p>
    <div class="filters">
      <label>Budget (gold)<input id="f-budget" type="number" min="0" step="1" placeholder="any" value="${esc(F.budget)}"></label>
      <label>Min profit (silver)<input id="f-profit" type="number" min="0" step="1" value="${esc(F.minProfit)}"></label>
      <label>Highest risk<select id="f-risk">
        <option value="HIGH">Any</option><option value="MEDIUM">Medium or lower</option>
        <option value="LOW">Low or none</option><option value="NONE">None (vendor only)</option></select></label>
      <label>Play<select id="f-play"><option value="">All</option><option value="VENDOR">Vendor</option>
        <option value="FLIP">Flip</option><option value="DIP">Dip</option></select></label>
      <label>Sort by<select id="f-sort"><option value="profit">Profit</option><option value="roi">Return</option>
        <option value="risk">Lowest risk</option><option value="speed">Fastest sale</option></select></label>
      <label>Search<input id="f-q" type="search" placeholder="item name" value="${esc(F.q)}"></label>
    </div>
    <div id="buy-out"></div>`;
  $('#f-risk').value = F.risk; $('#f-play').value = F.play; $('#f-sort').value = F.sort;

  const draw = () => {
    const budget = F.budget === '' ? Infinity : Number(F.budget) * 10000;
    const minProfit = Number(F.minProfit || 0) * 100, q = F.q.trim().toLowerCase();
    let rows = d.signals.filter(s => s.buy_cost <= budget && s.profit >= minProfit
      && RISK_RANK[s.risk_level] <= RISK_RANK[F.risk] && (!F.play || s.strategy === F.play)
      && (!q || s.name.toLowerCase().includes(q)));
    const sorters = {
      profit: (a, b) => b.profit - a.profit, roi: (a, b) => b.roi - a.roi,
      risk: (a, b) => a.risk_score - b.risk_score || b.profit - a.profit,
      speed: (a, b) => (a.days_to_sell ?? 99) - (b.days_to_sell ?? 99) || b.profit - a.profit,
    };
    rows.sort(sorters[F.sort]);
    const total = rows.length;
    rows = rows.slice(0, 200);
    const out = $('#buy-out');
    if (!total) {
      const warming = h.n_snaps < S.meta.min_history_snaps;
      out.innerHTML = `<div class="note">${d.signals.length ? 'No trades match these filters.'
        : warming ? `Still collecting: ${h.n_snaps} of ${S.meta.min_history_snaps} hourly snapshots needed before flips can be judged. Vendor deals show up straight away; flips and dips need price history, and get more reliable over the first few days.`
        : 'No profitable trades found in the latest snapshot.'}</div>`;
      return;
    }
    out.innerHTML = `<p class="muted">${total} trade${total === 1 ? '' : 's'}${total > 200 ? ' (showing the top 200)' : ''}</p>
      <div class="table-wrap"><table>
      <thead><tr><th>Item</th><th>Play</th><th class="num">Buy</th><th class="num">Pay up to</th>
        <th class="num">Total cost</th><th class="num">Sell each at</th><th class="num">Profit</th>
        <th class="num">Return</th><th class="num">Sells in</th><th>Risk</th></tr></thead>
      <tbody>${rows.map(s => `<tr>
        <td class="item">${itemLink(s)}<span class="why">${esc(s.reasons.join(' · '))}</span></td>
        <td><span class="play" title="${esc(PLAY[s.strategy])}">${s.strategy}</span></td>
        <td class="num">${num(s.buy_qty)}</td><td class="num">${money(s.buy_max_price)}</td>
        <td class="num">${money(s.buy_cost)}</td><td class="num">${money(s.sell_price)}</td>
        <td class="num"><b>${money(s.profit)}</b></td><td class="num">${pct(s.roi)}</td>
        <td class="num">${sellsIn(s.days_to_sell)}</td><td>${riskBadge(s.risk_level)}</td></tr>`).join('')}
      </tbody></table></div>`;
  };
  const bind = (id, key) => $(id).addEventListener('input', e => { F[key] = e.target.value; draw(); });
  bind('#f-budget', 'budget'); bind('#f-profit', 'minProfit'); bind('#f-risk', 'risk');
  bind('#f-play', 'play'); bind('#f-sort', 'sort'); bind('#f-q', 'q');
  draw();
}

// ------------------------------------------------------------------ market --
async function viewMarket() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  const classes = [...new Set(d.items.map(x => x.item_class).filter(Boolean))].sort();
  view.innerHTML = `
    <h1>Market — ${esc(houseLabel(h))}</h1>
    <p class="lead">Every item currently listed. Use it to price something you want to sell:
      "Typical" is the median hourly price over the last week.</p>
    <div class="filters">
      <label>Search<input id="m-q" type="search" placeholder="item name" value="${esc(M.q)}"></label>
      <label>Type<select id="m-cls"><option value="">All</option>${classes.map(c => `<option>${esc(c)}</option>`).join('')}</select></label>
      <label>Sort by<select id="m-sort"><option value="sold">Most sold per day</option>
        <option value="discount">Furthest below typical</option><option value="price">Highest price</option>
        <option value="qty">Most stock</option></select></label>
    </div><div id="m-out"></div>`;
  $('#m-cls').value = M.cls; $('#m-sort').value = M.sort;
  const disc = x => x.ref_price ? x.min_price / x.ref_price - 1 : null;
  const draw = () => {
    const q = M.q.trim().toLowerCase();
    let rows = d.items.filter(x => (!q || x.name.toLowerCase().includes(q)) && (!M.cls || x.item_class === M.cls));
    const sorters = {
      sold: (a, b) => (b.sold_per_day ?? -1) - (a.sold_per_day ?? -1) || b.quantity - a.quantity,
      discount: (a, b) => (disc(a) ?? 9) - (disc(b) ?? 9),
      price: (a, b) => b.min_price - a.min_price, qty: (a, b) => b.quantity - a.quantity,
    };
    rows.sort(sorters[M.sort]);
    const total = rows.length; rows = rows.slice(0, 300);
    $('#m-out').innerHTML = `<p class="muted">${num(total)} item${total === 1 ? '' : 's'}${total > 300 ? ' (showing 300)' : ''}</p>
      <div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">Cheapest</th><th class="num">Typical</th>
      <th class="num">vs typical</th><th class="num">Trend / day</th><th class="num">Sold / day</th>
      <th class="num">In stock</th><th class="num">Listings</th></tr></thead><tbody>${rows.map(x => `<tr>
      <td class="item">${itemLink(x)}</td><td class="num">${money(x.min_price)}</td><td class="num">${money(x.ref_price)}</td>
      <td class="num">${pct(disc(x), true)}</td><td class="num">${pct(x.trend_per_day, true)}</td>
      <td class="num">${num(x.sold_per_day, 1)}</td><td class="num">${num(x.quantity)}</td><td class="num">${num(x.listings)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  };
  const bind = (id, key) => $(id).addEventListener('input', e => { M[key] = e.target.value; draw(); });
  bind('#m-q', 'q'); bind('#m-cls', 'cls'); bind('#m-sort', 'sort');
  draw();
}

// -------------------------------------------------------------------- item --
async function viewItem(variantId) {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  const x = d.byId.get(variantId);
  if (!x) {
    view.innerHTML = `<h1>Not listed</h1><p class="lead">That item has no listings in ${esc(houseLabel(h))} right now.
      <a href="#/market">Back to the market</a>.</p>`;
    return;
  }
  const chunk = await getJSON(`data/hist-${S.houseId}-${variantId % S.meta.history_chunks}.json`);
  const hist = chunk[variantId] || { t: [], p: [], m: [], s: [], q: [], ladder: [] };
  const sigs = d.signals.filter(s => s.variant_id === variantId);
  const vs = x.ref_price ? x.min_price / x.ref_price - 1 : null;
  view.innerHTML = `
    <h1 class="${qClass(x.quality)}">${esc(x.name)}</h1>
    <p class="lead">${esc([x.quality, x.item_class].filter(Boolean).join(' · '))}
      · <a href="https://www.wowhead.com/classic/item=${x.item_id}" target="_blank" rel="noopener">Wowhead</a>
      · ${esc(houseLabel(h))}</p>
    ${sigs.map(s => `<div class="advice">
      <p><b>Buy ${num(s.buy_qty)}</b> at up to <b>${money(s.buy_max_price)}</b> each (${money(s.buy_cost)} in total).
        ${s.strategy === 'VENDOR' ? `Sell to a vendor for <b>${money(s.sell_price)}</b> each.` : `Relist at <b>${money(s.sell_price)}</b> each.`}
        Expected profit <b>${money(s.profit)}</b> (${pct(s.roi)}) after fees.</p>
      <p>Risk: ${riskBadge(s.risk_level)} <span class="muted">— ${esc(s.reasons.join(' · '))}</span></p></div>`).join('')}
    <div class="tiles">
      <div class="tile"><div class="k">Cheapest now</div><div class="v">${money(x.min_price)}</div></div>
      <div class="tile"><div class="k">Typical price (7 days)</div><div class="v">${money(x.ref_price)}</div></div>
      <div class="tile"><div class="k">Now vs typical</div><div class="v">${pct(vs, true)}</div></div>
      <div class="tile"><div class="k">Sold per day (est.)</div><div class="v">${num(x.sold_per_day, 1)}</div></div>
      <div class="tile"><div class="k">In stock</div><div class="v">${num(x.quantity)}</div></div>
      <div class="tile"><div class="k">Vendor pays</div><div class="v">${money(x.vendor_sell)}</div></div>
    </div>
    <h2>Price history</h2>
    <div class="legend"><span style="--c:var(--series-1)">Market price (cheapest 15% of stock)</span>
      <span style="--c:var(--series-2)">Cheapest listing</span></div>
    <div class="chart" id="c-price"></div>
    <h2>Units sold per snapshot (estimated)</h2>
    <div class="chart" id="c-sold"></div>
    <details><summary>History as a table</summary><div class="table-wrap"><table><thead><tr><th>Snapshot</th>
      <th class="num">Market price</th><th class="num">Cheapest</th><th class="num">Sold</th><th class="num">In stock</th></tr></thead>
      <tbody>${hist.t.map((t, i) => `<tr><td>${clock(t)}</td><td class="num">${money(hist.p[i])}</td>
      <td class="num">${money(hist.m[i])}</td><td class="num">${num(hist.s[i])}</td><td class="num">${num(hist.q[i])}</td></tr>`).reverse().join('')}</tbody></table></div></details>
    <h2>Order book (cheapest first)</h2>
    <div class="table-wrap" style="max-width:420px"><table><thead><tr><th class="num">Price each</th><th class="num">Units</th>
      <th class="num">Cost to clear to here</th></tr></thead><tbody>${(() => {
        let run = 0;
        return (hist.ladder || []).map(([p, q]) => { run += p * q; return `<tr><td class="num">${money(p)}</td><td class="num">${num(q)}</td><td class="num">${money(run)}</td></tr>`; }).join('');
      })()}</tbody></table></div>`;
  if (hist.t.length < 2) {
    $('#c-price').innerHTML = $('#c-sold').innerHTML = '<p class="muted">Not enough snapshots to chart yet.</p>';
    return;
  }
  lineChart($('#c-price'), hist.t, [
    { name: 'Market price', color: 'var(--series-1)', values: hist.p },
    { name: 'Cheapest listing', color: 'var(--series-2)', values: hist.m },
  ]);
  barChart($('#c-sold'), hist.t, hist.s, 'Sold');
}

// ------------------------------------------------------------------ charts --
function niceTicks(lo, hi, n = 4) {
  if (hi <= lo) hi = lo + 1;
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(v);
  return ticks;
}
function frame(t, lo, hi, fmt) {
  const W = 800, H = 240, l = 48, r = 12, top = 10, b = 24;
  const x = v => l + (v - t[0]) / Math.max(t[t.length - 1] - t[0], 1) * (W - l - r);
  const y = v => top + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - top - b);
  let svg = '';
  for (const v of niceTicks(lo, hi)) {
    svg += `<line x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/>
            <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
  }
  const days = new Set();
  t.forEach(e => {
    const day = new Date(e * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    if (!days.has(day)) { days.add(day); if (days.size > 1 || t.length < 30) svg += `<text x="${x(e)}" y="${H - 6}">${day}</text>`; }
  });
  return { W, H, l, r, top, b, x, y, svg };
}
function hover(el, f, t, onIndex) {
  const svg = $('svg', el);
  svg.addEventListener('mousemove', ev => {
    const box = svg.getBoundingClientRect();
    const px = (ev.clientX - box.left) / box.width * f.W;
    let best = 0;
    t.forEach((e, i) => { if (Math.abs(f.x(e) - px) < Math.abs(f.x(t[best]) - px)) best = i; });
    tip.innerHTML = onIndex(best);
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; onIndex(-1); });
}
function lineChart(el, t, series) {
  const all = series.flatMap(s => s.values).filter(v => v != null);
  const lo0 = Math.min(...all), hi0 = Math.max(...all), pad = (hi0 - lo0) * 0.08 || hi0 * 0.05 || 1;
  const f = frame(t, Math.max(0, lo0 - pad), hi0 + pad, shortMoney);
  let svg = f.svg;
  for (const s of series) {
    let dpath = '', pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      dpath += `${pen ? 'L' : 'M'}${f.x(t[i]).toFixed(1)},${f.y(v).toFixed(1)}`; pen = true;
    });
    svg += `<path d="${dpath}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  svg += `<line id="xh" y1="${f.top}" y2="${f.H - f.b}" stroke="var(--text-3)" visibility="hidden"/>`;
  svg += series.map((s, i) => `<circle id="dot${i}" r="4" fill="${s.color}" stroke="var(--surface)" stroke-width="2" visibility="hidden"/>`).join('');
  el.innerHTML = `<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="Price history line chart">${svg}</svg>`;
  hover(el, f, t, i => {
    const xh = $('#xh', el);
    if (i < 0) { xh.setAttribute('visibility', 'hidden'); series.forEach((_, k) => $('#dot' + k, el).setAttribute('visibility', 'hidden')); return ''; }
    xh.setAttribute('x1', f.x(t[i])); xh.setAttribute('x2', f.x(t[i])); xh.setAttribute('visibility', 'visible');
    series.forEach((s, k) => {
      const dot = $('#dot' + k, el), v = s.values[i];
      if (v == null) { dot.setAttribute('visibility', 'hidden'); return; }
      dot.setAttribute('cx', f.x(t[i])); dot.setAttribute('cy', f.y(v)); dot.setAttribute('visibility', 'visible');
    });
    return `<b>${clock(t[i])}</b><br>` + series.map(s => `<span class="sw" style="background:${s.color}"></span>${s.name}: <b>${money(s.values[i])}</b>`).join('<br>');
  });
}
function barChart(el, t, values, label) {
  const f = frame(t, 0, Math.max(...values, 1), v => num(v));
  const slot = (f.W - f.l - f.r) / t.length, w = Math.max(1, Math.min(24, slot - 2));
  let svg = f.svg;
  values.forEach((v, i) => {
    if (!v) return;
    const top = f.y(v), hgt = f.y(0) - top;
    svg += `<rect data-i="${i}" x="${(f.x(t[i]) - w / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}" rx="${Math.min(4, w / 2, hgt)}" fill="var(--series-1)"/>`;
  });
  el.innerHTML = `<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="Units sold bar chart">${svg}</svg>`;
  hover(el, f, t, i => {
    el.querySelectorAll('rect').forEach(r => r.setAttribute('opacity', i < 0 || Number(r.dataset.i) === i ? 1 : 0.45));
    return i < 0 ? '' : `<b>${clock(t[i])}</b><br>${label}: <b>${num(values[i])}</b>`;
  });
}

// ----------------------------------------------------------- cross-faction --
async function viewCross() {
  const mine = houseMeta(S.houseId);
  const pick = faction => S.meta.houses.find(h => h.faction === faction && h.pinned)
    || S.meta.houses.find(h => h.faction === faction && h.slot === mine.slot)
    || S.meta.houses.find(h => h.faction === faction);
  const a = pick('alliance'), o = pick('horde');
  if (!a || !o) {
    view.innerHTML = '<h1>Cross-faction</h1><div class="note">Needs both an Alliance and a Horde house to be tracked.</div>';
    return;
  }
  const [da, dh] = await Promise.all([loadHouse(a.id), loadHouse(o.id)]);
  const cut = S.meta.fees.faction_cut, rows = [];
  for (const x of da.items) {
    const y = dh.byId.get(x.variant_id);
    if (!y || x.quantity < 5 || y.quantity < 5) continue;
    const [buy, sell, from, to] = x.min_price <= y.min_price ? [x, y, 'Alliance', 'Horde'] : [y, x, 'Horde', 'Alliance'];
    const net = Math.floor(sell.min_price * (1 - cut)) - buy.min_price;
    if (net <= 0 || !(sell.sold_per_day >= 1)) continue;
    rows.push({ x, from, to, buy: buy.min_price, sell: sell.min_price, net, gap: sell.min_price / buy.min_price - 1, spd: sell.sold_per_day });
  }
  rows.sort((p, q) => q.gap - p.gap);
  view.innerHTML = `<h1>Cross-faction price gaps</h1>
    <p class="lead">${esc(houseLabel(a))} against ${esc(houseLabel(o))}. Informational: moving goods between factions
      means going through the neutral Blackwater house (assumed ${pct(S.meta.fees.neutral_cut)} cut) or a second character,
      and that route is not confirmed for Forever. ${a.pinned && o.pinned ? '' : '<b>These two houses are not confirmed as the same ruleset yet</b> — see Status.'}</p>
    <div class="table-wrap"><table><thead><tr><th>Item</th><th>Buy on</th><th class="num">Cheapest there</th><th>Sell on</th>
    <th class="num">Cheapest there</th><th class="num">Gap</th><th class="num">Net per unit</th><th class="num">Sold / day (sell side)</th></tr></thead>
    <tbody>${rows.slice(0, 200).map(r => `<tr><td class="item">${itemLink(r.x)}</td><td>${r.from}</td><td class="num">${money(r.buy)}</td>
    <td>${r.to}</td><td class="num">${money(r.sell)}</td><td class="num">${pct(r.gap, true)}</td><td class="num">${money(r.net)}</td>
    <td class="num">${num(r.spd, 1)}</td></tr>`).join('') || '<tr><td colspan="8" class="muted">Nothing yet — sales history is needed on the selling side.</td></tr>'}</tbody></table></div>`;
}

// ------------------------------------------------------------------ status --
function viewStatus() {
  const m = S.meta, setup = Object.entries(m.setup || {});
  view.innerHTML = `<h1>Status</h1>
    <p class="lead">Region ${esc(m.region.toUpperCase())}, ruleset ${m.ruleset}. Dashboard built ${clock(m.generated_at)} (${ago(m.generated_at)}).
      Assumed fees: ${pct(m.fees.faction_cut)} cut (${pct(m.fees.neutral_cut)} at Blackwater), ${money(m.fees.min_deposit)} minimum deposit,
      ${m.fees.listing_hours}h listings — check these in game and correct <code>config.toml</code> if they differ.</p>
    <div class="table-wrap"><table><thead><tr><th>House</th><th>Faction</th><th>State</th><th class="num">Snapshots</th>
      <th>Latest data</th><th class="num">Items</th><th class="num">Trades found</th></tr></thead><tbody>
      ${m.houses.map(h => `<tr><td>#${h.id}</td><td>${cap(h.faction)}</td><td>${h.pinned ? 'Confirmed' : 'Candidate'}</td>
        <td class="num">${h.n_snaps}</td><td>${clock(h.published_at)} (${ago(h.published_at)})</td>
        <td class="num">${num(h.n_items)}</td><td class="num">${num(h.n_signals)}</td></tr>`).join('')}</tbody></table></div>
    ${setup.length ? `<h2>One-off setup: which house is yours?</h2>
      <div class="note">Blizzard's API lists every ruleset's auction houses without saying which is which. Open the auction house
      in game, search two or three of the items below, and see which column matches the cheapest price you see
      (prices are from the latest snapshot, so allow for some drift). Then set that house number in
      <code>config.toml</code> under <code>[houses]</code> and commit it. The other candidates stop being tracked.</div>
      ${setup.map(([faction, items]) => {
        const ids = items.length ? Object.keys(items[0].prices) : [];
        return `<h2>${cap(faction)}</h2>${items.length ? `<div class="table-wrap"><table><thead><tr><th>Item</th>
          ${ids.map(i => `<th class="num">House #${i}</th>`).join('')}</tr></thead><tbody>${items.map(it => `<tr><td>${esc(it.name)}</td>
          ${ids.map(i => `<td class="num">${money(it.prices[i])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">No comparison items yet.</p>'}`;
      }).join('')}` : ''}`;
}

// ------------------------------------------------------------------ router --
function freshness() {
  const h = houseMeta(S.houseId), el = $('#fresh');
  if (!h) { el.textContent = ''; return; }
  const stale = Date.now() / 1000 - h.published_at > 3 * 3600;
  el.textContent = `Prices as of ${clock(h.published_at)} (${ago(h.published_at)}), ${h.n_snaps} snapshot${h.n_snaps === 1 ? '' : 's'} collected.`
    + (stale ? ' Data is stale — the collector may have stopped.' : '');
  el.classList.toggle('stale', stale);
}
async function render() {
  const [, route = 'buy', arg] = location.hash.split('/');
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#/' + (route === 'item' ? 'market' : route)));
  tip.hidden = true;
  freshness();
  try {
    if (!S.meta.houses.length) {
      view.innerHTML = '<div class="note">No data collected yet. Run <code>forever-ah run</code>.</div>';
    } else if (route === 'market') await viewMarket();
    else if (route === 'item') await viewItem(Number(arg));
    else if (route === 'cross') await viewCross();
    else if (route === 'status') viewStatus();
    else await viewBuy();
  } catch (err) {
    view.innerHTML = `<div class="note">Could not load data: ${esc(err.message)}</div>`;
  }
}
async function start() {
  try {
    S.meta = await getJSON('data/meta.json');
  } catch (err) {
    view.innerHTML = `<div class="note">No dashboard data found (${esc(err.message)}). Run <code>forever-ah run</code> first.</div>`;
    return;
  }
  const order = { alliance: 0, horde: 1, blackwater: 2 };
  S.meta.houses.sort((a, b) => (b.pinned - a.pinned) || (order[a.faction] - order[b.faction]) || (a.id - b.id));
  const sel = $('#house');
  sel.innerHTML = S.meta.houses.map(h => `<option value="${h.id}">${esc(houseLabel(h))}</option>`).join('');
  let saved = null;
  try { saved = Number(localStorage.getItem('house')); } catch { /* storage unavailable */ }
  S.houseId = houseMeta(saved) ? saved : (S.meta.houses[0] || {}).id;
  sel.value = S.houseId;
  sel.addEventListener('change', () => {
    S.houseId = Number(sel.value);
    try { localStorage.setItem('house', S.houseId); } catch { /* storage unavailable */ }
    render();
  });
  addEventListener('hashchange', render);
  render();
}
start();
