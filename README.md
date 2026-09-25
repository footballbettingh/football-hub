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
| `python fb.py fetch odds` | fixture prices | ~10 s per league | **Odds API credits, 2 per league** |
| `python fb.py model` | walk-forward predictions | 6–8 min | — |
| `python fb.py calibrate` | calibrators, the reliability record, the pick factors | ~2 min | — |
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
| `python fb.py history` | the daily pick's record against what it claimed |
| `python fb.py evaluate` | the reliability tables, per market and against the closing line |
| `python fb.py sweep` | the market-fusion weight, 0 (model alone) to 1 (line alone) |

### Measuring a change

```bash
python fb.py backtest-slate --compare-tiebreak
```

Chooses the slate again over the whole history, out of sample — a month at a
time, with the calibrators, band records, ceilings and tie-break factors rebuilt
from the months before it only, and the day's picks made by the same code the
card uses — then grades it by band, market and year: what was claimed, what
landed, the z-score between them. About three minutes a replay; every pick goes
to `reports/slate_backtest.csv`. It is the number to judge a change to the model
or the picker on, because it is the thing the site publishes. What it cannot
replay is the earlier price the card is really bought at: the history only has
the closing one.

Read it for big regressions, not for fine print. At about a thousand picks a
band its standard error is near 1.4 points, and a change that moves the picks
moves the band gaps by as much again on its own: per-line calibration at 40,
80 and 150 knots — three equally good fits by every per-selection measure —
put the safe band at −1.7, +2.1 and +2.6. A calibration change is judged on the
walk-forward over every selection (`fb.py evaluate`), where the sample is a
hundred times larger; the replay says whether the published picks still look
like their claims.

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
fatal, because notifying about a stale card is worse than saying nothing. A
stage that fails on anything but a provider — a bug — is survived as well, but
prints its traceback and makes the run exit 1 once it has finished; a provider's
failure alone exits 0. Either way the run ends with a tally of what it skipped
and what broke.

| Flag | Effect |
|---|---|
| `--no-odds` | spend no Odds API credits this run |
| `--odds-every N` | buy the whole plan at once if the newest price is older than N days (`0` forces it) |
| `--skip-model` | no walk-forward rebuild or recalibration (~1 min instead of ~12) |
| `--skip-fetch` | re-price and notify from what is already on disk |
| `--sports a,b,c` | buy prices for these leagues now, instead of the ones that play soon |
| `--no-notify` | rebuild only, send nothing |
| `--only-if-changed` | stay quiet unless the pick itself changed |

### API credit usage

Only one thing in this project costs anything: fetching bookmaker prices from
The Odds API. Results, the model, the calibrators, the card and the backtest are
all free and local.

| | Credits |
|---|---|
| One league, one fetch (`eu` region, 1X2 and totals) | 2 |
| 31 tracked leagues, one fetch | ~62 |
| Which leagues play soon, and how many credits are left | free |
| Free tier | 500 per month |

**The free tier is the binding constraint, and the prices are the forecast** —
the model is nine parts closing line to one part itself, so a price four days
old is a forecast four days old. So a run does not buy every league at once. It
asks the free events endpoint which leagues have a match in the next three days,
and buys those, stalest first, up to today's share of what is left:
(credits remaining − 50) ÷ days to the monthly reset. The remaining count comes
from the API itself, on a free call, so the run cannot overspend however the
cache fares or whoever else is using the key. The reset date is not published;
the run watches for the used count falling and remembers the day in
`data/odds_quota.json`, and assumes the first of the month until it has seen one.

Simulated against seven months of fixtures from the history, that spends 374 to
450 credits a month and writes a pick down on prices 1.2 days old. The rule it
replaced — every league at once whenever the newest price was eight days old —
spent 308 to 512 and wrote picks down on prices 3.7 days old.

One region, not two: `eu,uk` cost four credits a league. Measured on 1,122
events before it was dropped, the UK books moved the de-vigged 1X2 by 0.36
points on average and the 2.5 total by 0.20, with no lean either way and the
same totals coverage — against 2.24 points for a price three to seven days old.
The exchange stays through its EU copy, and Pinnacle is in `eu`.

Three ways to spend nothing at all:

```bash
python fb.py run --no-odds        # this run fetches no prices
python fb.py run --odds-every 30  # the whole plan, only if the prices are a month old
python fb.py fetch leagues        # asks what is in season; always free
```

And ways to spend deliberately: `python fb.py run --odds-every 0` buys every
league in the plan now, and `python fb.py fetch odds --sports a,b,c` buys a named
subset, at 2 credits each, which is how you price one league without paying for
thirty-one.

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
only refreshing the files that already exist. `fetch odds` redraws the plan
itself before every fetch, so a league coming into season is added the first
time it is quoted, without anyone running `fetch leagues` again.

