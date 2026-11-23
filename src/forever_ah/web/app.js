'use strict';
// Static dashboard: reads the JSON files written by `forever-ah run`. No server, no build step.

const $ = (s, root = document) => root.querySelector(s);
const view = $('#view'), tip = $('#tip');
const S = { meta: null, houseId: null, cache: {} };
const F = { budget: '', minProfit: 10, risk: 'HIGH', play: '', q: '', sort: 'profit', cat: '' };   // buy list filters
const M = { q: '', sort: 'sold', cat: '', show: '' };                                             // market filters

// The auction house's own top-level categories -> the item classes in the data.
const CATS = {
  'Weapons': ['Weapon'], 'Armor': ['Armor'], 'Containers': ['Container'], 'Consumables': ['Consumable'],
  'Trade Goods': ['Trade Goods', 'Reagent'], 'Ammo': ['Projectile', 'Quiver'], 'Recipes': ['Recipe'],
  'Quest Items': ['Quest', 'Key'], 'Miscellaneous': ['Miscellaneous', null, undefined, ''],
};
const inCat = (cls, cat) => !cat || (CATS[cat] || []).includes(cls);
const catSelect = id => `<label>Category<select id="${id}"><option value="">All</option>${
  Object.keys(CATS).map(c => `<option>${c}</option>`).join('')}</select></label>`;

// Your professions, as the game has them: two primary professions (each a profession, a skill level
// and a specialisation) and the three secondary ones (a skill level each, 0 = not learned), plus the
// recipes you have ticked as known. Remembered in this browser only.
const SECONDARY = ['Cooking', 'First Aid', 'Fishing'];
const PROFILE = { slots: [{ prof: '', skill: 300, spec: '' }, { prof: '', skill: 300, spec: '' }],
  secondary: { Cooking: 0, 'First Aid': 0, Fishing: 0 }, known: [], onlyKnown: false, view: 'overview', focus: '' };
