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
// Snapshots land at about :25 past; item pages show them by the hour they belong to.
const toHour = e => Math.floor(e / 3600) * 3600;
const hourClock = e => new Date(toHour(e) * 1000).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
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
const PLAY = {
  VENDOR: 'Sell to a vendor', FLIP: 'Relist at the next price up', DIP: 'Hold for recovery',
  EXPORT: 'Buy here, list on Blackwater for the other faction',
  CRAFT: 'Buy the materials, make it, list it',
  DISENCHANT: 'Buy it, disenchant it, sell the materials',
};
const G = { q: '', how: '', sort: 'gold', minPrice: '' };                                  // farm filters
const sellCell = s => money(s.sell_price) + (s.strategy === 'VENDOR' ? ''
  : s.strategy === 'DISENCHANT' ? '<span class="why">in materials</span>' : `<span class="why">${
  [s.strategy === 'EXPORT' ? 'on Blackwater' : '', s.list_hours ? `list ${s.list_hours}h` : ''].filter(Boolean).join(' · ')}</span>`);
const buyCell = s => num(s.buy_qty) + (s.strategy === 'CRAFT' ? '<span class="why">to make</span>' : '');
const payCell = s => money(s.buy_max_price) + (s.strategy === 'CRAFT' ? '<span class="why">materials each</span>' : '');

