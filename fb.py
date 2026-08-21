#!/usr/bin/env python
"""fb — Football Hub. One entry point for both halves.

    python fb.py serve            the site, with buttons (start here)
    python fb.py export           the same site as static files

Everything the buttons do is also a command, for scripting and scheduling:

    python fb.py fetch results    results + closing odds (free)
    python fb.py fetch leagues    which leagues could be priced (free)
    python fb.py fetch odds       upcoming prices (spends Odds API credits)
    python fb.py model            walk-forward over every finished match
    python fb.py calibrate        fit calibrators + reliability record
    python fb.py card             price the upcoming fixtures
    python fb.py best             best pick of the day + accumulator pick
    python fb.py history          what the daily pick has actually done
    python fb.py evidence         re-run the value-betting backtest
    python fb.py evaluate         reliability tables, printed
    python fb.py sweep            how much the closing line deserves

    python fb.py run              all of it unattended, then Telegram
    python fb.py notify           send the current best pick to Telegram
    python fb.py telegram         one-time Telegram setup check
"""

import argparse
import sys

import pandas as pd

# Windows consoles default to cp1252, which cannot encode a Turkish "s" with a
# cedilla — so printing a card containing Gazisehir Gaziantep killed the whole
# command with a UnicodeEncodeError. Team names come from twenty countries;
# the console has to speak UTF-8.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _show(frame, floats=4):
    if frame is None or len(frame) == 0:
        print("  (nothing to show)")
        return
    with pd.option_context("display.width", 200, "display.max_columns", 40,
                           "display.max_rows", 250,
                           "display.float_format", f"{{:.{floats}f}}".format):
        print(frame.to_string(index=False))


def cmd_serve(args):
    from hub.server import serve
    serve(port=args.port, open_browser=not args.no_open)


def cmd_export(args):
    from hub.export import export
    out = export(args.out)
    print(f"Static site -> {out}")
    print("Open index.html, or publish the folder as-is.")


def cmd_fetch(args):
    from hub import pipeline
    if args.what == "results":
        pipeline.fetch_results(refresh_all=args.all)
    elif args.what == "leagues":
        pipeline.discover_leagues()
    else:
        pipeline.fetch_odds(sports=args.sports.split(",") if args.sports else None)


def cmd_model(args):
    from hub import pipeline
    pipeline.rebuild_model(refit_days=args.refit_days,
                           competitions=args.competitions.split(",")
                           if args.competitions else None)


def cmd_calibrate(args):
    from hub import pipeline
    pipeline.recalibrate(weight=args.weight, folds=args.folds)


def cmd_card(args):
    from hub import card
    payload = card.build(weight=args.weight)
    rows = [r for r in payload["selections"]
            if r["prob"] >= args.min_confidence and r["validated"]]
    seen, shortlist = set(), []
    for row in sorted(rows, key=lambda r: -r["prob"]):
        if row["match"] in seen:
            continue
        seen.add(row["match"])
        shortlist.append(row)
    print(f"\n{len(shortlist)} fixtures with a selection at or above "
          f"{args.min_confidence:.0%}\n")
    _show(pd.DataFrame(shortlist[:args.limit])[
        ["date", "competition", "match", "selection", "prob", "fair_odds",
         "odds", "edge", "hit_rate"]])


