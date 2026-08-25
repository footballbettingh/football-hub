# Football Hub

Both halves of the football work in one place, behind a local website instead of
a terminal.

* **The card** — every upcoming fixture priced across ~45 markets, ranked by a
  probability that has been checked against what actually happened.
* **The evidence** — the value-betting backtest that concluded a model cannot
  beat the closing line, which is precisely why the card uses the line instead
  of fighting it.

```bash
pip install -r requirements.txt
```

Then double-click **`start.bat`**, or:

```bash
python fb.py serve
```

That opens <http://127.0.0.1:8756/>. The site only reads: every page renders
files that are already on disk, and nothing on a page rebuilds anything. The
jobs that write those files are the commands below, run from a terminal where
their output and their exit code are in front of you.

## The commands

The site never computes anything on a page load. It reads files, and each file
has a command that rebuilds it. Run them from the project root; every one is
safe to re-run, and every one prints what it did.

| Command | Rebuilds | Takes | Costs |
|---|---|---|---|
| `python fb.py fetch results` | match history and closing odds | 1–3 min | free, no API key |
| `python fb.py fetch leagues` | the league plan | ~5 s | **free** — it only asks what is in season |
| `python fb.py fetch odds` | fixture prices | ~10 s per league | **Odds API credits, ~4 per league** |
| `python fb.py model` | walk-forward predictions | 6–8 min | — |
| `python fb.py calibrate` | calibrators + the reliability record | ~2 min | — |
| `python fb.py card` | the card, the slate and the two headline picks | ~20 s | — |
| `python fb.py evidence` | the value-betting backtest and its insights | 5–15 min | — |

`fetch odds` is the only one that spends anything. Everything else reads what is
already on disk, so the worst a mistaken re-run costs you is the time.

They depend on each other in that order. New results are worth nothing until the
model has walked forward over them, the model is worth nothing until the
calibrators have been refitted, and the card is priced from both. Running one
without the ones above it is the mistake that actually happens, and it shows up
as a page quoting numbers built from something older than it claims.

The whole chain, in order, without the prices:

```bash
python fb.py run --no-odds --no-notify
```

### Reading what is already there

These compute nothing and write nothing. They print to the terminal.

| Command | Shows |
|---|---|
| `python fb.py best` | the day's pick and the accumulator pick, from the card on disk |
| `python fb.py history` | the daily pick's record and running P&L |
| `python fb.py evaluate` | the reliability tables, per market and against the closing line |
| `python fb.py sweep` | the market-fusion weight, 0 (model alone) to 1 (line alone) |

### Looking at it

| Command | Does |
|---|---|
| `python fb.py serve` | the local site on <http://127.0.0.1:8756/>; `--port N`, `--no-open` |
| `python fb.py export` | the same seven pages as static files under `site/` |

The server loads the code once at startup, so after editing anything under
`hub/`, `confidence/` or `valuebets/` you need to restart it. Editing
`static/*.css` or `*.js` does not: assets are served with their own timestamp
in the URL, so a reload picks the new file up.

## Running it without pressing anything

```bash
python fb.py run
```

Results, prices, model, calibration, card, Telegram — the whole chain above in
one command, with prices in front of it, because the card takes its fixture list
from the price files and without a fetch the upcoming matches eventually all
kick off.

Each stage is independently survivable: a provider having a bad afternoon logs a
line and the run carries on with yesterday's copy of that data. Only the card is
fatal, because notifying about a stale card is worse than saying nothing. The
run exits 0 either way and ends with a tally of what it skipped.

| Flag | Effect |
|---|---|
| `--no-odds` | spend no Odds API credits this run |
| `--odds-every N` | only fetch prices when the ones on file are older than N days (default 8) |
| `--skip-model` | no walk-forward rebuild or recalibration (~1 min instead of ~12) |
| `--skip-fetch` | re-price and notify from what is already on disk |
| `--sports a,b,c` | fetch prices for these leagues only |
| `--no-notify` | rebuild only, send nothing |
| `--only-if-changed` | stay quiet unless the pick itself changed |