// ---------------------------------------------------------------- buy list --
async function viewBuy() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  view.innerHTML = `
    <h1>Buy list — ${esc(houseLabel(h))}</h1>
    <p class="lead">Each row is one trade: what to buy in this auction house, the most to pay per unit,
      what to relist it for, and the profit after the house cut. EXPORT rows are bought here and listed on
      Blackwater instead; CRAFT rows are made from materials bought here; DISENCHANT rows are gear worth more as
      enchanting materials. Risk reflects how fast the item sells, how much history backs the estimate,
      and where the price is heading.</p>
    <div class="filters">
      <label>Budget (gold)<input id="f-budget" type="number" min="0" step="1" placeholder="any" value="${esc(F.budget)}"></label>
      <label>Min profit (silver)<input id="f-profit" type="number" min="0" step="1" value="${esc(F.minProfit)}"></label>
      <label>Highest risk<select id="f-risk">
        <option value="HIGH">Any</option><option value="MEDIUM">Medium or lower</option>
        <option value="LOW">Low or none</option><option value="NONE">None (vendor only)</option></select></label>
      <label>Play<select id="f-play"><option value="">All</option><option value="VENDOR">Vendor</option>
        <option value="FLIP">Flip</option><option value="DIP">Dip</option><option value="EXPORT">Export</option>
        <option value="CRAFT">Craft</option><option value="DISENCHANT">Disenchant</option></select></label>
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
        <td class="num">${buyCell(s)}</td><td class="num">${payCell(s)}</td>
        <td class="num">${money(s.buy_cost)}</td><td class="num">${sellCell(s)}</td>
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

// -------------------------------------------------------------------- farm --
// What is worth going out to get and posting: no buying involved.
async function viewFarm() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  const known = d.items.filter(x => x.how).length;
  const ways = [...new Set(d.items.filter(x => x.farmable && x.how).flatMap(x => x.how.split(', ')))].sort();
  view.innerHTML = `
    <h1>Farm — ${esc(houseLabel(h))}</h1>
    <p class="lead">Items you can go and get, ranked by how much gold the market pays out for them each day
      (typical price times estimated daily sales). "Days of stock" is what is listed against what sells:
      a low number means the market is hungry. Post at the cheapest current price: at one price the newest
      listing sells first. ${known < d.items.length * 0.8 ? `<b>How items are obtained is still being looked up</b>
      (${num(known)} of ${num(d.items.length)} known so far; a batch is added every hour).` : ''}</p>
    <div class="filters">
      <label>Search<input id="g-q" type="search" placeholder="item name" value="${esc(G.q)}"></label>
      <label>How you get it<select id="g-how"><option value="">Any</option>${ways.map(w => `<option>${esc(w)}</option>`).join('')}</select></label>
      <label>Worth at least (silver)<input id="g-min" type="number" min="0" step="1" placeholder="any" value="${esc(G.minPrice)}"></label>
      <label>Sort by<select id="g-sort"><option value="gold">Gold paid out per day</option><option value="price">Highest price</option>
        <option value="sold">Most sold per day</option><option value="stock">Fewest days of stock</option></select></label>
    </div><div id="g-out"></div>`;
  $('#g-how').value = G.how; $('#g-sort').value = G.sort;
  const price = x => x.ref_price || x.market_price;
  const gold = x => (x.sold_per_day || 0) * price(x);
  const stock = x => x.sold_per_day ? x.quantity / x.sold_per_day : null;
  const draw = () => {
    const q = G.q.trim().toLowerCase(), min = Number(G.minPrice || 0) * 100;
    let rows = d.items.filter(x => x.farmable && x.sold_per_day > 0 && price(x) >= min && !x.name.includes('[variant')
      && (!q || x.name.toLowerCase().includes(q)) && (!G.how || (x.how || '').split(', ').includes(G.how)));
    const sorters = {
      gold: (a, b) => gold(b) - gold(a), price: (a, b) => price(b) - price(a),
      sold: (a, b) => b.sold_per_day - a.sold_per_day, stock: (a, b) => (stock(a) ?? 1e9) - (stock(b) ?? 1e9) || gold(b) - gold(a),
    };
    rows.sort(sorters[G.sort]);
    const total = rows.length; rows = rows.slice(0, 300);
    $('#g-out').innerHTML = total ? `<p class="muted">${num(total)} item${total === 1 ? '' : 's'}${total > 300 ? ' (showing 300)' : ''}</p>
      <div class="table-wrap"><table><thead><tr><th>Item</th><th>How you get it</th><th class="num">Post at</th>
      <th class="num">Typical</th><th class="num">Trend / day</th><th class="num">Sold / day</th>
      <th class="num">Gold paid out / day</th><th class="num">Days of stock</th></tr></thead><tbody>${rows.map(x => `<tr>
      <td class="item">${itemLink(x)}</td><td>${esc(x.how)}</td><td class="num">${money(x.min_price)}</td>
      <td class="num">${money(x.ref_price)}</td><td class="num">${pct(x.trend_per_day, true)}</td>
      <td class="num">${num(x.sold_per_day, 1)}</td><td class="num">${money(gold(x))}</td>
      <td class="num">${stock(x) == null ? '–' : num(stock(x), 1)}</td></tr>`).join('')}</tbody></table></div>`
      : `<div class="note">${d.items.some(x => x.sold_per_day > 0) ? 'Nothing matches these filters.'
        : `Still collecting: sales rates need 6 hours of history, which is 7 hourly snapshots in a row. ${h.n_snaps} collected so far.`}</div>`;
  };
  const bind = (id, key) => $(id).addEventListener('input', e => { G[key] = e.target.value; draw(); });
  bind('#g-q', 'q'); bind('#g-how', 'how'); bind('#g-min', 'minPrice'); bind('#g-sort', 'sort');
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
  hist.t = hist.t.map(toHour);
  const sigs = d.signals.filter(s => s.variant_id === variantId);
  const vs = x.ref_price ? x.min_price / x.ref_price - 1 : null;
  view.innerHTML = `
    <h1 class="${qClass(x.quality)}">${esc(x.name)}</h1>
    <p class="lead">${esc([x.quality, x.item_class].filter(Boolean).join(' · '))}
      · <a href="https://www.wowhead.com/forever/item=${x.item_id}" target="_blank" rel="noopener">Wowhead</a>
      · ${esc(houseLabel(h))}</p>
    ${sigs.map(s => `<div class="advice">
      <p>${s.strategy === 'CRAFT'
          ? `<b>Make ${num(s.buy_qty)}</b> for about <b>${money(s.buy_max_price)}</b> each in materials (${money(s.buy_cost)} in total).`
          : `<b>Buy ${num(s.buy_qty)}</b> at up to <b>${money(s.buy_max_price)}</b> each (${money(s.buy_cost)} in total).`}
        ${s.strategy === 'DISENCHANT' ? `Disenchant them: each gives about <b>${money(s.sell_price)}</b> of materials on average.` :
          s.strategy === 'VENDOR' ? `Sell to a vendor for <b>${money(s.sell_price)}</b> each.`
          : s.strategy === 'EXPORT' ? `List on the Blackwater house at <b>${money(s.sell_price)}</b> each${s.list_hours ? ` for ${s.list_hours}h` : ''}.`
          : `Relist at <b>${money(s.sell_price)}</b> each${s.list_hours ? ` for ${s.list_hours}h` : ''}.`}
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
    <h2>Units sold per hour (estimated)</h2>
    <div class="chart" id="c-sold"></div>
    <details><summary>History as a table</summary><div class="table-wrap"><table><thead><tr><th>Hour</th>
      <th class="num">Market price</th><th class="num">Cheapest</th><th class="num">Sold</th><th class="num">In stock</th></tr></thead>
      <tbody>${hist.t.map((t, i) => `<tr><td>${hourClock(t)}</td><td class="num">${money(hist.p[i])}</td>
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
  // Whole-hour marks along the bottom, at most about eight; midnight shows the date instead.
  const span = (t[t.length - 1] - t[0]) / 3600;
  const step = [1, 2, 3, 6, 12, 24, 48, 96].find(s => span / s <= 8) || 168;
  for (let e = toHour(t[0]); e <= t[t.length - 1]; e += 3600) {
    const d = new Date(e * 1000);
    if (e < t[0] || d.getHours() % Math.min(step, 24) || (step > 24 && Math.round(e / 86400) % (step / 24))) continue;
    const label = d.getHours() === 0 ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
      : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    svg += `<line x1="${x(e)}" x2="${x(e)}" y1="${H - b}" y2="${H - b + 4}" stroke="var(--grid)"/>
            <text x="${x(e)}" y="${H - 6}" text-anchor="${x(e) > W - r - 20 ? 'end' : 'middle'}">${label}</text>`;
  }
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
    return `<b>${hourClock(t[i])}</b><br>` + series.map(s => `<span class="sw" style="background:${s.color}"></span>${s.name}: <b>${money(s.values[i])}</b>`).join('<br>');
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
    return i < 0 ? '' : `<b>${hourClock(t[i])}</b><br>${label}: <b>${num(values[i])}</b>`;
  });
}