def cmd_best(args):
    """The two headline picks, from the card already on disk."""
    from hub import artifacts
    payload = artifacts.load_picks()
    if not payload:
        raise SystemExit("No card yet — run `python fb.py card` first.")

    best = payload.get("best_pick")
    low, high = payload.get("best_band", [1.6, 2.2])
    print(f"\n== Best pick of the day  ({low:g}-{high:g})\n")
    if not best:
        print("  Nothing in that price range on the next match day.")
    else:
        print(f"  {best['match']}  ({best.get('competition_name') or best['competition']}"
              f", {best['date']})")
        print(f"  {best['selection']}")
        print(f"  confidence {best['prob']:.1%}   fair {best['fair_odds']:.2f}"
              + (f"   offered {best['odds']:.2f}" if best.get("odds") else "")
              + (f"   edge {best['edge']:+.1%}" if best.get("edge") is not None else ""))
        if best.get("hit_rate") is not None:
            print(f"  this band has landed {best['hit_rate']:.1%} over "
                  f"{int(best.get('hit_rate_n') or 0):,} historical bets")

    slate = payload.get("slate") or []
    if slate:
        days = sorted({pick["day"] for pick in slate})
        print(f"\n== The next {len(days)} match days\n")
        for day in days:
            print(f"  {day}")
            for pick in [p for p in slate if p["day"] == day]:
                offered = ("" if pick.get("odds") is None
                           else f"  offered {pick['odds']:.2f}")
                print(f"    {pick['band']:<6} {pick['match']:<44} "
                      f"{pick['selection']:<28} {pick['prob']:.1%} @ "
                      f"{pick['fair_odds']:.2f}{offered}")

    accas = payload.get("accumulators") or {}
    key = str(args.legs) if args.legs else payload.get("acca_default", "4")
    acca = accas.get(key)
    print(f"\n== Accumulator pick  ({key} legs, target "
          f"{payload.get('acca_target', 3.0):g})\n")
    if not acca:
        print("  No accumulator of that size clears the target.")
        return
    for leg in acca["selections"]:
        print(f"  {leg['date']}  {leg['match']:<44} {leg['selection']:<28} "
              f"{leg['prob']:.1%}  @ {leg['fair_odds']:.2f}")
    print(f"\n  combined chance {acca['probability']:.1%}   "
          f"fair {acca['fair_odds']:.2f}"
          + (f"   offered {acca['offered_odds']:.2f}" if acca.get("offered_odds") else ""))


def cmd_history(args):
    from hub import ledger
    frame = ledger.load()
    if frame.empty:
        raise SystemExit("No picks recorded yet — run `python fb.py card`.")

    head = ledger.summary(frame)
    columns = ["day", "band", "match", "selection", "prob", "odds", "outcome", "pnl"]
    _show(frame.sort_values(["day", "band"], ascending=False)[columns])
    for band in ledger.summary_by_band(frame):
        if band["settled"]:
            print(f"  {band['band']:<6} {band['wins']}-{band['losses']}"
                  f"   did {band['hit_rate']:.1%} vs said {band['expected']:.1%}")
    print(f"\n  record {head['wins']}-{head['losses']}"
          + (f" ({head['void']} void)" if head["void"] else "")
          + f", {head['pending']} pending")
    if head["hit_rate"] is not None:
        print(f"  hit rate {head['hit_rate']:.1%} against {head['expected']:.1%} claimed")
    if head["priced"]:
        print(f"  P&L {head['pnl']:+.2f} over {head['priced']} bets at 1 unit"
              f"  (ROI {head['roi']:+.1f}%)")
    if head["unpriced"]:
        print(f"  {head['unpriced']} settled pick(s) had no quoted price and are "
              "excluded from P&L")

    accas = ledger.load_accas()
    if not accas.empty:
        acca = ledger.acca_summary(accas)
        print(f"\n== Accumulator picks (a separate book)\n")
        _show(accas.sort_values("issued", ascending=False)[
            ["issued", "legs", "probability", "fair_odds", "outcome",
             "legs_won", "pnl"]])
        print(f"\n  record {acca['wins']}-{acca['losses']}, "
              f"{acca['pending']} pending")
        if acca["hit_rate"] is not None:
            print(f"  landed {acca['hit_rate']:.1%} against "
                  f"{acca['expected']:.1%} claimed")


def cmd_evidence(args):
    from hub import evidence
    payload = evidence.build()
    head = payload["summary"]
    print(f"\n{head['n']:,} bets, ROI {head['roi']:+.2f}% "
          f"95% CI [{head['ci'][0]:+.2f}%, {head['ci'][1]:+.2f}%] — {head['verdict']}")