### API credit usage

Only one thing in this project costs anything: fetching bookmaker prices from
The Odds API. Results, the model, the calibrators, the card and the backtest are
all free and local.

| | Credits |
|---|---|
| One league, one fetch | 4 |
| 31 tracked leagues, one fetch | ~124 |
| Free tier | 500 per month |
| Fetches that fit | 4 |

**The free tier is the binding constraint**, and `--odds-every` is what keeps a
daily run inside it. At every seventh day a year of runs would want 539 credits
a month; every eighth day wants 471. That is the whole reason the default is 8.

The price files reach about twelve days ahead, so an eight-day threshold still
leaves the card several days of fixtures at its thinnest. A run that finds fresh
prices spends nothing and re-prices the card against the new results anyway, so
the card keeps improving on days no credit is spent.

Three ways to spend nothing at all:

```bash
python fb.py run --no-odds        # this run fetches no prices
python fb.py run --odds-every 30  # only if the ones on file are a month old
python fb.py fetch leagues        # asks what is in season; always free
```

And one way to spend deliberately: `--odds-every 0` forces a fetch regardless of
how fresh the files are. `python fb.py fetch odds --sports a,b,c` fetches a named
subset, at 4 credits each, which is how you price one league without paying for
thirty-one.

Usage and remaining balance are on your Odds API dashboard; this project does not
track them, so `--odds-every` is a guard rather than a guarantee.

### Telegram

The card is a page; a bet is placed before kick-off. `fb.py run` closes that gap
by sending the day's pick when the refresh finishes.

1. Message **@BotFather**, `/newbot`, paste the token into `.env` as
   `TELEGRAM_BOT_TOKEN`.
2. Message the bot once — or, for a channel, add it as an **administrator** and
   post once. A channel will not accept a bot as a plain member, which is the
   step that looks like Telegram refusing to add it at all.
3. Find the id and check the wiring:

```bash
python fb.py telegram --whoami     # prints the chat id to paste into .env
python fb.py telegram --test       # sends one test message
python fb.py notify --dry-run      # shows the real message, sends nothing
```

The message is four lines, one fact to a line — the day and the league, the
fixture, the bet. It is read on a lock screen and answers one question, so the
confidence, the price, the band, the reliability footnote, the other two bands,
the accumulator and the ledger's record all stay on the page that has room for
them; `--full` sends that longer version instead. Several destinations:
comma-separate the ids.

Without credentials the run reports the notification as *unconfigured* and still
rebuilds everything; it never fails the refresh. "The same pick as last time" is
remembered in `data/notify_state.json`, so `--only-if-changed` only means
anything where that file survives between runs.