// ----------------------------------------------------------- cross-faction --
// Every EXPORT trade, both directions: buy in one faction's house, list on
// Blackwater for the other faction to buy.
async function viewCross() {
  const sources = S.meta.houses.filter(h => h.pinned && h.faction !== 'blackwater');
  const dest = S.meta.houses.find(h => h.pinned && h.faction === 'blackwater');
  if (!dest || !sources.length) {
    view.innerHTML = `<h1>Cross-faction</h1><div class="note">Needs a confirmed Blackwater house and at least one
      confirmed faction house — see Status.</div>`;
    return;
  }
  const loaded = await Promise.all(sources.map(h => loadHouse(h.id)));
  const rows = loaded.flatMap((d, i) => d.signals.filter(s => s.strategy === 'EXPORT')
    .map(s => ({ ...s, from: cap(sources[i].faction) })));
  rows.sort((a, b) => b.profit - a.profit);
  const fees = S.meta.fees;
  view.innerHTML = `<h1>Cross-faction: sell through Blackwater</h1>
    <p class="lead">Buy in a faction house, list on Blackwater for the other faction to buy. A trade only appears when the
      item has actually been selling on Blackwater, the price is no higher than the buyers pay on their own house, and the
      profit survives the ${pct(fees.neutral_cut)} cut and one lost deposit. Blackwater is a thin market, so treat these
      as riskier than a flip.</p>
    ${rows.length ? `<p class="muted">${rows.length} trade${rows.length === 1 ? '' : 's'}${rows.length > 200 ? ' (showing the top 200)' : ''}</p>
    <div class="table-wrap"><table><thead><tr><th>Item</th><th>Buy on</th><th class="num">Buy</th><th class="num">Pay up to</th>
      <th class="num">Total cost</th><th class="num">List on Blackwater at</th><th class="num">Profit</th>
      <th class="num">Return</th><th class="num">Sells in</th><th>Risk</th></tr></thead>
    <tbody>${rows.slice(0, 200).map(s => `<tr>
      <td class="item">${itemLink(s)}<span class="why">${esc(s.reasons.join(' · '))}</span></td><td>${s.from}</td>
      <td class="num">${num(s.buy_qty)}</td><td class="num">${money(s.buy_max_price)}</td>
      <td class="num">${money(s.buy_cost)}</td><td class="num">${money(s.sell_price)}${s.list_hours ? `<span class="why">list ${s.list_hours}h</span>` : ''}</td>
      <td class="num"><b>${money(s.profit)}</b></td><td class="num">${pct(s.roi)}</td>
      <td class="num">${sellsIn(s.days_to_sell)}</td><td>${riskBadge(s.risk_level)}</td></tr>`).join('')}</tbody></table></div>`
    : `<div class="note">${dest.n_snaps < 7
        ? `Still collecting: about 6 hours of Blackwater sales history are needed before these can be judged (${dest.n_snaps} hourly snapshot${dest.n_snaps === 1 ? '' : 's'} so far).`
        : 'No cross-faction trades clear the cut and deposit in the latest snapshot.'}</div>`}`;
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
      }).join('')}` : ''}
    ${scorecardHtml(m.scorecard)}
    <h2>When things sell — ${esc(houseLabel(houseMeta(S.houseId)))}</h2>
    <p class="muted">Average value sold in the hour up to each snapshot, in your local time. List shortly before the busy
      hours: at one price the newest listing sells first. It takes about a week of data to settle.</p>
    <div class="chart" id="c-hours"></div>
    <details><summary>Sales by hour as a table</summary><div id="t-hours"></div></details>
    ${ratesHtml(m.houses)}`;
  hourChart($('#c-hours'), $('#t-hours'), houseMeta(S.houseId).sales_by_hour || []);
}

