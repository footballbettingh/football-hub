# Next steps

Written 12 August 2026, after a full review of the merged project. Ordered by
what actually moves the needle, not by what is fun to build.

## 1. Make the record accumulate without you (highest value, lowest effort)

The ledger only grows when someone presses a button. Miss three days and those
three picks are gone — and unlike every other artifact here, a pick cannot be
reconstructed after the fact without destroying the thing that makes it worth
anything. Everything below depends on this being solved first.

A scheduled task is enough:

```
schtasks /create /tn "football-hub daily" /tr "C:\Users\Dafov\Dev\football-hub\daily.bat" /sc daily /st 09:00
```

where `daily.bat` runs `fb.py fetch odds`, `fb.py card`, and — separately, later
in the day — `fb.py fetch results`. The second one settles yesterday's picks.

**The catch is credits.** A full fetch of 31 leagues costs 124 credits
(31 × 2 regions × 2 markets). The free tier is 500 a month, so on it you get a
choice, not both:

| | leagues | fetches per month |
|---|---|---|
| current setup | 31 | 4 |
| drop to `eu` region only | 31 | 8 |
| six original leagues, daily | 6 | 20 |

Daily coverage of all 31 needs ~3,700 credits a month. That is the case for
paying, and it is made in section 5.

## 2. Record more than one pick a day

At one pick a day, 300 settled bets — the point where a hit rate starts meaning
something — is **ten months away**. At five picks a day in different price
bands, it is two. The ledger already keys on the match day; generalising it to a
small daily slate (say the best pick in each of 1.3–1.6, 1.6–2.2, 2.2–3.0, plus
the accumulator) is a contained change and it is the difference between
validating this year and validating next.

Keep the headline "best pick of the day" as the one you publish. The rest exist
to measure.

## 3. Track closing-line value

This is the sharpest analytical addition available, and it is what lets you
believe something before the sample is large enough to prove it.

Instead of waiting for results, compare the price taken against the price the
same market closed at. Beating the close is the single best-known predictor of
long-run profit, and it converges in **weeks rather than years** because every
bet contributes a measurement instead of one bit of win/lose.

Mechanically: record the pick as now, then re-fetch that fixture's price shortly
before kick-off and store it beside the taken price. The History page grows a
column — "beat the close 61% of the time" — long before the P&L column says
anything at all.

Note the tension with section 1: this needs a second fetch per fixture, close to
kick-off, which is more credits.

## 4. Re-check reliability per league

The calibrators and the confidence ceilings were fitted across all 40
competitions pooled, and validated before 25 of them ever had a fixture on the
card. The historical data covers them, so this is an evaluation rather than new
data — but "the pooled bands are calibrated" does not guarantee "the Chinese
Super League bands are calibrated", and the card now leans on exactly that.

Run `group_summary` split by league tier (top division / second tier / outside
the main 22) and check the top bands hold. If a tier overstates, it needs its
own ceiling. This is a half-day of analysis and it protects the number you are
about to sell.

## 5. Which APIs are worth paying for

**The Odds API — yes, first, and it is the only clear one.** It is the binding
constraint on everything above. Check current pricing, but the credit arithmetic
is what matters: daily fetches across 31 leagues need roughly 3,700 credits a
month, twice-daily (for closing-line value) roughly 7,400. Their paid tiers
start well inside that. Three things unlock together:

* daily recording, so the ledger has no gaps
* the **historical odds endpoints**, which are paid-only — those would let you
  reconstruct a real, priced track record instead of waiting for one
* more markets: BTTS and alternate totals get quoted prices, which turns most of
  the card's empty *Offered* and *Edge* columns into real numbers

**Lineups and injuries (API-Football, SportMonks) — only if you decide to chase
that edge deliberately.** The sibling project measured this model's errors as
correlating 0.989 with the market's, which says the market has already priced
everything in the historical record — including team news. The value in a
lineups feed is not better information, it is *speed*: betting in the minutes
after lineups drop, before the price moves. That is an automation project, not a
data purchase, and it is a different product from the one you have. Do not buy
it hoping the forecast improves on its own; it will not.

**xG providers (Opta, StatsBomb) — no.** Enterprise pricing for the thing that
is least likely to be the bottleneck. Shots on target already recovered most of
what xG offers here (it closed a fifth of the gap to the closing line), and the
remaining four fifths are not a data problem.

## 6. Selling subscriptions — the honest sequence

You said this comes after you validate the success rate yourself. That order is
right; here is what sits between.

**The gate is legal, not technical.** Selling betting tips for money in
Bulgaria/EU touches gambling advertising rules, consumer-protection law, and —
the moment money changes hands — company registration, terms of service, refund
policy and VAT on digital services. Check this before building a paywall, not
after. It is the step most likely to change the shape of the product.

**Sell the record, not the tips.** The market for tipsters is saturated with
unfalsifiable claims. What this project has that they do not is an append-only
ledger and a reliability page that can embarrass it. Publishing the full
historical record for free, permanently, and paywalling only the *forward*
picks, is both the more credible product and the marketing for it.

**Technically, keep the compute at home.** The pipeline is heavy and CPU-bound;
nothing about it wants to run on a web host. `fb.py export` already produces the
exact static site a host would serve, so the shape is: local machine builds,
pushes artifacts, a static host serves them, a membership layer gates the
forward-looking pages.

**Payments.** Stripe is the default and is excellent, but as a solo operator
selling digital subscriptions into the EU you inherit VAT MOSS obligations
yourself. A merchant of record — Paddle or Lemon Squeezy — takes a larger cut
and becomes the seller of record, handling EU VAT entirely. For a first product
at small volume that trade is usually worth it; revisit at scale.

**Do not sell early.** At one pick a day you will have ~30 settled bets after a
month, which is indistinguishable from noise at these prices, and a public
record that starts with a losing month is much harder to recover from than a
launch delayed by eight weeks. Sections 2 and 3 exist to make the wait shorter,
not to skip it.