A league is left out of that plan if its **results have stopped arriving**, even
when the price feed is happy to sell it. Russia's Premier League published its
last result on 2 August 2026 and was still quoted for weeks afterwards; every
bet taken on it in that window can never be graded, and unlike an abandoned
fixture it cannot even be recorded as having no result, because a feed that says
nothing is not evidence that nothing happened. The test is a match rather than
a calendar: a fixture the price files listed kicked off more than fourteen days
ago, and the results file has nothing from that day on — in-season leagues run
two to ten days behind even through an international break. It used to be
three weeks since the newest result, which read every league as dead in
mid-August and after every winter break, and left the first round back
unpriced. A league returns to the card by itself the day its results resume.

If you would rather keep betting one of them and look the results up yourself,
add its code to `GRADED_BY_HAND` in `hub/leagues.py`. It stays on the card, the
build says so every time it prices it, and every bet on it settles only from a
score you enter in `data/manual_results.csv`. The list is empty by default,
because a bet nobody gets round to checking does not fail loudly — it sits at
pending for good.

As of August 2026 that is **31 leagues, about 62 credits** for a full fetch
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

**There is a tie-break, and it is switched off.** The price band does not
actually choose a price: ranking on probability inside a range always returns
the shortest price in it — all 27 safe picks in the early ledger sat between
1.3000 and 1.3142 of a band thirty odds-points wide — so dozens of selections
arrive at the target together. The tie-break gave those to whichever selection
had done best **at that exact price**, from a record in `data/pick_factors.csv`
on a grid of 2.5%-wide price cells, among scores within 5% of the best.

It was measured at +1.27 points pooled over 2,445 historical picks — but on
factors drawn from the same history it then chose picks on, so every pick was
ranked partly on its own result. Replayed strictly out of sample with
`python fb.py backtest-slate --compare-tiebreak`, factors built from earlier
months only, it changed 1,203 of 3,127 picks and landed **1.02 points fewer**
than ranking on the score alone: −2.0 in the safe band, 0.0 in main, −1.2 in
value. That is about a standard error, so it is not shown to do harm; it is
shown not to earn its complexity. `PICK_TIEBREAK` in `confidence/config.py`
turns it back on, and `fb.py calibrate` still writes the factors it would use.

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

The three are always on three different matches. Two selections on one fixture
are one bet on its scoreline rather than two measurements — on 26 August all
three bands were Real Madrid v Real Sociedad, and away under 0.5 goals, under
3.5 goals and over 7.5 corners land and miss together — so the Best band
chooses first and the other two take what it leaves. On a thin day that can
leave a band empty, which is the honest outcome: a second bet on a match
already bet is not a second test of anything.

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
what was picked, what it claimed, what happened, and how far the two are apart.

**It reports results, not money, and that is deliberate.** A profit figure needs
a price the bookmakers actually offered, and the feed quotes 1X2 and the goals
totals and nothing else. The picker ranks on confidence, and confidence peaks on
team totals, corners and draw-no-bet — none of which are quoted. Twenty-seven of
the first thirty settled picks had no price, so the page was showing a P&L
computed over three bets beside a record computed over thirty, an order of
magnitude apart and neither labelled. The money went; the record stayed.

That also matches what the project claims. Nothing here says it beats the
closing line — the Evidence page says the opposite at length. What it claims is
that a probability means what it says, and testing that needs a result, not a
price. So the page leads with the record and the claim beside it, and reads
the gap in standard errors: the wins to expect are the sum of the claims, the
spread around them the sum of p(1 − p), and `z` is how many spreads apart the
two are. The question is not whether the record is ahead but whether it sits
within two of its claim — which at this sample size it comfortably does. (It
was a Wilson interval on the pooled hit rate once, which treats picks claiming
45% and 77% as one coin and comes out wider than the truth.)

And beside the record, the same claims **asked again at the close**. Every
settled match is in the walk-forward priced at its closing line, so each pick's
selection can be priced there through the same fusion and calibration, and set
against the number it was written down at — `data/ledger_close.csv`, rebuilt by
each run. A hit rate needs hundreds of picks to read; how far a claim moved by
kick-off hardly varies from pick to pick, so a few dozen say whether the claims
are being made on prices the market then takes back. Corners have no closing
line and sit it out, and a pick written down before a change to the model
carries that change in its drift too.

The `odds` and `pnl` columns are still written when a price happens to exist,
because the ledger is the one file in the project that cannot be rebuilt and
throwing away a column is not reversible. Nothing reads them.