try { Object.assign(PROFILE, JSON.parse(localStorage.getItem('fah-profile') || '{}')); } catch { /* private window */ }
PROFILE.secondary = { Cooking: 0, 'First Aid': 0, Fishing: 0, ...PROFILE.secondary };
// An earlier version let a secondary profession take a primary slot: move it across.
PROFILE.slots.forEach(s => { if (SECONDARY.includes(s.prof)) { PROFILE.secondary[s.prof] = s.skill; s.prof = ''; s.spec = ''; } });
PROFILE.knownSet = new Set(PROFILE.known);
function saveProfile() {
  const { knownSet, ...rest } = PROFILE;
  try { localStorage.setItem('fah-profile', JSON.stringify({ ...rest, known: [...knownSet] })); } catch { /* not remembered */ }
}
const hasProfile = () => PROFILE.slots.some(s => s.prof) || SECONDARY.some(p => PROFILE.secondary[p] > 0);
function slotFor(prof) {
  if (SECONDARY.includes(prof)) {
    const skill = PROFILE.secondary[prof] || 0;
    return skill > 0 ? { prof, skill, spec: '' } : undefined;
  }
  return PROFILE.slots.find(s => s.prof && s.prof === prof);
}
const myProfs = () => [...PROFILE.slots.filter(s => s.prof).map(s => s.prof), ...SECONDARY.filter(p => PROFILE.secondary[p] > 0)];
// Whether your profile can make a recipe: right profession, enough skill, the specialisation, and
// (if you asked for it) ticked as known.
function canMake(prof, need, spec, spell) {
  const slot = slotFor(prof);
  if (!slot) return false;
  if (need != null && slot.skill < need) return false;
  if (spec && slot.spec && slot.spec !== spec) return false;
  if (PROFILE.onlyKnown && spell != null && !PROFILE.knownSet.has(spell)) return false;
  return true;
}
// Buy list rows that need a profession: shown only if your profile covers them (or no profile is set).
function profileAllows(s) {
  if (!hasProfile()) return true;
  if (s.strategy === 'DISENCHANT') return !!slotFor('Enchanting');
  if (s.strategy === 'CRAFT' || s.strategy === 'CRAFTVEND') return canMake(s.profession, s.need, s.spec, s.spell);
  return true;
}
// The house's daily price cycle (24 UTC values, share above or below the day's average) in local hours.
function localCycle(utc) {
  const local = Array.from({ length: 24 }, () => null);
  (utc || []).forEach((v, hour) => { local[new Date(Date.UTC(2026, 0, 1, hour)).getHours()] = v; });
  return local;
}
const hh = h => String(h).padStart(2, '0') + ':00';
function cycleNote(utc) {
  const local = localCycle(utc), known = local.map((v, h) => [v, h]).filter(x => x[0] != null);
  if (known.length < 20) return '';
  const low = known.reduce((a, b) => b[0] < a[0] ? b : a), high = known.reduce((a, b) => b[0] > a[0] ? b : a);
  const now = local[new Date().getHours()];
  const state = now == null ? '' : now <= -0.02 ? ` Right now they are typically <b>${pct(-now)} below</b> the day's average: a better time to buy than to list.`
    : now >= 0.02 ? ` Right now they are typically <b>${pct(now)} above</b> the day's average: a better time to list than to buy.`
    : ' Right now they are close to the day\'s average.';
  return `<div class="note"><b>Daily cycle.</b> Busy stackable goods in this house are usually cheapest around
    ${hh(low[1])} (${pct(low[0], true)}) and dearest around ${hh(high[1])} (${pct(high[0], true)}), your local time.${state}</div>`;
}

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
const dayLabel = e => new Date(e * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
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
// How trades of the same play and risk rating have done over the last few days (the strict, net-of-competition test).
function trackRecord(s) {
  const r = ((S.meta.scorecard || {}).rows || []).find(x => x.strategy === s.strategy && x.risk_level === s.risk_level);
  if (!r || r.checked < 20 || r.net == null) return '';
  return `<span class="why" title="Of the last ${num(r.checked)} ${s.strategy} trades rated ${RISK[s.risk_level][2].toLowerCase()} risk, this share sold at the price even after other sellers posted at the same price">${pct(r.net / r.checked)} sold</span>`;
}
function riskBadge(level) {
  const [color, shape, label] = RISK[level] || RISK.HIGH;
  return `<span class="badge ${shape}"><i style="background:var(--${color})"></i>${label}</span>`;
}
const PLAY = {
  VENDOR: 'Sell to a vendor', FLIP: 'Relist at the next price up', DIP: 'Hold for recovery',
  EXPORT: 'Buy here, list on Blackwater for the other faction',
  CRAFT: 'Buy the materials, make it, list it',
  DISENCHANT: 'Buy it, disenchant it, sell the materials',
  CRAFTVEND: 'Buy the materials, make it, sell it to a vendor',
};
const PLAY_LABEL = { CRAFTVEND: 'CRAFT · VENDOR' };
const isCraft = s => s.strategy === 'CRAFT' || s.strategy === 'CRAFTVEND';
const G = { q: '', how: '', sort: 'gold', minPrice: '', cat: '' };                                  // farm filters
const sellCell = s => money(s.sell_price) + (s.strategy === 'VENDOR' ? ''
  : s.strategy === 'CRAFTVEND' ? '<span class="why">to a vendor</span>'
  : s.strategy === 'DISENCHANT' ? '<span class="why">in materials</span>' : `<span class="why">${
  [s.strategy === 'EXPORT' ? 'on Blackwater' : '', s.list_hours ? `list ${s.list_hours}h` : ''].filter(Boolean).join(' · ')}</span>`);
const buyCell = s => num(s.buy_qty) + (isCraft(s) ? '<span class="why">to make</span>' : '');
const payCell = s => money(s.buy_max_price) + (isCraft(s) ? '<span class="why">materials each</span>' : '');

// ---------------------------------------------------------------- buy list --
async function viewBuy() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  view.innerHTML = `
    <h1>Buy list — ${esc(houseLabel(h))}</h1>
    <p class="lead">Each row is one trade: what to buy in this auction house, the most to pay per unit,
      what to relist it for, and the profit after the house cut. EXPORT rows are bought here and listed on
      Blackwater instead; CRAFT rows are made from materials bought here (CRAFT · VENDOR rows are then sold
      to a vendor, not listed); DISENCHANT rows are gear worth more as
      enchanting materials. Risk reflects how fast the item sells, how much history backs the estimate,
      and where the price is heading. The small figure under each risk rating is the track record: how often
      trades of that play and rating actually sold at the price over the last few days.</p>
    <div class="filters">
      <label>Budget (gold)<input id="f-budget" type="number" min="0" step="1" placeholder="any" value="${esc(F.budget)}"></label>
      <label>Min profit (silver)<input id="f-profit" type="number" min="0" step="1" value="${esc(F.minProfit)}"></label>
      <label>Highest risk<select id="f-risk">
        <option value="HIGH">Any</option><option value="MEDIUM">Medium or lower</option>
        <option value="LOW">Low or none</option><option value="NONE">None (vendor only)</option></select></label>
      <label>Play<select id="f-play"><option value="">All</option><option value="VENDOR">Vendor</option>
        <option value="FLIP">Flip</option><option value="DIP">Dip</option><option value="EXPORT">Export</option>
        <option value="CRAFT">Craft</option><option value="CRAFTVEND">Craft and vendor</option>
        <option value="DISENCHANT">Disenchant</option></select></label>
      <label>Sort by<select id="f-sort"><option value="profit">Profit</option><option value="roi">Return</option>
        <option value="risk">Lowest risk</option><option value="speed">Fastest sale</option></select></label>
      ${catSelect('f-cat')}
      <label>Search<input id="f-q" type="search" placeholder="item name" value="${esc(F.q)}"></label>
    </div>
    <p class="chips"><span>Your professions:</span> ${esc(profileSummary())}
      <a href="#/professions">${hasProfile() ? 'Change' : 'Set them'}</a>
      <span class="muted">${hasProfile() ? 'Crafts and disenchants are limited to what you can make.' : 'Set them to see only crafts you can make.'}</span></p>
    ${cycleNote(h.price_by_hour)}
    <div id="buy-out"></div>`;
  $('#f-risk').value = F.risk; $('#f-play').value = F.play; $('#f-sort').value = F.sort; $('#f-cat').value = F.cat;

  const draw = () => {
    const budget = F.budget === '' ? Infinity : Number(F.budget) * 10000;
    const minProfit = Number(F.minProfit || 0) * 100, q = F.q.trim().toLowerCase();
    let rows = d.signals.filter(s => s.buy_cost <= budget && s.profit >= minProfit
      && RISK_RANK[s.risk_level] <= RISK_RANK[F.risk] && (!F.play || s.strategy === F.play)
      && inCat(s.item_class, F.cat)
      && profileAllows(s)
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
        <td><span class="play" title="${esc(PLAY[s.strategy])}">${PLAY_LABEL[s.strategy] || s.strategy}</span>${isCraft(s) && s.profession
          ? `<span class="why">${esc(s.profession)}${s.need != null ? ` ${s.exact ? '' : '≈'}${s.need}` : ''}${s.spec ? ` · ${esc(s.spec)}` : ''}</span>` : ''}</td>
        <td class="num">${buyCell(s)}</td><td class="num">${payCell(s)}</td>
        <td class="num">${money(s.buy_cost)}</td><td class="num">${sellCell(s)}</td>
        <td class="num"><b>${money(s.profit)}</b></td><td class="num">${pct(s.roi)}</td>
        <td class="num">${sellsIn(s.days_to_sell)}</td><td>${riskBadge(s.risk_level)}${trackRecord(s)}</td></tr>`).join('')}
      </tbody></table></div>`;
  };
  const bind = (id, key) => $(id).addEventListener('input', e => { F[key] = e.target.value; draw(); });
  bind('#f-budget', 'budget'); bind('#f-profit', 'minProfit'); bind('#f-risk', 'risk');
  bind('#f-play', 'play'); bind('#f-sort', 'sort'); bind('#f-q', 'q'); bind('#f-cat', 'cat');
  draw();
}

// ------------------------------------------------------------------ market --
async function viewMarket() {
  const h = houseMeta(S.houseId), d = await loadHouse(S.houseId);
  view.innerHTML = `
    <h1>Market — ${esc(houseLabel(h))}</h1>
    <p class="lead">Every item currently listed. Use it to price something you want to sell:
      "Typical" is the median hourly price over the last week.</p>
    <div class="filters">
      <label>Search<input id="m-q" type="search" placeholder="item name" value="${esc(M.q)}"></label>
      ${catSelect('m-cat')}
      <label>Show<select id="m-show"><option value="">Everything</option>
        <option value="short">Shortages: selling fast, little stock</option>
        <option value="glut">Gluts: days of stock, price falling</option></select></label>
      <label>Sort by<select id="m-sort"><option value="sold">Most sold per day</option>
        <option value="gold">Most gold traded per day</option>
        <option value="discount">Furthest below typical</option><option value="price">Highest price</option>
        <option value="qty">Most stock</option></select></label>
    </div><div id="m-note"></div><div id="m-out"></div>`;
  $('#m-cat').value = M.cat; $('#m-sort').value = M.sort; $('#m-show').value = M.show;
  const disc = x => x.ref_price ? x.min_price / x.ref_price - 1 : null;
  const gold = x => (x.sold_per_day || 0) * (x.ref_price || x.market_price || 0);
  const stockDays = x => x.sold_per_day > 0 ? x.quantity / x.sold_per_day : null;
  // Only items with a real market count: 50g+ traded a day and 20+ observed sales.
  const busy = x => gold(x) >= 500000 && x.total_sold >= 20 && !x.name.includes('[variant');
  const SHOW = {
    short: x => busy(x) && stockDays(x) < 0.25 && x.ref_price && x.min_price >= x.ref_price,
    glut: x => busy(x) && stockDays(x) > 3 && x.trend_per_day != null && x.trend_per_day < -0.05,
  };
  const NOTE = {
    short: 'Under six hours of stock on sale and the price at or above typical: good things to farm, craft or bring to market now.',
    glut: 'Over three days of stock on sale and the price falling more than 5% a day: avoid buying these to resell, and expect a lower price if you sell.',
  };
  const draw = () => {
    const q = M.q.trim().toLowerCase();
    let rows = d.items.filter(x => (!q || x.name.toLowerCase().includes(q)) && inCat(x.item_class, M.cat)
      && (!M.show || SHOW[M.show](x)));
    $('#m-note').innerHTML = M.show ? `<div class="note">${NOTE[M.show]}</div>` : '';
    const sorters = {
      sold: (a, b) => (b.sold_per_day ?? -1) - (a.sold_per_day ?? -1) || b.quantity - a.quantity,
      gold: (a, b) => gold(b) - gold(a),
      discount: (a, b) => (disc(a) ?? 9) - (disc(b) ?? 9),
      price: (a, b) => b.min_price - a.min_price, qty: (a, b) => b.quantity - a.quantity,
    };
    rows.sort(sorters[M.sort]);
    const total = rows.length; rows = rows.slice(0, 300);
    $('#m-out').innerHTML = `<p class="muted">${num(total)} item${total === 1 ? '' : 's'}${total > 300 ? ' (showing 300)' : ''}</p>
      <div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">Cheapest</th><th class="num">Typical</th>
      <th class="num">vs typical</th><th class="num">Trend / day</th><th class="num">Sold / day</th>
      <th class="num">In stock</th><th class="num">Days of stock</th><th class="num">Listings</th></tr></thead><tbody>${rows.map(x => `<tr>
      <td class="item">${itemLink(x)}</td><td class="num">${money(x.min_price)}</td><td class="num">${money(x.ref_price)}</td>
      <td class="num">${pct(disc(x), true)}</td><td class="num">${pct(x.trend_per_day, true)}</td>
      <td class="num">${num(x.sold_per_day, 1)}</td><td class="num">${num(x.quantity)}</td>
      <td class="num">${stockDays(x) == null ? '–' : num(stockDays(x), 1)}</td><td class="num">${num(x.listings)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  };
  const bind = (id, key) => $(id).addEventListener('input', e => { M[key] = e.target.value; draw(); });
  bind('#m-q', 'q'); bind('#m-cat', 'cat'); bind('#m-sort', 'sort'); bind('#m-show', 'show');
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
      ${catSelect('g-cat')}
      <label>Worth at least (silver)<input id="g-min" type="number" min="0" step="1" placeholder="any" value="${esc(G.minPrice)}"></label>
      <label>Sort by<select id="g-sort"><option value="gold">Gold paid out per day</option><option value="price">Highest price</option>
        <option value="sold">Most sold per day</option><option value="stock">Fewest days of stock</option></select></label>
    </div><div id="g-out"></div>`;
  $('#g-how').value = G.how; $('#g-sort').value = G.sort; $('#g-cat').value = G.cat;
  const price = x => x.ref_price || x.market_price;
  const gold = x => (x.sold_per_day || 0) * price(x);
  const stock = x => x.sold_per_day ? x.quantity / x.sold_per_day : null;
  const draw = () => {
    const q = G.q.trim().toLowerCase(), min = Number(G.minPrice || 0) * 100;
    let rows = d.items.filter(x => x.farmable && x.sold_per_day > 0 && price(x) >= min && !x.name.includes('[variant')
      && inCat(x.item_class, G.cat)
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
  bind('#g-q', 'q'); bind('#g-how', 'how'); bind('#g-min', 'minPrice'); bind('#g-sort', 'sort'); bind('#g-cat', 'cat');
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
      <p>${isCraft(s)
          ? `<b>Make ${num(s.buy_qty)}</b> for about <b>${money(s.buy_max_price)}</b> each in materials (${money(s.buy_cost)} in total).`
          : `<b>Buy ${num(s.buy_qty)}</b> at up to <b>${money(s.buy_max_price)}</b> each (${money(s.buy_cost)} in total).`}
        ${s.strategy === 'DISENCHANT' ? `Disenchant them: each gives about <b>${money(s.sell_price)}</b> of materials on average.` :
          s.strategy === 'VENDOR' || s.strategy === 'CRAFTVEND' ? `Sell to a vendor for <b>${money(s.sell_price)}</b> each.`
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
    ${hist.h ? `<h2>Price by hour of day</h2>
    <p class="muted">How far this item's price usually sits above or below its own daily average at each hour, in your
      local time, over the days tracked so far. Buy in the low hours; list in the high ones.</p>
    <div class="chart" id="c-cycle"></div>` : ''}
    ${hist.d ? `<h2>Day by day (up to 90 days)</h2>
    <p class="muted">Average market price and units sold for each day the item sold. Days are UTC; today is still filling.</p>
    <div class="chart" id="c-dprice"></div><div class="chart" id="c-dsold"></div>` : ''}
    <h2>Order book (cheapest first)</h2>
    <div class="table-wrap" style="max-width:420px"><table><thead><tr><th class="num">Price each</th><th class="num">Units</th>
      <th class="num">Cost to clear to here</th></tr></thead><tbody>${(() => {
        let run = 0;
        return (hist.ladder || []).map(([p, q]) => { run += p * q; return `<tr><td class="num">${money(p)}</td><td class="num">${num(q)}</td><td class="num">${money(run)}</td></tr>`; }).join('');
      })()}</tbody></table></div>`;
  if (hist.h) cycleChart($('#c-cycle'), hist.h);
  if (hist.d) {
    lineChart($('#c-dprice'), hist.d.t, [{ name: 'Average market price', color: 'var(--series-1)', values: hist.d.p }], dayLabel);
    barChart($('#c-dsold'), hist.d.t, hist.d.s, 'Sold that day', dayLabel);
  }
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
function frame(t, lo, hi, fmt, when) {
  const W = 800, H = 240, l = 48, r = 12, top = 10, b = 24;
  const x = v => l + (v - t[0]) / Math.max(t[t.length - 1] - t[0], 1) * (W - l - r);
  const y = v => top + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - top - b);
  let svg = '';
  for (const v of niceTicks(lo, hi)) {
    svg += `<line x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/>
            <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;
  }
  if (when) {   // one point per day: label about eight of the days themselves
    const every = Math.ceil(t.length / 8);
    t.forEach((e, i) => {
      if (i % every) return;
      svg += `<text x="${x(e)}" y="${H - 6}" text-anchor="${x(e) > W - r - 30 ? 'end' : 'middle'}">${when(e).replace(/^\S+ /, '')}</text>`;
    });
    return { W, H, l, r, top, b, x, y, svg };
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
function lineChart(el, t, series, when) {
  const all = series.flatMap(s => s.values).filter(v => v != null);
  const lo0 = Math.min(...all), hi0 = Math.max(...all), pad = (hi0 - lo0) * 0.08 || hi0 * 0.05 || 1;
  const f = frame(t, Math.max(0, lo0 - pad), hi0 + pad, shortMoney, when);
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
    return `<b>${(when || hourClock)(t[i])}</b><br>` + series.map(s => `<span class="sw" style="background:${s.color}"></span>${s.name}: <b>${money(s.values[i])}</b>`).join('<br>');
  });
}
// Bars above and below a zero line: an item's usual price against its daily average, by local hour.
function cycleChart(el, utc) {
  const local = localCycle(utc), vals = local.filter(v => v != null);
  const W = 800, H = 220, l = 48, r = 12, top = 12, b = 24;
  const hi = Math.max(0.02, ...vals.map(Math.abs)) * 1.1;
  const y = v => top + (1 - (v + hi) / (2 * hi)) * (H - top - b), slot = (W - l - r) / 24, w = slot - 4;
  let svg = '';
  for (const v of niceTicks(-hi, hi, 4)) {
    svg += `<line x1="${l}" x2="${W - r}" y1="${y(v)}" y2="${y(v)}" stroke="${Math.abs(v) < 1e-9 ? 'var(--text-3)' : 'var(--grid)'}"/>
            <text x="${l - 6}" y="${y(v) + 4}" text-anchor="end">${pct(v, true)}</text>`;
  }
  local.forEach((v, h) => {
    const cx = l + slot * h + slot / 2;
    if (h % 3 === 0) svg += `<text x="${cx}" y="${H - 6}" text-anchor="middle">${hh(h)}</text>`;
    if (v == null) return;
    const a = y(Math.max(v, 0)), hgt = Math.max(1, Math.abs(y(v) - y(0)));
    svg += `<rect data-h="${h}" x="${(cx - w / 2).toFixed(1)}" y="${a.toFixed(1)}" width="${w.toFixed(1)}" height="${hgt.toFixed(1)}" rx="${Math.min(3, hgt / 2)}" fill="var(--series-${v >= 0 ? 2 : 1})"/>`;
  });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price against the daily average by hour of day, bar chart">${svg}</svg>`;
  const svgEl = $('svg', el);
  svgEl.addEventListener('mousemove', ev => {
    const box = svgEl.getBoundingClientRect();
    const h = Math.floor(((ev.clientX - box.left) / box.width * W - l) / slot), v = local[h];
    el.querySelectorAll('rect').forEach(q => q.setAttribute('opacity', Number(q.dataset.h) === h ? 1 : 0.45));
    if (v == null) { tip.hidden = true; return; }
    tip.innerHTML = `<b>${hh(h)}</b><br>Usually <b>${pct(Math.abs(v))} ${v >= 0 ? 'above' : 'below'}</b> the day's average`;
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  });
  svgEl.addEventListener('mouseleave', () => { tip.hidden = true; el.querySelectorAll('rect').forEach(q => q.setAttribute('opacity', 1)); });
}
function barChart(el, t, values, label, when) {
  const f = frame(t, 0, Math.max(...values, 1), v => num(v), when);
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
    return i < 0 ? '' : `<b>${(when || hourClock)(t[i])}</b><br>${label}: <b>${num(values[i])}</b>`;
  });
}

// ------------------------------------------------------------- professions --
// Overview of every profession, or one profession recipe by recipe, judged against your profile.
const ALL_PROFS = ['Alchemy', 'Blacksmithing', 'Enchanting', 'Engineering', 'Leatherworking', 'Tailoring',
  'Mining', 'Herbalism', 'Skinning', 'Cooking', 'First Aid', 'Fishing'];
const GATHER_PROFS = ['Mining', 'Herbalism', 'Skinning', 'Fishing'];
const PRIMARY_PROFS = ALL_PROFS.filter(p => !SECONDARY.includes(p));
const SLOT_NAMES = ['Primary profession', 'Second primary profession'];
const P = { q: '', sort: 'day', can: true, profitable: true };   // recipe list filters
const DAY_CAP = 20;   // "profit a day" assumes you sell at most this many a day, or one craft per cooldown

// The best place to sell one unit: the auction house (after the cut) or a vendor.
function outlet(x) {
  const ah = x.net, vend = x.vnet;
  if (ah == null && vend == null) return null;
  return vend != null && (ah == null || vend > ah) ? { each: vend, where: 'vendor', price: x.vend } : { each: ah, where: 'ah', price: x.sell };
}
function perDay(x) {
  const o = outlet(x);
  if (!o) return null;
  if (x.cd) return o.each * x.mk / x.cd;
  return o.each * (o.where === 'vendor' ? DAY_CAP : Math.min(x.spd || 0, DAY_CAP));
}
// Recipe colour against a skill, as the game shows it.
function skillColour(x, skill) {
  if (skill == null) return null;
  if (x.need != null && skill < x.need) return ['cannot', 'Not yet: needs more skill'];
  if (x.y == null || x.g == null) return null;
  const green = Math.floor((x.y + x.g) / 2);
  if (skill < x.y) return ['orange', 'Orange: a skill point every craft'];
  if (skill < green) return ['yellow', 'Yellow: usually a skill point'];
  if (skill < x.g) return ['green', 'Green: sometimes a skill point'];
  return ['grey', 'Grey: no more skill points'];
}
const skillCell = (x, slot) => {
  const c = skillColour(x, slot ? slot.skill : null);
  return `${x.need == null ? '–' : (x.ex ? '' : '≈') + x.need}${c ? ` <span class="sk sk-${c[0]}" title="${esc(c[1])}">${c[0] === 'cannot' ? 'too low' : c[0]}</span>` : ''}`;
};

function profileSummary() {
  const parts = PROFILE.slots.filter(s => s.prof).map(s => `${s.prof} ${s.skill}${s.spec && s.spec !== 'none' ? ` (${s.spec})` : ''}`)
    .concat(SECONDARY.filter(p => PROFILE.secondary[p] > 0).map(p => `${p} ${PROFILE.secondary[p]}`));
  return parts.length ? parts.join(' · ') + (PROFILE.onlyKnown ? ' · known recipes only' : '') : 'not set';
}
function profileHtml(specs) {
  return `<details class="profile"${hasProfile() ? '' : ' open'}><summary><b>Your professions:</b> ${esc(profileSummary())}</summary>
    <div class="profile-body">${PROFILE.slots.map((slot, i) => `<fieldset data-slot="${i}"><legend>${SLOT_NAMES[i]}</legend>
      <div class="radios">${['', ...PRIMARY_PROFS].map(pr => `<label><input type="radio" name="prof${i}" value="${esc(pr)}"${slot.prof === pr ? ' checked' : ''}>${pr || 'None'}</label>`).join('')}</div>
      <label class="slider">Skill <input type="range" min="0" max="300" step="1" value="${slot.skill}" data-k="skill"><output>${slot.skill}</output></label>
      ${(specs[slot.prof] || []).length ? `<label class="slider">Specialisation <select data-k="spec">
        <option value="">Don't filter</option><option value="none">None</option>
        ${specs[slot.prof].map(sp => `<option${slot.spec === sp ? ' selected' : ''}>${esc(sp)}</option>`).join('')}</select></label>` : ''}
    </fieldset>`).join('')}
    <fieldset data-sec="1"><legend>Secondary professions <span class="muted">(0 = not learned)</span></legend>
      ${SECONDARY.map(p => `<label class="slider"><span class="sec-name">${esc(p)}</span>
        <input type="range" min="0" max="300" step="1" value="${PROFILE.secondary[p]}" data-sec="${esc(p)}"><output>${PROFILE.secondary[p]}</output></label>`).join('')}
    </fieldset>
    <label class="known-only"><input type="checkbox" id="only-known"${PROFILE.onlyKnown ? ' checked' : ''}> Only recipes I've ticked as known
      <span class="muted">(${num(PROFILE.knownSet.size)} ticked)</span></label>
    <p class="muted">Remembered in this browser. The Buy list uses it too: its CRAFT rows show only what you can make.</p></div></details>`;
}
function bindProfile(root, onChange) {
  const sec = $('fieldset[data-sec]', root);
  if (sec) sec.addEventListener('input', e => {
    const t = e.target;
    if (!t.dataset.sec) return;
    PROFILE.secondary[t.dataset.sec] = Number(t.value); t.nextElementSibling.value = t.value;
    saveProfile();
    onChange(false);
  });
  root.querySelectorAll('fieldset[data-slot]').forEach(fs => {
    const slot = PROFILE.slots[Number(fs.dataset.slot)];
    fs.addEventListener('input', e => {
      const t = e.target;
      if (t.type === 'radio') { slot.prof = t.value; slot.spec = ''; }
      else if (t.dataset.k === 'skill') { slot.skill = Number(t.value); t.nextElementSibling.value = t.value; }
      else if (t.dataset.k === 'spec') slot.spec = t.value;
      saveProfile();
      // A new profession changes which specialisations apply: redraw the whole page.
      onChange(t.type === 'radio');
    });
  });
  const ok = $('#only-known', root);
  if (ok) ok.addEventListener('change', e => { PROFILE.onlyKnown = e.target.checked; saveProfile(); onChange(false); });
}

async function viewProfessions() {
  const h = houseMeta(S.houseId);
  if (h.faction === 'blackwater') {
    view.innerHTML = `<h1>Professions</h1><div class="note">Crafting is judged in the faction houses: Blackwater's
      prices are too thin to value materials by. Pick your faction's house at the top right.</div>`;
    return;
  }
  const d = await getJSON(`data/recipes-${S.houseId}.json`);
  view.innerHTML = `
    <h1>Professions — ${esc(houseLabel(h))}</h1>
    <p class="lead">What each profession can earn in this house right now, and recipe by recipe what is worth making
      at your skill. Materials are priced at this house's market price (or the vendor price for everyday supplies);
      products at the lower of the cheapest listing and the typical price, after the ${pct(d.cut)} cut.</p>
    <div class="seg" role="tablist">
      <button type="button" data-v="overview" role="tab">Overview</button>
      <button type="button" data-v="detail" role="tab">Recipes by profession</button></div>
    <div id="pr-profile">${profileHtml(d.specs)}</div>
    <div id="pr-out"></div>`;
  const redraw = full => { if (full) { viewProfessions(); return; } draw(); $('#pr-profile summary').innerHTML = `<b>Your professions:</b> ${esc(profileSummary())}`; };
  bindProfile($('#pr-profile'), redraw);
  const tabs = view.querySelectorAll('.seg button');
  tabs.forEach(b => b.addEventListener('click', () => { PROFILE.view = b.dataset.v; saveProfile(); draw(); }));

  const draw = () => {
    tabs.forEach(b => b.setAttribute('aria-selected', String(b.dataset.v === PROFILE.view)));
    if (PROFILE.view === 'detail') drawDetail(d); else drawOverview(d);
  };
  draw();
}

function drawOverview(d) {
  const rows = ALL_PROFS.map(prof => {
    const list = d.recipes.filter(x => x.p === prof && outlet(x));
    const slot = slotFor(prof);
    const yours = slot ? list.filter(x => canMake(x.p, x.need, x.sp, x.s)) : null;
    const paying = list.filter(x => outlet(x).each > 0 && (perDay(x) || 0) > 0).sort((a, b) => perDay(b) - perDay(a));
    const rec = d.records[prof];
    const gather = (d.gather[prof] || []);
    return {
      prof, slot, priced: list.length, paying, rec,
      yoursPaying: yours ? yours.filter(x => outlet(x).each > 0 && (perDay(x) || 0) > 0).length : null,
      top: paying.slice(0, 10).reduce((a, x) => a + perDay(x), 0),
      cds: paying.filter(x => x.cd),
      gold: gather.reduce((a, x) => a + x[2] * x[3], 0), gather,
    };
  }).sort((a, b) => (b.top - a.top) || (b.gold - a.gold));
  $('#pr-out').innerHTML = `
    <div class="table-wrap"><table><thead><tr><th>Profession</th><th class="num">Recipes priced</th>
      <th class="num">Profitable now</th><th class="num">Top ten, profit a day</th><th>Best right now</th><th>Cooldowns</th>
      <th class="num">Track record</th><th class="num">Gathered goods traded a day</th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td><a href="#/professions" data-prof="${esc(r.prof)}"><b>${esc(r.prof)}</b></a>${r.slot ? `<span class="why">yours, skill ${r.slot.skill}${r.yoursPaying != null ? ` · ${r.yoursPaying} profitable you can make` : ''}</span>` : ''}</td>
      <td class="num">${r.priced || '–'}</td><td class="num">${r.priced ? r.paying.length : '–'}</td>
      <td class="num">${r.top ? money(r.top) : '–'}</td>
      <td class="item">${r.paying.slice(0, 3).map(x => `${esc(x.n)} <span class="muted">(${money(perDay(x))}/day)</span>`).join('<br>') || '–'}</td>
      <td class="item">${r.cds.map(x => `${esc(x.n)} <span class="muted">(${money(outlet(x).each * x.mk)} every ${x.cd} day${x.cd === 1 ? '' : 's'})</span>`).join('<br>') || '–'}</td>
      <td class="num">${r.rec && r.rec[0] >= 10 ? `<span title="${r.rec[1]} of ${r.rec[0]} recommended crafts sold at the price, net of competition, over ${d.record_days} days">${pct(r.rec[1] / r.rec[0])}</span>` : '–'}</td>
      <td class="num">${r.gold ? money(r.gold) : '–'}</td></tr>`).join('')}</tbody></table></div>
    <p class="muted">"Profit a day" assumes you sell up to ${DAY_CAP} a day (fewer if fewer sell) or make one per cooldown,
      and leaves out listing deposits and your time. Track record: how often this profession's recommended crafts actually
      sold at the price over the last ${d.record_days} days, after other sellers' listings at the same price. Click a
      profession for its recipes.</p>`;
  $('#pr-out').querySelectorAll('a[data-prof]').forEach(a => a.addEventListener('click', e => {
    e.preventDefault(); PROFILE.focus = a.dataset.prof; PROFILE.view = 'detail'; saveProfile(); viewProfessions();
  }));
}

function drawDetail(d) {
  const mine = myProfs();
  if (!PROFILE.focus) PROFILE.focus = mine[0] || 'Blacksmithing';
  const prof = PROFILE.focus, slot = slotFor(prof), gather = d.gather[prof] || [];
  const others = ALL_PROFS.filter(p => !mine.includes(p));
  $('#pr-out').innerHTML = `
    <div class="filters">
      ${mine.length ? `<div class="seg small">${[...new Set(mine)].map(p => `<button type="button" data-f="${esc(p)}" aria-selected="${p === prof}">${esc(p)}</button>`).join('')}</div>` : ''}
      <label>${mine.length ? 'Another profession' : 'Profession'}<select id="pr-other"><option value="">${mine.length ? 'Choose…' : ''}</option>
        ${others.map(p => `<option${p === prof ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
      <label>Search<input id="pr-q" type="search" placeholder="recipe or material" value="${esc(P.q)}"></label>
      <label>Sort by<select id="pr-sort"><option value="day">Profit a day</option><option value="each">Profit each</option>
        <option value="need">Skill needed</option><option value="name">Name</option></select></label>
      ${slot ? `<label class="inline"><input type="checkbox" id="pr-can"${P.can ? ' checked' : ''}> Only what I can make</label>` : ''}
      <label class="inline"><input type="checkbox" id="pr-prof"${P.profitable ? ' checked' : ''}> Profitable only</label>
    </div>
    ${prof === 'Mining' ? `<div class="note"><b>Smelt or sell?</b> For smelting, "profit each" is what a bar earns over
      selling the ore it took. Positive: smelt. Negative or nothing: sell the ore.</div>` : ''}
    ${!slot ? `<p class="muted">${esc(prof)} is not in your profile, so recipes are not filtered by skill. Add it to your professions above to see what you can make.</p>` : ''}
    <div id="pr-list"></div>
    ${gather.length ? `<h2>What ${esc(prof.toLowerCase())} gathers, by gold traded a day</h2>
      <div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">Price</th><th class="num">Sold / day</th>
        <th class="num">Gold traded / day</th><th class="num">Days of stock</th></tr></thead><tbody>${gather.map(g => `<tr>
        <td class="item"><a href="#/item/${g[0]}">${esc(g[1])}</a></td><td class="num">${money(g[2])}</td><td class="num">${num(g[3], 1)}</td>
        <td class="num">${money(g[2] * g[3])}</td><td class="num">${g[3] ? num(g[4] / g[3], 1) : '–'}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  $('#pr-sort').value = P.sort;
  const list = () => {
    const q = P.q.trim().toLowerCase();
    let rows = d.recipes.filter(x => x.p === prof
      && (!q || x.n.toLowerCase().includes(q) || x.mats.some(m => m[0].toLowerCase().includes(q)))
      && (!P.profitable || (outlet(x) && outlet(x).each > 0))
      && (!slot || !P.can || canMake(x.p, x.need, x.sp, x.s)));
    const val = (x, f) => f(x) ?? -Infinity;
    const sorters = {
      day: (a, b) => val(b, perDay) - val(a, perDay), each: (a, b) => val(b, x => outlet(x)?.each) - val(a, x => outlet(x)?.each),
      need: (a, b) => (b.need ?? 0) - (a.need ?? 0), name: (a, b) => a.n.localeCompare(b.n),
    };
    rows.sort(sorters[P.sort]);
    const total = rows.length; rows = rows.slice(0, 300);
    $('#pr-list').innerHTML = total ? `<p class="muted">${num(total)} recipe${total === 1 ? '' : 's'}${total > 300 ? ' (showing 300)' : ''}.
      Skill "≈" is estimated (no recipe item states it; usually within 20 points).</p>
      <div class="table-wrap"><table><thead><tr><th title="Tick the recipes you know">Known</th><th>Recipe</th><th class="num">Skill</th>
        <th>Learned from</th><th class="num">Materials each</th><th class="num">Sells for</th><th class="num">Profit each</th>
        <th class="num">Sold / day</th><th class="num">Listed</th><th class="num">Profit a day</th><th class="num">Track record</th></tr></thead>
      <tbody>${rows.map(x => {
        const o = outlet(x), day = perDay(x);
        const tags = [x.cd ? `cooldown ${x.cd} day${x.cd === 1 ? '' : 's'}` : '', x.sp ? `needs ${x.sp}` : '', x.new ? 'new in Forever: check you can learn it' : '',
          x.mk !== 1 ? `makes ${num(x.mk, 1)}` : ''].filter(Boolean);
        return `<tr>
        <td><input type="checkbox" data-spell="${x.s}"${PROFILE.knownSet.has(x.s) ? ' checked' : ''} aria-label="I know ${esc(x.n)}"></td>
        <td class="item">${x.v ? `<a class="${qClass(x.q)}" href="#/item/${x.v}">${esc(x.n)}</a>` : esc(x.n)}
          <span class="why">${esc(x.mats.map(m => `${m[1]}x ${m[0]} (${m[2] == null ? 'not listed' : money(m[2]) + (m[3] ? ' at a vendor' : '')})`).join(', '))}</span>
          ${tags.length ? `<span class="why">${esc(tags.join(' · '))}</span>` : ''}</td>
        <td class="num">${skillCell(x, slot)}</td>
        <td>${x.learn ? `${esc(x.learn.n || 'Recipe item')}<span class="why">${x.learn.p ? money(x.learn.p) + ' here' : 'not on sale here'}</span>`
          : `<span title="No recipe item for it has been seen: usually a trainer teaches it">Trainer?</span>`}</td>
        <td class="num">${money(x.cost)}</td>
        <td class="num">${o ? money(o.price) + (o.where === 'vendor' ? '<span class="why">to a vendor</span>' : '') : '–'}</td>
        <td class="num"><b>${o ? money(o.each) : '–'}</b></td>
        <td class="num">${num(x.spd, 1)}</td><td class="num">${num(x.lst)}</td>
        <td class="num">${day == null ? '–' : money(day)}</td>
        <td class="num">${x.rec ? `<span title="Recommended ${x.rec[0]} times in ${d.record_days} days; sold at the price, net of competition, ${x.rec[1]} times">${x.rec[1]} of ${x.rec[0]}</span>` : '–'}</td></tr>`;
      }).join('')}</tbody></table></div>`
      : `<div class="note">No ${esc(prof)} recipes match these filters.${slot && P.can ? ' Try unticking "Only what I can make", or raise your skill above.' : ''}</div>`;
    $('#pr-list').querySelectorAll('input[data-spell]').forEach(cb => cb.addEventListener('change', () => {
      const spell = Number(cb.dataset.spell);
      if (cb.checked) PROFILE.knownSet.add(spell); else PROFILE.knownSet.delete(spell);
      saveProfile();
      const counter = $('#pr-profile .known-only .muted');
      if (counter) counter.textContent = `(${num(PROFILE.knownSet.size)} ticked)`;
      if (PROFILE.onlyKnown) list();
    }));
  };
  $('#pr-out').querySelectorAll('button[data-f]').forEach(b => b.addEventListener('click', () => { PROFILE.focus = b.dataset.f; saveProfile(); drawDetail(d); }));
  $('#pr-other').addEventListener('change', e => { if (e.target.value) { PROFILE.focus = e.target.value; saveProfile(); drawDetail(d); } });
  $('#pr-q').addEventListener('input', e => { P.q = e.target.value; list(); });
  $('#pr-sort').addEventListener('input', e => { P.sort = e.target.value; list(); });
  $('#pr-prof').addEventListener('change', e => { P.profitable = e.target.checked; list(); });
  if (slot) $('#pr-can').addEventListener('change', e => { P.can = e.target.checked; list(); });
  list();
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
      <td class="num">${sellsIn(s.days_to_sell)}</td><td>${riskBadge(s.risk_level)}${trackRecord(s)}</td></tr>`).join('')}</tbody></table></div>`
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
    ${dayTable(houseMeta(S.houseId).sales_by_day || [])}
    ${ratesHtml(m.houses)}`;
  hourChart($('#c-hours'), $('#t-hours'), houseMeta(S.houseId).sales_by_hour || []);
}

// Gold traded per day, with the change on the same weekday a week earlier once there is one.
function dayTable(days) {
  if (!days.length) return '';
  const byDay = new Map(days.map(d => [d.day, d]));
  return `<h2>Gold traded per day</h2>
    <p class="muted">Estimated value of everything sold, per UTC day, kept for 90 days. A day with fewer than 24 snapshots
      is incomplete. "Week on week" compares complete days with the same weekday a week before.</p>
    <div class="table-wrap" style="max-width:640px"><table><thead><tr><th>Day</th><th class="num">Snapshots</th>
      <th class="num">Gold traded</th><th class="num">Units</th><th class="num">Week on week</th></tr></thead><tbody>
    ${[...days].reverse().map(d => {
      const prev = byDay.get(d.day - 7 * 86400);
      const wow = prev && prev.snapshots >= 23 && d.snapshots >= 23 && prev.value ? d.value / prev.value - 1 : null;
      return `<tr><td>${dayLabel(d.day)}</td><td class="num">${d.snapshots}</td><td class="num">${money(d.value)}</td>
        <td class="num">${num(d.units)}</td><td class="num">${pct(wow, true)}</td></tr>`;
    }).join('')}</tbody></table></div>`;
}

function scorecardHtml(sc) {
  const head = `<h2>Scorecard: did past recommendations work?</h2>
    <p class="muted">Each recommendation is logged when first made, then checked against what the market did next.
      "Sold at the price" means at least the suggested quantity changed hands at the suggested sell price (within 3%).
      It is strict for flips: the play assumes you bought out the cheap listings, and if nobody did, buyers kept taking those.
      "Enough buyers" is the looser test: that many units sold at any price. "Sold, net of competition" is the strictest
      and the one to trust: it subtracts listings other sellers posted at the same price afterwards, which sell before yours.
      Covers the last ${sc && sc.days ? sc.days : 3} days.</p>`;
  if (!sc || !sc.rows.length) {
    return head + `<div class="note">Nothing scored yet${sc && sc.pending ? ` — ${num(sc.pending)} recommendation${sc.pending === 1 ? ' is' : 's are'} being watched` : ''}.
      A recommendation is scored once twice its predicted selling time has passed (at least 6 hours).</div>`;
  }
  const share = (a, b) => b ? pct(a / b) : '–';
  return head + `<div class="table-wrap"><table><thead><tr><th>Play</th><th>Risk rating</th><th class="num">Checked</th>
    <th class="num">Sold at the price, on time</th><th class="num">Sold at the price, late</th><th class="num">Not sold at the price</th>
    <th class="num">Enough buyers at any price</th><th class="num">Sold, net of competition</th></tr></thead><tbody>${sc.rows.map(r => `<tr><td>${esc(r.strategy)}</td>
    <td>${riskBadge(r.risk_level)}</td><td class="num">${num(r.checked)}</td><td class="num">${share(r.on_time, r.checked)}</td>
    <td class="num">${share(r.late, r.checked)}</td><td class="num">${share(r.missed, r.checked)}</td>
    <td class="num">${share(r.enough_buyers, r.checked)}</td><td class="num"><b>${r.net == null ? '–' : share(r.net, r.checked)}</b></td></tr>`).join('')}</tbody></table></div>
    <p class="muted">${num(sc.pending)} more being watched. Results older than ${sc.keep_days} days are dropped.
      ${sc.stretch && Object.keys(sc.stretch).length ? `"Sells in" is currently stretched by: ${Object.entries(sc.stretch).map(([k, v]) => `${k} ×${v}`).join(', ')} (re-tuned daily from these results).` : ''}</p>`;
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
    else if (route === 'professions') await viewProfessions();
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