### Scheduling

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 12:00
```

Registers a daily Windows task that runs as you, no admin rights, logging to
`logs\run-YYYY-MM-DD.log`. `-StartWhenAvailable` is set, so a laptop asleep at
noon runs the job on wake instead of skipping the day. `-VbArgs` passes flags
through to `fb.py run`, `-RunNow` starts it once, `-Remove` deletes it.

## How many leagues the card covers

The history covers **40 competitions**; the card can only show the ones a price
feed quotes upcoming fixtures for. Those two numbers are set by different
things, which is why the Method page can say 40 while the card shows six.

**Check available leagues** asks The Odds API what is in season and cross-checks
it against the history here. That call is free, so the expensive question — what
would a full refresh cost? — gets answered before anything is spent. It writes
`data/leagues.json`, and **Fetch new prices** then follows that plan instead of
only refreshing the files that already exist.

As of August 2026 that is **31 leagues, about 124 credits** for a full fetch
against a 500/month free tier — which turns a six-league card of 70 fixtures
into a 31-league one of 334. Nine competitions in the dataset have no feed at
all (the National League, the lower Scottish divisions, Switzerland, Ireland,
Romania, Argentina's Copa de la Liga) and simply cannot be priced.

Two things the price files do that bite if you let them:

**They are appended to, never pruned**, so a match played last week is still in
them. Fixture loading drops anything that has kicked off — by kick-off time
where the feed gives one, so a match at 20:00 is still on the card at lunchtime.
Without that filter the card goes on offering played fixtures, "best pick of the
day" keeps naming one, and the ledger refuses to record a day that has gone —
the history simply stops growing while every page still looks populated.

**They mix two spellings of the same day.** Rows already on disk read back as
`2026-08-21`; fresh ones are Timestamps and write as `2026-08-21 00:00:00`.
pandas infers a format from the first value and raises on the first row that
disagrees, which is how a successful price fetch broke the next card build.
Dates are now parsed from their first ten characters with an explicit format,
and written back as plain text.

The mapping from competition to sport key was checked entry by entry against a
live listing, because a plausible wrong key is silent: the first draft pointed
the National League at `soccer_england_efl_cup`, a real in-season key for a
different competition, which would have priced cup ties against National League
team strengths without raising anything. A test now refuses any mapping onto a
cup.

## The two headline picks

At the top of the card, chosen in Python so the static export shows the same two
bets the server does:

**Best pick of the day** — the most reliable selection priced between **1.60 and
2.20** on the next match day. The range is the point: without it the answer is
always a 99% handicap paying 1.01, which is true, useless, and not what anyone
means by a best pick. (It is also the one band whose value-betting ROI came out
positive in the backtest — weak evidence, pointing the same way.)

Ranking is not on the raw probability, and not on the band's hit rate either. A
hit rate is shared by thousands of selections, so ranking on it collapses them
to one score and makes the order inside a band arbitrary. Instead the band
supplies a **factor** — actual ÷ predicted, capped at 1 — so a band that came up
two points short scales every claim in it down by the same proportion, the
ordering within the band survives, and a band that beat its claim gets no bonus.
Bands with fewer than 200 historical bets are left alone rather than adjusted by
noise.

**The next three match days** — the strongest selection in each of three price
bands, for each of the next three days that have fixtures. Match days rather
than calendar days, so an international break stretches the horizon instead of
showing two empty panels.

| band | price | that is roughly |
|---|---|---|
| Safe | 1.30–1.60 | a 77% shot |
| Best | 1.60–2.20 | a 62% shot |
| Longer | 2.20–3.00 | a 45% shot |

Every one of them goes into the record the moment it appears, so Saturday's pick
is logged on Thursday at Thursday's price and never revised. Three bands rather
than one because one pick a day needs ten months to reach a sample worth
reading — and because they test whether the forecast is as honest at 45% as it
is at 77%, which pooling would hide.

**Accumulator pick** — of every accumulator paying at least **3.0**, the one most
likely to land. Each leg must clear the target's n-th root on its own, so the
combined price clears the target by construction and no single long shot carries
the slip; the legs are then the highest-scoring available, and confined to the
same three-day horizon. Left unbounded the search happily paired a match on the
14th with one on the 26th — a slip nobody would place, and one that cannot
settle for a fortnight. One leg per fixture, always: two selections on one match
are correlated and multiplying them overstates the slip badly. Switch between 2
and 6 legs on the page, or `python fb.py best --legs 3`.

Both are configurable in `confidence/config.py` (`BEST_ODDS_MIN`,
`BEST_ODDS_MAX`, `ACCA_LEGS`, `ACCA_TARGET_ODDS`).

## The record

Every best pick of the day is written down in `data/best_picks.csv` before the
match, and graded once the result arrives. The **History** page shows the lot:
what was picked, at what price, what happened, and the running P&L at flat
stakes of one unit.

The ledger is deliberately dumb and append-only. One row per match day **per
price band**, and refreshing the card again before kick-off keeps the first
answer — a record that follows whichever pick currently looks best would show a
flattering history and mean nothing. Settlement only ever fills in the empty
columns. The History page splits the record by band, because a forecast can be
honest at 77% and overconfident at 45%, and pooling hides exactly that.

Two things it refuses to fudge:

* **A pick with no quoted price is graded won or lost but kept out of the P&L.**
  Settling it at our own fair odds would return exactly zero by construction and
  look like a result.
* **A pick still pending long after its match is flagged, not ignored.** Pending
  forever means the result is not being found — usually a club the results file
  spells differently — and left alone it would quietly keep a loss out of the
  record.

Matches are found by competition and both team keys rather than by date, so a
postponement of up to a week is still the same bet; beyond that it is treated as
a different fixture, because it is. The keys are **resolved**, not compared: the
price feed says "Mansfield Town" where the results file says "Mansfield", and an
exact comparison left eight real bets pending forever with nothing on the page
to say why. Resolution accepts only a single unambiguous candidate, so a name
that could be two clubs stays unmatched rather than being graded against the
wrong match.

**The accumulator keeps its own book** in `data/best_accas.csv` — one slip per
day it was issued, with its legs stored alongside it. A four-leg slip at 33% and
a single at 62% have nothing to say to each other, so they never share a hit
rate or a total. A void leg drops out and the slip settles on what is left, as a
bookmaker would; a slip whose surviving legs were not all quoted stays out of
the P&L whether it won or lost, since counting its losses and not its wins would
be worse than counting neither.

The record opens on 12 August 2026 with nine picks — three bands across 14, 15
and 16 August — the flagship being LASK v Ried, Over 2.5 goals at 1.75. Read the
hit rate against the confidence rather than the money: at these prices a run of
twenty either way is ordinary noise.

## The pages

| Page | Answers |
|---|---|
| **Card** | The two headline picks, then everything else filtered by league, market, confidence and price |
| **Fixtures** | Every match with its headline markets; click a row for all ~45 |
| **History** | What the daily pick has actually done, and the running P&L |
| **Reliability** | Does an 80% pick win 80% of the time — pooled, and per market |
| **Evidence** | Can a model beat the closing line (no), with the backtest that shows it |
| **Method** | How a price becomes a probability, and what the thing cannot do |

The card ships to the browser in full, so filtering and the accumulator tray are
instant and work with no server at all. Tick two legs from the same fixture and
it says so: they are not independent, and multiplying them overstates the parlay.

**Offered and Edge are blank on most rows, and that is not a bug.** The price
feed quotes 1X2 and, when fetched with the totals market, Over/Under 1.5 and
2.5 — about 1,700 of 13,800 selections on a full card. Handicaps, team totals,
BTTS and corners are priced here and nowhere else, so there is no offer to
compare against. *Fair* is always filled in: it is what the price would have to
be to break even. Tick **only with a price** to see just the comparable ones.
The reason a 1X2 row rarely appears at all is the one-per-fixture cap — a match
result almost never outranks the handicap or total from the same match.

Nothing on the site scrolls sideways. Wide tables shed their least useful
columns as the window narrows — Band record first, then Offered and Edge (empty
on most rows anyway), then the league — rather than pushing a scrollbar under
the page.

## Publishing

```bash
python fb.py export
```

writes the same seven pages as static files under `site/`, with relative links,
self-hosted fonts, a sitemap and a robots.txt. Both modes call the same page
builders with a different `Links`, which is what stops the two from drifting
apart — the only difference is that one serves `/card` and the other
`card.html`.

`.github/workflows/daily.yml` runs that, and everything before it, on GitHub
Actions at **09:00 UTC** daily — the same `fb.py run` a laptop would call — then
publishes `site/` to GitHub Pages. Nothing of yours has to be switched on.

Three things about it are worth knowing, because each one is a way the cycle
could quietly stop being true:

**The cache is what makes it affordable.** `data/` is restored from the previous
run before anything else happens. `fb.py run` decides whether to spend Odds API
credits by reading `fetched_at` out of the price files, so with no cache there
are no price files, every run looks overdue, and 31 leagues at 4 credits drains
the 500-a-month free tier in four days.

**The ledger is committed back.** Everything else under `data/` is derived and
can be rebuilt; `best_picks.csv` and `best_accas.csv` cannot, because each row
was written down *before* its match. The workflow commits and pushes them after
each run, which is also why it asks for `contents: write`.

**The clock drifts.** GitHub cron is UTC only, so 09:00 UTC is noon in Sofia
under summer time and 11:00 once the clocks go back.

### Setting it up

Repo → **Settings → Pages → Source: GitHub Actions**, then
**Settings → Secrets and variables → Actions**:

| Secret | Needed for |
|---|---|
| `ODDS_API_KEY` | upcoming prices, and therefore the fixture list |
| `FOOTBALL_DATA_KEY` | fixture metadata |
| `TELEGRAM_BOT_TOKEN` | the daily message |
| `TELEGRAM_CHAT_ID` | who receives it |

All four are optional in the sense that the run degrades rather than fails
without them — but with no odds key there are no upcoming fixtures, and so no
card.

## Layout

```
fb.py                    one CLI for both halves
start.bat                double-click to run the site
hub/                     the part you look at
  server.py              stdlib HTTP server: 7 pages, GET only, localhost only
  artifacts.py           what exists on disk, and how stale it is
  pipeline.py            fetch / model / calibrate, called by the CLI
  card.py                fixtures -> picks.json
  evidence.py            the value backtest -> evidence.json
  leagues.py             readable names, and which leagues a feed can reach
  ledger.py              the daily pick, written down and later graded
  pages.py               the seven page builders
  components.py          layout, tables, the page shell
  export.py              the same pages as static files, plus sitemap and robots
  notify.py              the day's pick, pushed to Telegram after a run
  static/                style.css, fonts.css, charts.js, hub.js, fonts/