The ledger is deliberately dumb and append-only. One row per match day **per
price band**, and refreshing the card again before kick-off keeps the first
answer — a record that follows whichever pick currently looks best would show a
flattering history and mean nothing. Settlement only ever fills in the empty
columns. The History page splits the record by band, because a forecast can be
honest at 77% and overconfident at 45%, and pooling hides exactly that.

Two things it refuses to fudge:

* **A bet whose match never arrived is recorded as having no result**, not left
  at "pending" where it reads as a bet that might still land. The test is
  evidence rather than a timeout: a result more than a week after the match day
  could not settle the bet anyway, so once the league has played on past that,
  everything the results file is ever going to say has been said. It settles as
  a void does — stake back, out of the hit rate — and on an accumulator the leg
  drops out and the rest of the slip is graded. A slip graded on three of four
  legs is then compared against the three legs' own claim, not the four-leg one
  it was written down with, which would credit the forecast for a leg nothing
  tested.
* **What is still pending long afterwards is flagged, not ignored.** After the
  above, that can only be a league whose results have stopped arriving
  altogether — which is worth saying out loud, because nothing else on the page
  tells a quiet feed from a quiet week.

For the match a dead feed is never going to publish, `data/manual_results.csv`
takes a score checked by hand — date, competition, both team names as the
results file spells them, the goals, and a note saying where it was checked.
It is the only file here whose contents nobody can verify from a public source,
so it is fenced off accordingly: the row is marked, a real result overwrites it
the day the feed publishes one, and neither *has this league gone quiet* nor
*is this bet's result ever coming* is allowed to look at it. Otherwise one row
entered by hand would put a dead league straight back on the card.

Matches are found by competition and both team keys rather than by date, so a
postponement of up to a week is still the same bet; beyond that it is treated as
a different fixture, because it is. The keys are **resolved**, not compared: the
price feed says "Mansfield Town" where the results file says "Mansfield", and an
exact comparison left eight real bets pending forever with nothing on the page
to say why. Resolution accepts only a single unambiguous candidate, so a name
that could be two clubs stays unmatched rather than being graded against the
wrong match.

**The accumulator keeps its own book** in `data/best_accas.csv` — one slip per
size (two legs to six) per day it was issued, with its legs stored alongside it.
A four-leg slip at 33% and a single at 62% have nothing to say to each other, so
they never share a hit rate or a total, and nor do two slip sizes: History shows
one size at a time. Until 11 September 2026 only the four-leg slip was written
down, so the other sizes' records start there. A void leg drops out and the slip
settles on what is left, as a bookmaker would.

The record opens on 12 August 2026 with nine picks — three bands across 14, 15
and 16 August. Read what landed against what was claimed, and read the interval
next to both: a run of twenty either way is ordinary noise at this many picks,
and the page says so rather than letting a reader guess.

## The pages

| Page | Answers |
|---|---|
| **Card** | The two headline picks, then everything else filtered by league, market, confidence and price |
| **Fixtures** | Every match with its headline markets; click a row for all ~45 |
| **History** | What the daily pick has actually done, against what it said it would |
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

**The cache is what keeps the card whole.** `data/` is restored from the
previous run before anything else happens. The spending no longer depends on it
— the run paces itself on the API's own count of the credits left — but the
price files are the fixture list, the evidence that a league's results have
stopped, and the odds history nothing else sells; and `odds_quota.json` is
where the day the quota resets is remembered.

**The ledger is committed back.** Everything else under `data/` is derived and
can be rebuilt; `best_picks.csv` and `best_accas.csv` cannot, because each row
was written down *before* its match. The workflow commits and pushes them after
each run, which is also why it asks for `contents: write`.

Twice a run it holds them to their own rules with `python fb.py check-ledger`:
rows only ever added at the end, a column written before the match never
changed, a result only ever filled into a blank. Right after the cache is
restored, a book that breaks them — one a failed run cut short, say, since the
cache is saved whatever happened — is put back to the committed one
(`--restore`); right before the commit, it is refused. Every file under `data/`
is also written whole or not at all, into a temporary file renamed over the old
one, so a run killed mid-write leaves the previous version rather than half of
the new one.

**A bug turns the run red, after the fact.** A provider failing is a skipped
stage and the run exits 0. Any other failure prints its traceback and makes
`fb.py run` exit 1 — but the run still finishes, so the ledger is committed and
the site published from what is on hand, and only then does the `verdict` job
fail the workflow.

**The clock drifts.** GitHub cron is UTC only, so 09:00 UTC is noon in Sofia
under summer time and 11:00 once the clocks go back.

### Setting it up

Repo → **Settings → Pages → Source: GitHub Actions**, then
**Settings → Secrets and variables → Actions**:

| Secret | Needed for |
|---|---|
| `ODDS_API_KEY` | upcoming prices, and therefore the fixture list |
| `TELEGRAM_BOT_TOKEN` | the daily message |
| `TELEGRAM_CHAT_ID` | who receives it |

All three are optional in the sense that the run degrades rather than fails
without them — but with no odds key there are no upcoming fixtures, and so no
card. Each is handed only to the step that fetches and notifies, and the
workflow's token can push only from the step that commits the ledger. A
`FOOTBALL_DATA_KEY` secret left over from earlier setups is no longer read by
anything and can be deleted.

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

   Both prices, not just the 1X2. The three match-result prices pin two
   lambdas and the fit is exact, but rho — the low-scoring, draw-heavy
   structure that independent Poissons get wrong — is left at whatever the
   goals model guessed. The Over/Under 2.5 price frees it, and the feed sells
   it in the same call that already paid for the 1X2, on about nine fixtures in
   ten. The card used to drop it: priced both ways over 9,000 historical
   matches that costs 0.0045 Brier on BTTS, 0.0023 on the goal totals and
   0.0014 on team totals, and nothing measurable on the match result or
   anything derived from it alone — which is the signature of a missing rho,
   and lands on the three markets most of the slate is picked from.
3. Fuse in a joint-MLE goals model at **10%**. Measured, not assumed: 0.90 beats
   both 0.75 and the pure market, paired by match at p = 2.3e-07.

   All of that is measured on closing prices, and the card is priced a day or
   two earlier. Two ways of adjusting for that were tried on the early
   (Friday-afternoon) prices football-data keeps beside the closing ones, over
   35,163 matches out of sample, and both turned down: a lower market weight
   against the early price (0.9 still wins: Brier 0.18462, 0.18470 at 0.75),
   and calibrating on early prices instead of closing ones (0.18462 against
   0.18459 — the two lean the same way and differ only in how much they know).
   The early price's real cost, 0.18398 on the closing line against 0.18459,
   is paid down by buying the price later, which is what the paced fetch does.
   Nor does Pinnacle's closing price beat the average of the books it would
   replace (RPS 0.20186 against 0.20187 over 34,050 matches), and football-data
   stopped publishing it in 2025.
4. **Shrink the corner strengths to 55%** before reading a corner line off
   them. Corners are the one market here with no closing line behind them, so
   nothing else holds the fitted strengths in and they came out about twice as
   spread as reality: actual total corners regressed on predicted had a slope
   of 0.558 over 31,773 matches, the top decile predicting 11.6 and delivering
   10.8, the bottom predicting 8.2 and delivering 8.9. At a 10.5 line that is
   eight to ten points of probability, in opposite directions either side of
   the league average — which is exactly the error a per-group calibrator
   cannot repair, because it is monotone and over and under on the same line
   are wrong opposite ways at the same probability. Shrunk, the slope comes
   back to 0.86 and the spread between the best- and worst-behaved corner
   selection inside one confidence band falls from 15.8 points to 5.9. On the
   picks themselves the corner selections in the main band went from landing
   7.7 points below their claim to 0.8 points above it. It is an improvement
   and not a repair: corners are still overdispersed against a Poisson
   (variance / mean of 1.18, against 1.02 for goals). The league's own level is
   refitted after the shrink — squeezing the strengths under an unchanged base
   lowers the average of exp (Jensen), and the walk-forward was predicting
   9.695 corners a match against 9.799 played, every line leaning to the under.
   And the market gets a word in after all: a match the line prices for more
   goals than its league usually plays, or for more than the goals model
   expects, is played at a pitch that yields more corners, and the corner
   expectation is scaled for it (`confidence/corners.py`). Two coefficients,
   fitted on earlier matches only; out of sample the corner lines' Brier score
   fell by 0.0005 to 0.0007 on every split tried.
5. **Calibrate** each line with isotonic regression fitted only on earlier
   matches — one curve per line, learned on one side of it, the other side its
   complement. It was one curve per market, pooling lines that err in opposite
   directions; per line, the average gap between a line's claim and its record
   fell from 0.57 to 0.17 points on the goal totals, 0.68 to 0.20 on team
   totals and 2.43 to 0.53 on both-teams-to-score, out of sample. The three
   results and the three double chances, which have no single partner, keep
   one curve each market.
6. **Cap** each market at the highest confidence its own record supports.
   Corners stop at 85% and BTTS at 70% because they overstated themselves above
   that; the rest stop where the sample runs out.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

The versions in both requirements files are pinned to the ones the suite
passes on, and the workflow's actions to commits; Dependabot proposes every
bump as a pull request, so nothing changes under the daily run that the tests
have not seen.

Over four hundred tests, aimed at the quiet failures: a fixture that has already been played
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