def cmd_evaluate(args):
    from hub import pipeline
    tables = pipeline.evaluation_tables(weight=args.weight, folds=args.folds)
    print("\n== Does the number mean what it says? (out of sample)\n")
    _show(tables["reliability"])
    print("\n== By market group\n")
    _show(tables["groups"])
    print("\n== Against the closing line it is built from\n")
    _show(tables["versus_market"], floats=5)
    if len(tables["rps"]):
        print("\n== Ranked probability score on 1X2 (lower is better)\n")
        _show(tables["rps"], floats=5)
    print("\n== Internal consistency after calibrating each group separately\n")
    _show(tables["coherence"], floats=5)


def cmd_sweep(args):
    from hub import pipeline
    print("Fusion weight on the market-implied matrix "
          "(0 = model alone, 1 = closing line alone):\n")
    _show(pipeline.sweep_market_weight(folds=args.folds), floats=5)


# --------------------------------------------------------------------------
# unattended
# --------------------------------------------------------------------------

def _step(label, fn, required=False, skipped=None):
    """One stage of an unattended run: timed, and survivable unless required.

    A scheduled job that dies on a provider's bad afternoon refreshes nothing
    and says nothing. Optional stages log the failure and let the run continue
    on yesterday's copy of that data.
    """
    from datetime import datetime
    print(f"\n[{datetime.now():%H:%M:%S}] {label}")
    try:
        fn()
        return True
    except (Exception, SystemExit) as exc:        # SystemExit: the "no data" guards
        if required:
            raise
        print(f"  [!] skipped - {type(exc).__name__}: {exc}")
        if skipped is not None:
            skipped.append(label)
        return False


def _odds_age_days():
    """How long ago the prices were fetched, in days, or None if never.

    The card takes its fixture list from the price files, and they reach about
    twelve days ahead — so they do not need refreshing daily, and cannot afford
    to be. Thirty-one leagues at four credits is ~124 a fetch against a free
    tier of 500 a month: every seven days would be 539, every eight is 471.
    Hence a threshold rather than a schedule; a run that finds fresh prices
    spends nothing and still re-prices the card against the new results.

    Read from `fetched_at` inside the files rather than their timestamps on
    disk, because a modification time says when this machine last wrote the
    file, not when the prices were taken. On a CI runner the two disagree
    completely: a fresh checkout has no files at all, so every run would look
    overdue and spend the month's credits in a week, while a restored cache
    would look newly written and never refresh at all.
    """
    from datetime import datetime, timezone

    import pandas as pd

    from valuebets import config

    newest = None
    for path in config.DATA_DIR.glob("odds_*.csv"):
        try:
            stamps = pd.to_datetime(pd.read_csv(path, usecols=["fetched_at"])
                                    ["fetched_at"], errors="coerce", utc=True)
        except (ValueError, KeyError, OSError):
            continue                   # a file without the column tells us nothing
        latest = stamps.max()
        if pd.notna(latest) and (newest is None or latest > newest):
            newest = latest
    if newest is None:
        return None                    # nothing on file: fetching is the point
    return (datetime.now(timezone.utc) - newest.to_pydatetime()).total_seconds() / 86400


def _evidence_is_stale():
    """Whether the value-betting evidence is behind the results behind it.

    Judged on the number of rows in history.csv, which the artifact records
    when it is built -- not on either file's modification time. A runner
    restores both from a cache, so their timestamps say when a tarball was
    unpacked rather than when the numbers were worked out, and an evidence
    page that never rebuilds looks exactly like one that is up to date.

    An artifact written before this was recorded has no `source_rows`, and is
    treated as stale so it is brought forward once.
    """
    import json

    from hub.artifacts import EVIDENCE_JSON
    from hub.evidence import _history_rows

    if not EVIDENCE_JSON.exists():
        return True
    try:
        built_from = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8")).get("source_rows")
    except (ValueError, OSError):
        return True
    return built_from is None or int(built_from) != _history_rows()