confidence/              calibrated probabilities (the card)
valuebets/               value betting (the evidence)
scripts/                 Windows scheduler, for running it locally instead
.github/workflows/       the daily cloud run: refresh, notify, publish
data/                    everything fetched and derived, gitignored
tests/                   224 tests
```

`confidence` and `valuebets` are unchanged from the two projects this was merged
from, other than pointing at one shared `data/` folder. Those two checkouts still
exist and still run; **this one is now the one to edit.**

## What the numbers are

The short version, with the measurements in
[`confidence`'s notes](confidence/) and on the Method page:

1. De-vig the consensus closing price with the **power method** — it takes
   margin off longshots rather than spreading it evenly, halving calibration
   error against proportional scaling.
2. Fit a pair of Poisson means and a Dixon-Coles rho until the score matrix
   **reproduces that price**. Every market nobody quotes — BTTS, Over 1.5, team
   totals, handicaps — is read off the same matrix, so the card cannot
   contradict itself.
3. Fuse in a joint-MLE goals model at **10%**. Measured, not assumed: 0.90 beats
   both 0.75 and the pure market, paired by match at p = 2.3e-07.
4. **Calibrate** per market with isotonic regression fitted only on earlier
   matches. Over 45,484 out-of-sample matches every confidence band lands within
   0.16pp of what it claimed.
5. **Cap** each market at the highest confidence its own record supports.
   Corners stop at 85% and BTTS at 70% because they overstated themselves above
   that; the rest stop where the sample runs out.

## Tests

```bash
python -m pytest tests/ -q
```

185 tests, aimed at the quiet failures: a fixture that has already been played
still being offered, a static export whose links point at server routes and 404
once published, a page in the nav that the server has no route for, a job that
dies without saying so, a `</script>` inside a team name that ends the data
block early, a page that renders an empty table instead of admitting the data is
missing, a league mapped onto a cup competition of the same country, an
accumulator built from two legs of the same match, a date column written in two
spellings by two runs, a ledger row rewritten after the result was known, an
accumulator whose void leg was counted as a loss, a slip whose legs spanned a
fortnight.

## Not betting advice

A calibrated probability says how often something happens. It does not say the
price on offer is worth taking — and the short prices at the top of the card are
the ones bookmakers get most right. The Evidence page exists to keep that
distinction in view.