function scorecardHtml(sc) {
  const head = `<h2>Scorecard: did past recommendations work?</h2>
    <p class="muted">Each recommendation is logged when first made, then checked against what the market did next.
      "Sold at the price" means at least the suggested quantity changed hands at the suggested sell price (within 3%).
      It is strict for flips: the play assumes you bought out the cheap listings, and if nobody did, buyers kept taking those.
      "Enough buyers" is the looser test: that many units sold at any price.</p>`;
  if (!sc || !sc.rows.length) {
    return head + `<div class="note">Nothing scored yet${sc && sc.pending ? ` — ${num(sc.pending)} recommendation${sc.pending === 1 ? ' is' : 's are'} being watched` : ''}.
      A recommendation is scored once twice its predicted selling time has passed (at least 6 hours).</div>`;
  }
  const share = (a, b) => b ? pct(a / b) : '–';
  return head + `<div class="table-wrap"><table><thead><tr><th>Play</th><th>Risk rating</th><th class="num">Checked</th>
    <th class="num">Sold at the price, on time</th><th class="num">Sold at the price, late</th><th class="num">Not sold at the price</th>
    <th class="num">Enough buyers at any price</th></tr></thead><tbody>${sc.rows.map(r => `<tr><td>${esc(r.strategy)}</td>
    <td>${riskBadge(r.risk_level)}</td><td class="num">${num(r.checked)}</td><td class="num">${share(r.on_time, r.checked)}</td>
    <td class="num">${share(r.late, r.checked)}</td><td class="num">${share(r.missed, r.checked)}</td>
    <td class="num">${share(r.enough_buyers, r.checked)}</td></tr>`).join('')}</tbody></table></div>
    <p class="muted">${num(sc.pending)} more being watched. Results older than ${sc.keep_days} days are dropped.</p>`;
}

function ratesHtml(houses) {
  const rows = houses.filter(h => h.rates);
  if (!rows.length) return '';
  const cell = (h, k) => `<td class="num">${pct(h.rates[k])}</td>`;
  return `<h2>Base rates behind the sales estimates</h2>
    <p class="muted">Chances per hour, re-measured from every hourly snapshot still held. They decide how much of an unexplained
      disappearance counts as a sale. A house with too few listings observed keeps the starting values.</p>
    <div class="table-wrap"><table><thead><tr><th>House</th><th>Kind</th><th class="num">Front listing sells</th>
      <th class="num">Withdrawn quietly</th><th class="num">Expired listings reposted identical</th>
      <th class="num">Listings observed</th></tr></thead><tbody>${rows.map(h => ['stack', 'gear'].map(k => `<tr>
      <td>${esc(houseLabel(h))}</td><td>${k === 'stack' ? 'Stackable goods' : 'Gear'}</td>${cell(h, k + '_sale')}${cell(h, k + '_cancel')}
      ${cell(h, k + '_repost')}<td class="num">${num((h.rates.sample || {})[k] || 0)}</td></tr>`).join('')).join('')}</tbody></table></div>`;
}