def cmd_run(args):
    """Everything the buttons do, in dependency order, with nobody watching.

    The order is the one `full-refresh` uses, with prices in front of it: the
    card reads its fixture list out of the odds files, so without a fetch the
    upcoming matches all eventually kick off and the card has nothing to price.
    """
    from datetime import datetime
    from hub import card, evidence, notify as tg, pipeline

    started, skipped = datetime.now(), []
    print(f"=== Football Hub run {started:%Y-%m-%d %H:%M:%S} ===")

    if not args.skip_fetch:
        _step("Fetching results and closing odds",
              lambda: pipeline.fetch_results(), skipped=skipped)

        if args.no_odds:
            print("\nSkipping prices (--no-odds); the card will use the fixtures "
                  "already on file.")
        else:
            age = _odds_age_days()
            if age is not None and age < args.odds_every:
                print(f"\nPrices are {age:.1f} days old and the threshold is "
                      f"{args.odds_every}; not spending credits today. "
                      f"Force with --odds-every 0.")
            else:
                sports = args.sports.split(",") if args.sports else None
                # A machine that has never fetched has no league plan and no
                # price files, so fetch_odds would refuse with advice meant for
                # a person at a keyboard. Discovery costs zero credits, so an
                # unattended run can simply do it and carry on.
                if not sports and not pipeline.league_plan():
                    _step("Working out which leagues can be priced (free)",
                          lambda: pipeline.discover_leagues(), skipped=skipped)
                _step("Fetching prices",
                      lambda: pipeline.fetch_odds(sports=sports), skipped=skipped)

    if args.skip_model:
        print("\nSkipping the model rebuild (--skip-model).")
    else:
        _step("Rebuilding the model", lambda: pipeline.rebuild_model(), skipped=skipped)
        _step("Recalibrating", lambda: pipeline.recalibrate(), skipped=skipped)

    # Required: the card is what a notification is about. Sending yesterday's
    # pick because today's build failed is worse than sending nothing.
    _step("Pricing the card", lambda: card.build(), required=True)

    if args.no_notify:
        print("\nSkipping Telegram (--no-notify).")
    else:
        _step("Sending the best pick to Telegram",
              lambda: _notify(tg, only_if_changed=args.only_if_changed),
              skipped=skipped)

    if args.no_evidence:
        print("\nSkipping the evidence rebuild (--no-evidence).")
    elif args.force_evidence or _evidence_is_stale():
        # Free, but the slowest thing here, which is why it runs after the
        # notification rather than in front of it.
        _step("Rebuilding the value-betting evidence (no credits, ~10 min)",
              lambda: evidence.build(), skipped=skipped)
    else:
        print("\nEvidence is current with the results on file; not rebuilding.")

    # Exit 0 either way: the card was rebuilt, which is what the run is for.
    # The tally is here so a skimmed log shows a degraded run at a glance.
    minutes = (datetime.now() - started).total_seconds() / 60
    tail = f", {len(skipped)} skipped: {'; '.join(skipped)}" if skipped else ""
    print(f"\n=== done in {minutes:.1f} min{tail} ===")


def _notify(tg, only_if_changed=False, dry_run=False, full=False):
    result = tg.notify(only_if_changed=only_if_changed, dry_run=dry_run, full=full)
    print({
        "sent": lambda: f"  sent to {', '.join(result['chats'])}",
        "unchanged": lambda: "  same pick as last time - not sent again "
                             "(drop --only-if-changed to send anyway)",
        "dry-run": lambda: "  dry run, nothing sent:\n\n" + result["text"],
        "unconfigured": lambda: "  [!] no Telegram credentials. Add "
                                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env "
                                "(see: python fb.py telegram --whoami)",
        "no-card": lambda: "  [!] no card yet - run `python fb.py card` first",
    }[result["status"]]())
    return result


def cmd_notify(args):
    from hub import notify as tg
    # A missing card or a refused chat is a configuration problem, not a bug;
    # say what to do about it instead of printing a traceback.
    try:
        _notify(tg, only_if_changed=args.only_if_changed, dry_run=args.dry_run,
                full=args.full)
    except tg.NotifyError as exc:
        raise SystemExit(str(exc))


def cmd_telegram(args):
    from hub import notify as tg
    try:
        _telegram(tg, args)
    except tg.NotifyError as exc:
        raise SystemExit(str(exc))


