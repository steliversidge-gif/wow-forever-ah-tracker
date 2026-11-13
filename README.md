# Forever AH

A zero-cost auction house tracker for **World of Warcraft: Forever**. It watches one
ruleset's auction houses and answers a single question: *what should I buy, what do I
relist it for, and how risky is it?*

It runs itself on GitHub Actions every hour and publishes a static dashboard to GitHub
Pages. No server, no home PC left on, nothing to pay for.

## What the dashboard shows

| Page | What it is for |
|---|---|
| **Buy list** | One row per trade: how many to buy, the most to pay, what to sell each for, profit after the house cut, how long it should take to sell, and a risk rating with the reasons behind it. Filter by budget, minimum profit, risk and play. |
| **Market** | Every listed item with its cheapest price, typical price, trend and estimated sales per day. Use it to price things you want to sell. |
| **Farm** | Items you can go and get (herbs, ore, skins, fish, drops), ranked by how much gold the market pays out for them a day, with days of stock on sale. No buying involved. |
| **Item page** | Price history and estimated sales by the hour, the order book with the running cost of buying it out. |
| **Cross-faction** | Every EXPORT trade in both directions: buy in one faction's house, list on Blackwater for the other faction. |
| **Status** | Collector health, the scorecard (did past recommendations work?), when things sell by hour of day, the measured base rates, and the one-off "which house is mine?" setup. |

### The six plays

- **VENDOR** – listed below what an NPC vendor pays. Buy it, vendor it. No market risk.
- **FLIP** – the cheapest listings sit well under the next price up. Buy them, relist at
  that next price (never above the item's typical price). At one price the newest listing
  sells first, so matching beats undercutting.
- **DIP** – the whole market is 25%+ under its typical price. Buy cheap, relist near
  typical. Slower, and it depends on the price recovering, so none is suggested while the
  price is still falling faster than 10% a day, and the resale price is never more than
  1.5 times the current market price.
- **CRAFT** – buy the materials, make the item, list it at the cheapest current price.
  Recipes come from Wowhead's Forever database; materials are priced at this house's market
  price, or the vendor price for everyday vendor-only supplies. The "Buy" and "Pay up to"
  columns show how many to make and the material cost of each.
- **DISENCHANT** – gear listed for less than the enchanting materials it breaks into (with
  a 20% margin). Needs an enchanter. Items made by enchanters (wands, for example) are
  left out: Forever does not let you disenchant them. It uses the original game's disenchant table, which
  is not confirmed for Forever: test a few pieces before buying many.
- **EXPORT** – buy in your faction's house and list on the neutral Blackwater house, where
  the other faction can buy it. Needs all three houses confirmed in `config.toml`. A trade
  is only suggested when the item has actually been selling on Blackwater (about 6 hours
  of history, at least 3 sales), it is priced no higher than the buyers pay on their own
  house or than it has sold for on Blackwater, and the profit survives Blackwater's cut
  and one lost deposit. Blackwater carries roughly a twentieth of a faction house's
  volume, so these are rated riskier than a flip.

**Selling speed is judged at your price, not any price.** Only sales at the relist price
or above count, and listings other sellers post at that same price are subtracted, because
at one price the newest listing sells first. Each trade also suggests a listing duration:
24h by default (the deposit costs the same per hour and is refunded on sale, so the
longest listing loses least), or 8h when the price is falling, volatile or crowded and
you may have to cancel and reprice.

**Junk listings are ignored.** A piece of cloth posted at 1,000g is not stock, a price or
a sale. Anything listed above 5 times the item's going price (the higher of its current
market price and its typical price; `junk_multiple` in `config.toml`) is left out of the
stock count, the relist prices and the sales estimates. Random-suffix gear is also judged
against its siblings: a "Feral Cloak of X" above 10 times the median Feral Cloak variant
(`family_multiple`) is junk, even when it is that variant's only listing. The item page's
order book still shows every listing.

**The scorecard keeps it honest.** Every recommendation is logged when first made and
later checked against what the market did ([scorecard.py](src/forever_ah/scorecard.py)).
The base rates behind the sales estimates are re-measured from every hourly snapshot.
Its first day changed three things: "sells in" is stretched for flips, crafts and exports
(they ran late), dips on fast-falling prices were dropped, exports are rated riskier, and
deep dips no longer hope for the full typical price.

Risk is a 0–100 score built from how fast the item sells, how much history backs the
estimate, how volatile the price is, whether it is falling, how often buyers pay the
relist price, and how much competing stock is posted at it. Every signal
lists its reasons in plain English. All definitions live in one file,
[src/forever_ah/signals.py](src/forever_ah/signals.py).

## How it works

```
GitHub Actions (hourly; each run starts the next whenever GitHub's scheduler misses the hour)
  ├─ download the SQLite database from the "state" release
  ├─ forever-ah run
  │    ├─ collect   Blizzard API → diff against live listings → hourly per-item summary
  │    ├─ analyse   typical price, trend, sales/day → buy/sell signals
  │    └─ export    static site + JSON into dist/
  ├─ upload the database back to the release
  └─ deploy dist/ to GitHub Pages
```