// Value sold by hour of day. Hours arrive in UTC and are shown in the viewer's local time.
function hourChart(el, tableEl, byHour) {
  if (!byHour.length) {
    el.innerHTML = '<p class="muted">No hourly sales recorded yet.</p>';
    tableEl.innerHTML = '';
    return;
  }
  const local = Array.from({ length: 24 }, () => null);
  for (const r of byHour) {
    const d = new Date(Date.UTC(2026, 0, 1, r.hour));
    local[d.getHours()] = r;
  }
  const W = 800, H = 240, l = 48, r = 12, top = 10, b = 24;
  const hi = Math.max(...byHour.map(x => x.value), 1);
  const y = v => top + (1 - v / hi) * (H - top - b), slot = (W - l - r) / 24, w = slot - 4;
  let svg = '';
  for (const v of niceTicks(0, hi)) {
    svg += `<line x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/>
            <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${shortMoney(v)}</text>`;
  }
  local.forEach((x, h) => {
    const cx = l + slot * h + slot / 2;
    if (h % 3 === 0) svg += `<text x="${cx}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, '0')}:00</text>`;
    if (!x) return;
    const t = y(x.value), hgt = y(0) - t;
    svg += `<rect data-h="${h}" x="${(cx - w / 2).toFixed(1)}" y="${t.toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}" rx="${Math.min(4, hgt)}" fill="var(--series-1)"/>`;
  });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Value sold by hour of day, bar chart">${svg}</svg>`;
  const svgEl = $('svg', el);
  svgEl.addEventListener('mousemove', ev => {
    const box = svgEl.getBoundingClientRect();
    const h = Math.floor(((ev.clientX - box.left) / box.width * W - l) / slot), x = local[h];
    el.querySelectorAll('rect').forEach(q => q.setAttribute('opacity', Number(q.dataset.h) === h ? 1 : 0.45));
    if (!x) { tip.hidden = true; return; }
    tip.innerHTML = `<b>${String(h).padStart(2, '0')}:00</b><br>Sold: <b>${money(x.value)}</b> an hour<br>${x.snapshots} snapshot${x.snapshots === 1 ? '' : 's'}`;
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  });
  svgEl.addEventListener('mouseleave', () => { tip.hidden = true; el.querySelectorAll('rect').forEach(q => q.setAttribute('opacity', 1)); });
  tableEl.innerHTML = `<div class="table-wrap" style="max-width:420px"><table><thead><tr><th>Hour (local)</th>
    <th class="num">Value sold per hour</th><th class="num">Snapshots</th></tr></thead><tbody>${local.map((x, h) => x
      ? `<tr><td>${String(h).padStart(2, '0')}:00</td><td class="num">${money(x.value)}</td><td class="num">${x.snapshots}</td></tr>` : '').join('')}</tbody></table></div>`;
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
    else if (route === 'farm') await viewFarm();
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
  order[S.meta.home_faction] = -1;   // the player's own faction opens first
  S.meta.houses.sort((a, b) => (b.pinned - a.pinned) || (order[a.faction] - order[b.faction]) || (a.id - b.id));
  const sel = $('#house');
  sel.innerHTML = S.meta.houses.map(h => `<option value="${h.id}">${esc(houseLabel(h))}</option>`).join('');
  let saved = null;
  try { saved = Number(localStorage.getItem('house')); } catch { /* storage unavailable */ }
  const asked = Number(new URLSearchParams(location.search).get('house'));   // ?house=110 for shareable links
  S.houseId = houseMeta(asked) ? asked : houseMeta(saved) ? saved : (S.meta.houses[0] || {}).id;
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