def _telegram(tg, args):
    """One-time setup helper: prove the token works and find the chat id."""
    from valuebets import config
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("No TELEGRAM_BOT_TOKEN in .env. Get one from @BotFather "
                         "in Telegram (/newbot), then paste it into .env.")

    me = tg.get_me()
    print(f"Bot: @{me.get('username')} ({me.get('first_name')})")

    if args.test:
        chats = tg.send_message("Football Hub is connected. The best pick will "
                                "arrive here after each run.")
        print(f"Test message sent to {', '.join(chats)}")
        return

    found = tg.discover_chats()
    if not found:
        print(f"\nNo chats yet. Open Telegram, send @{me.get('username')} any "
              f"message, then run this again.\nFor a channel, add the bot as an "
              f"ADMINISTRATOR (a channel will not accept it as a plain member) "
              f"and post once.")
        return
    print("\nChats that have messaged the bot:\n")
    for chat in found:
        print(f"  TELEGRAM_CHAT_ID={chat['id']:<16} {chat['type']:<10} {chat['name']}")
    print("\nPaste the id you want into .env, then: python fb.py telegram --test")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fb", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the local site")
    p.add_argument("--port", type=int, default=8756)
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("export", help="write the site as static files")
    p.add_argument("--out", default="site")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("fetch", help="download data")
    p.add_argument("what", choices=["results", "odds", "leagues"],
                   help="'leagues' only asks what is available — it is free")
    p.add_argument("--all", action="store_true",
                   help="re-download finished seasons too, not just the live one")
    p.add_argument("--sports", default=None,
                   help="comma-separated Odds API keys; default is the plan "
                        "from `fetch leagues`, or whatever is already tracked")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("model", help="walk-forward over every finished match")
    p.add_argument("--refit-days", type=int, default=None)
    p.add_argument("--competitions", default=None, help="comma-separated subset")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("calibrate", help="fit calibrators and the reliability record")
    p.add_argument("--weight", type=float, default=None)
    p.add_argument("--folds", type=int, default=5)
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("card", help="price the upcoming fixtures")
    p.add_argument("--weight", type=float, default=None)
    p.add_argument("--min-confidence", type=float, default=0.75)
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("best", help="best pick of the day + accumulator pick")
    p.add_argument("--legs", type=int, default=None, help="accumulator size")
    p.set_defaults(func=cmd_best)

    p = sub.add_parser("history", help="the daily pick's record and P&L")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("evidence", help="re-run the value-betting backtest")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("evaluate", help="reliability tables")
    p.add_argument("--weight", type=float, default=None)
    p.add_argument("--folds", type=int, default=5)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("sweep", help="sweep the market fusion weight")
    p.add_argument("--folds", type=int, default=5)
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("run", help="fetch, rebuild, re-price and notify")
    p.add_argument("--sports", default=None,
                   help="comma-separated Odds API keys; default is the tracked plan")
    p.add_argument("--skip-fetch", action="store_true",
                   help="rebuild and notify from the data already on disk")
    p.add_argument("--no-odds", action="store_true",
                   help="spend no Odds API credits this run")
    p.add_argument("--odds-every", type=float, default=8.0, metavar="DAYS",
                   help="only fetch prices when the ones on file are older than "
                        "this (default: 8, which fits the 500-credit free tier)")
    p.add_argument("--skip-model", action="store_true",
                   help="skip the walk-forward rebuild and recalibration")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--no-evidence", action="store_true",
                   help="skip the value-betting backtest even if it is behind")
    p.add_argument("--force-evidence", action="store_true",
                   help="rebuild the backtest even if it is already current")
    p.add_argument("--only-if-changed", action="store_true",
                   help="stay quiet when the pick is the same as last time")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("notify", help="send the current best pick to Telegram")
    p.add_argument("--only-if-changed", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="print the message instead of sending it")
    p.add_argument("--full", action="store_true",
                   help="the long version: band, reliability, the other bands, "
                        "the accumulator and the record")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("telegram", help="one-time Telegram setup check")
    p.add_argument("--whoami", action="store_true",
                   help="list chats that have messaged the bot (default)")
    p.add_argument("--test", action="store_true", help="send a test message")
    p.set_defaults(func=cmd_telegram)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
