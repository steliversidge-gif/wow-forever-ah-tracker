# Keeping Forever AH running

A manual for running this without help. Everything here can be done from the GitHub
website and a text editor. Settings live in [config.toml](config.toml); change one,
commit, push, and the dashboard rebuilds itself within about ten minutes.

## The daily check (one minute)

Open the dashboard's **Status** page.

| Look at | Healthy | If not |
|---|---|---|
| The line under the page header | "Prices as of … (under 90 min ago)" | See "The dashboard has stopped updating" |
| Gold traded per day | Yesterday shows 24 snapshots | 22 or 23 is fine (Blizzard skips the odd hour). Under 20: see below |
| Scorecard, "Sold, net of competition" | Roughly: flips and dips over 50%, crafts over 40% | See "A play has stopped working" |

## The dashboard has stopped updating

Work down this list. Stop at the first thing that fixes it.

1. **Restart the chain.** On GitHub: **Actions → Collect and publish → Run workflow → Run
   workflow**. Each run starts the next one, so if a run ever fails to start, the chain
   stops until you do this once. Wait ten minutes and refresh the dashboard.
2. **Read the failed run.** On the **Actions** page, a red cross marks a failed run. Click
   it, then click the failed step to read the message at the bottom.
   - *"401" or "invalid client"*: the Blizzard API key has expired or been revoked. Make a
     new one at <https://develop.battle.net/access/clients> and replace the two secrets
     under **Settings → Secrets and variables → Actions**.
   - *"404" on an auctions address*: Blizzard has changed the API. The addresses are at the
     top of [src/forever_ah/blizzard.py](src/forever_ah/blizzard.py). This needs a programmer.
   - *Anything mentioning Wowhead*: harmless. Recipe and item-source lookups stop; nothing
     else is affected.
   - *Anything mentioning wago.tools*: harmless. Recipe skill levels stop refreshing and the
     last copy is kept. If Forever is ever renamed there, the product code `WAGO_PRODUCT` is at
     the top of [src/forever_ah/blizzard.py](src/forever_ah/blizzard.py).
3. **Sixty days of silence.** GitHub switches off scheduled workflows in a repository with
   no commits for 60 days. The workflow commits a small heartbeat file monthly to prevent
   this. If it ever happens, GitHub emails you; press **Enable workflow** on the Actions page.

The collector times itself from Blizzard's publish minute, so a change in when Blizzard
publishes needs no action.

## The game changed

| What changed | What to edit in `config.toml` |
|---|---|
| Auction house cut | `faction_cut` or `neutral_cut` under `[fees]` |
| Deposit rate | `deposit_rate` (24h listing, as a share of vendor value); `neutral_deposit_rate` for Blackwater |
| Minimum deposit (Blizzard has said 1s will become 1c) | `min_deposit = 1` |
| A recipe on the Buy list cannot actually be learned | Add the crafted item's name to `unavailable_recipes` |
| You play a different faction | `home_faction` |
| Blizzard merges or renumbers auction houses | The three numbers under `[houses]`. Set one to `0` to see the candidates on the Status page again |

Still unverified in game, and worth checking when you can: Blackwater's cut (set to 15%),
whether cancelling an auction forfeits the deposit (assumed yes), and the disenchant table
(the original game's).

## A play has stopped working

The **Scorecard** on the Status page is the judge. It covers the last three days and
re-measures itself every hour. Trust the last column, "Sold, net of competition".

- **One play's figure has fallen well below the others.** Hide it with the Play filter on
  the Buy list and wait a few days. Markets shift (a raid opens, a patch lands) and the
  typical prices take about a week to catch up.
- **Too many trades, most of them poor.** Raise the bar in `[tuning]`: `min_profit` (in
  copper, default 100) or `min_roi` (default 0.05 = 5%).
- **Too few trades.** Lower those two, or raise `horizon_days` (how many days of sales a
  single trade may try to cover, default 1).
- **Junk prices are showing up as "typical".** Lower `junk_multiple` (default 5) or
  `family_multiple` (default 10, for random-suffix gear).
- **Good dips are being left out.** `dip_max_fall` (default 0.10: no dip while the price
  falls over 10% a day), `dip_max_days` (default 1) and `dip_max_markup` (default 1.5).

Things that tune themselves, and need nothing from you:

- **"Sells in".** Each play's estimate is stretched so that three trades in four sell
  within the time shown. The stretch is re-tuned once a day from the scorecard, within
  1× to 3×. The current values are printed under the scorecard. To freeze them, set
  `auto_stretch = false` and the `stretch_*` values yourself.
- **Sale, cancel and repost rates** behind the sales estimates: re-measured from every
  hourly snapshot (the "Base rates" table on the Status page).
- **Repeat misses.** A trade that failed the last time it was recommended is rated riskier
  automatically (`repeat_miss_penalty`, default 15 points; 0 turns it off).
- **Track record.** The percentage under each risk rating on the Buy list is that play
  and rating's own recent record. If it reads 30%, believe it over the rating.

## What the numbers mean, honestly

- **Sales are inferred.** Blizzard never reports a sale. Stackable goods are measured
  well; a lone piece of gear vanishing is counted as a fraction of a sale.
- **"Sold at the price" flatters.** It asks whether buyers paid that price for that many
  units. It does not ask whether they would have bought *yours*. "Net of competition"
  subtracts later listings at the same price, which sell before yours. Measured over
  14–16 November 2026, crafts scored 93% on the first test and 48% on the second.
- **The data is up to 90 minutes old.** A cheap listing may be gone when you log in.
- **Prices have a daily rhythm.** Evenings are cheap, mornings dear, by about 6% each way.
  The note at the top of the Buy list says which you are in.

## Storage and cost

Everything runs on GitHub's free allowance for public repositories. The database travels
between runs as a file on the "state" release (about 70 MB compressed after a week; it
stops growing once the 7-day hourly window is full, apart from the small 90-day daily
summary). If the repository is ever made private, Actions minutes stop being free: the
collector uses about 4 minutes an hour.

## If you get a programmer's help later

- Every rule that decides a recommendation is in [src/forever_ah/signals.py](src/forever_ah/signals.py),
  with the evidence for each threshold in a comment beside it.
- How sales are inferred: [src/forever_ah/lifecycle.py](src/forever_ah/lifecycle.py).
- The scorecard and self-tuning: [src/forever_ah/scorecard.py](src/forever_ah/scorecard.py).
- Run the tests with `.venv\Scripts\pytest`. They should all pass before any push.
- To study the live data, download the newest file from the repository's "state" release,
  decompress it with `zstd -d`, and open it with any SQLite viewer.