**Sales are inferred, not reported.** Blizzard's API never says "this sold". The collector
compares each snapshot with the last ([lifecycle.py](src/forever_ah/lifecycle.py)):

1. A stackable listing whose quantity *shrank* was bought from. That is a certain sale.
2. Stackables are bought cheapest first and, at one price, newest first. A stack that
   vanished ahead of a part-bought one was sold; one that vanished behind a surviving
   stack was cancelled or expired.
3. A vanished listing with an identical fresh one (same item, quantity and price) was
   reposted, not sold.
4. A listing with more time left than the gap between snapshots cannot have expired. One
   that could have expired counts only partly as a sale, weighted by measured base rates
   (about a third of listings with 30 minutes to 2 hours left expire each hour).

**Storage.** A listing is stored once while it is live and deleted when it closes; what
happened to it is tallied into one summary row per item per snapshot. Seven days of
history for three houses is a few hundred MB, not tens of GB. Before the hourly rows are
pruned they are rolled up into one row per item per day it sold, kept for 90 days: that is
the "Day by day" chart on the item page and the "Gold traded per day" table (with week on
week change) on the Status page.

## Setup

### 1. Host it on GitHub (free)

1. Create a **public** repository on GitHub (public repos get unlimited Actions minutes
   and free Pages) and push this folder to it.
2. **Settings → Secrets and variables → Actions → New repository secret**, twice:
   `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` (from
   <https://develop.battle.net/access/clients>).
3. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Actions → Collect and publish → Run workflow**. The first run takes a few minutes.
   Your dashboard is then at `https://<user>.github.io/<repo>/`.

### 2. Tell it which auction house is yours

Forever has no realms. Each ruleset has its own Alliance, Horde and Blackwater houses,
but the API lists every ruleset's houses together without labelling them. Until you
choose, every candidate is tracked.

Open the dashboard's **Status** page, look up two or three of the listed items in game,
and see which house's prices match. Then edit [config.toml](config.toml):

```toml
[houses]
alliance = 110     # the house number that matched
horde = 0          # 0 = still deciding, -1 = not interested
blackwater = -1
```

Commit and push; the workflow re-runs and drops the other candidates.

### 3. Check the fee assumptions

Blizzard has not published Forever's auction fees, so `config.toml` holds what the
in-game auction house charged on 2026-11-11: a 5% cut, a 24h deposit of 60% of vendor
value (300% at Blackwater; shorter listings cost proportionally less: 20% for 8h, 5% for
2h) and a 1 silver minimum deposit per auction. Blackwater's 15%
cut is still Classic's figure and has not been checked. Every profit figure depends on
these, so correct them if the game charges you something different.

## Running it locally (optional)

```powershell
py -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env        # then add your Blizzard client id and secret
.venv\Scripts\forever-ah run      # collect + analyse + export
.venv\Scripts\forever-ah serve    # http://127.0.0.1:8000
.venv\Scripts\forever-ah status
.venv\Scripts\pytest
```

After changing `config.toml` locally, use `forever-ah run --reanalyse`.

## Known limits

- **Recipes and item sources arrive slowly.** They are looked up on Wowhead one item at a
  time, about 120 an hour, most-traded first, because Wowhead blocks faster reading. The
  Craft play and the Farm page fill in over the first two to three days. Wowhead is
  unofficial: if it stops answering, those two features stop growing and nothing else breaks.

- **The API is undocumented and a week old.** Endpoints were verified on 2026-11-11 and
  may change without notice. If the collector starts failing, look at
  [blizzard.py](src/forever_ah/blizzard.py) first.
- **Sales are estimates.** Stackable goods are measured well. Gear is weaker: a lone
  piece that vanishes counts as a fraction of a sale, using base rates measured on one
  day of launch week. Treat "sold per day" as a good guide, not a ledger.
- **Signals need history.** Vendor deals appear at once. Flips need 6 hourly snapshots and
  dips need 24; everything is rated riskier until about three days of data exist.
- **Data is up to ~90 minutes old.** Blizzard publishes hourly and GitHub's scheduler can
  run late. A cheap listing may be gone by the time you log in.
- **A lone junk listing cannot always be spotted.** If the only listing of an item with no
  sibling variants has been a silly price since tracking began, there is nothing to
  compare it with.
- **Item variants are shown by number.** Random-suffix gear is tracked separately (for
  example `[variant 12726]`) but the suffix name is not resolved.
- **A new economy deflates.** In launch weeks most prices fall as supply ramps up, which
  is exactly when "below typical price" is least trustworthy. The trend check covers
  part of this; keep stakes small at first.

## Ideas for later

- Ask-the-data agent (Claude tool-calling over the functions in `signals.py`).
- Discord alerts for high-value snipes.
- Crafting profit: materials cost against crafted item price.
