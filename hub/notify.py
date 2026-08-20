"""Telegram notifications — the day's pick, pushed rather than pulled.

Everything else here is pull: you open the site, or you run a command, and the
card tells you what it thinks. That works right up until the point of the thing,
which is a bet placed before a match kicks off. This sends the same numbers the
Best pick panel shows to a phone, once a run has finished.

It reads `picks.json`, the artifact `fb.py card` already writes, so notifying
re-computes nothing and cannot drift from what the site displays.

Missing credentials are not an error. A refresh that cannot notify should still
have produced a card, so `notify` reports what happened and the caller decides
whether to care.
"""

import json
from datetime import datetime, timezone

import requests

from valuebets import config

from .artifacts import DATA_DIR, load_picks

USER_AGENT = "football-hub/1.0"
TIMEOUT = 20

STATE_PATH = DATA_DIR / "notify_state.json"

BAND_LABEL = {"safe": "safe", "main": "main", "value": "value"}


class NotifyError(RuntimeError):
    """Telegram refused the request, or the network never reached it."""


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

def chat_ids(raw=None):
    """One chat per id; a comma-separated list fans the pick out to several."""
    raw = config.TELEGRAM_CHAT_ID if raw is None else raw
    return [c.strip() for c in str(raw).split(",") if c.strip()]


def configured(token=None, chats=None):
    token = token or config.TELEGRAM_BOT_TOKEN
    return bool(token and chat_ids(chats))


def _api(method, token=None, payload=None):
    token = token or config.TELEGRAM_BOT_TOKEN
    if not token:
        raise NotifyError("No TELEGRAM_BOT_TOKEN set. Add it to .env.")
    url = f"{config.TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        resp = requests.post(url, json=payload or {}, timeout=TIMEOUT,
                             headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:            # DNS, TLS, timeout
        raise NotifyError(f"Telegram unreachable: {exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        raise NotifyError(f"Telegram returned HTTP {resp.status_code}: "
                          f"{resp.text[:200]}") from None
    if not body.get("ok"):
        # 401 = bad token. 400 "chat not found" = nobody ever messaged the bot.
        # 403 "need administrator rights" = a channel the bot is only a member of.
        raise NotifyError(f"Telegram refused the request ({resp.status_code}): "
                          f"{body.get('description', body)}")
    return body["result"]


def send_message(text, token=None, chats=None, parse_mode="HTML"):
    """Send one message to every configured chat. Returns the ids reached."""
    targets = chat_ids(chats)
    if not targets:
        raise NotifyError("No TELEGRAM_CHAT_ID set. Run: python fb.py telegram --whoami")
    sent = []
    for chat in targets:
        _api("sendMessage", token, {
            "chat_id": chat, "text": text, "parse_mode": parse_mode,
            "disable_web_page_preview": True})
        sent.append(chat)
    return sent


def get_me(token=None):
    """Identify the bot behind the token. The cheapest proof the token works."""
    return _api("getMe", token)


def discover_chats(token=None):
    """Chats that have messaged the bot, so the id never has to be guessed.

    Telegram will not tell a bot who its audience is; it only replays recent
    updates. So: message the bot once from the chat you want, then run this.
    A channel has to have the bot as an administrator before it appears at all.
    """
    seen, order = {}, []
    for update in _api("getUpdates", token, {"timeout": 0}):
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if not chat or chat["id"] in seen:
                continue
            name = (chat.get("title")
                    or " ".join(filter(None, [chat.get("first_name"),
                                              chat.get("last_name")]))
                    or chat.get("username") or "")
            seen[chat["id"]] = {"id": chat["id"], "type": chat.get("type", "?"),
                                "name": name}
            order.append(seen[chat["id"]])
    return order


# --------------------------------------------------------------------------
# the message
# --------------------------------------------------------------------------

def _e(value):
    """Telegram's HTML parse mode: only these three characters need escaping."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _price(pick):
    """Fair price, plus the offered one when a bookmaker has actually quoted it."""
    fair = pick.get("fair_odds")
    line = f"fair {fair:.2f}" if fair else ""
    if pick.get("odds"):
        line += f" · offered <b>{pick['odds']:.2f}</b>"
    if pick.get("edge") is not None:
        line += f" · edge {pick['edge']:+.1%}"
    return line


def fingerprint(payload):
    """What makes a pick *the same* pick, for the purpose of not repeating it.

    Prices and probabilities move a little between refreshes and that is not
    news. A different day, match, market or selection is.
    """
    pick = (payload or {}).get("best_pick") or {}
    return "|".join(str(pick.get(k, "")) for k in
                    ("day", "competition", "match", "key", "selection"))


def _record_line(ledger_summary):
    """The ledger's own verdict, so the message cannot be more confident than
    the record it comes from."""
    if not ledger_summary or not ledger_summary.get("settled"):
        return None
    head = ledger_summary
    line = f"Record {head['wins']}-{head['losses']}"
    if head.get("pending"):
        line += f", {head['pending']} pending"
    if head.get("hit_rate") is not None and head.get("expected") is not None:
        line += (f" · landed {head['hit_rate']:.1%} against "
                 f"{head['expected']:.1%} claimed")
    return line


def format_picks(payload, ledger_summary=None, full=False):
    """The pick, in three lines.

    `payload` is picks.json as written by `fb.py card`.

    Three lines is the whole design. A notification is read on a lock screen
    and answers one question — what is the bet — so the band, the reliability
    footnote, the other two bands and the accumulator all belong on the page
    that has room for them. `full=True` restores them for anyone who wants the
    long version.
    """
    if not payload or not payload.get("best_pick"):
        return ("<b>No best pick today</b> — nothing on the next match day "
                "priced into the best-pick band.")

    best = payload["best_pick"]
    lines = [
        f"⚽ <b>Best pick of the day</b> · {_e(best.get('day', ''))}",
        f"<b>{_e(best.get('match', '?'))}</b> — "
        f"{_e(best.get('competition_name') or best.get('competition', '?'))}",
        f"<b>{_e(best.get('selection', '?'))}</b> — Confidence "
        f"<b>{best.get('prob', 0):.1%}</b> · {_price(best)}",
    ]
    if not full:
        return "\n".join(lines)

    low, high = payload.get("best_band", [1.6, 2.2])
    lines.insert(2, f"<i>band {low:g}–{high:g}</i>")
    if best.get("hit_rate") is not None:
        lines.append(
            f"<i>This band has landed {best['hit_rate']:.1%} over "
            f"{int(best.get('hit_rate_n') or 0):,} historical bets</i>")
    lines += _slate_block(payload, best)
    lines += _acca_block(payload)

    record = _record_line(ledger_summary)
    if record:
        lines += ["", f"<i>{_e(record)}</i>"]
    return "\n".join(lines)


def _slate_block(payload, best):
    """The other price bands on the same day.

    The flagship is one of three deliberately: they test the forecast at three
    confidence levels. Sending only the middle one would hide two thirds of
    what the card actually claims today.
    """
    same_day = [p for p in (payload.get("slate") or [])
                if p.get("day") == best.get("day") and p.get("band") != best.get("band")]
    if not same_day:
        return []
    out = ["", "<b>Also today</b>"]
    for pick in same_day:
        offered = f" · offered {pick['odds']:.2f}" if pick.get("odds") else ""
        out.append(f"{BAND_LABEL.get(pick['band'], pick['band'])} · "
                   f"{_e(pick['match'])} — {_e(pick['selection'])} · "
                   f"{pick.get('prob', 0):.1%} @ {pick.get('fair_odds', 0):.2f}{offered}")
    return out


def _acca_block(payload):
    acca = (payload.get("accumulators") or {}).get(payload.get("acca_default"))
    if not acca:
        return []
    legs = acca.get("selections") or []
    out = ["", f"<b>Accumulator</b> · {len(legs)} legs"]
    for leg in legs:
        out.append(f"{_e(leg['date'])} · {_e(leg['match'])} — "
                   f"{_e(leg['selection'])} · {leg.get('prob', 0):.1%} "
                   f"@ {leg.get('fair_odds', 0):.2f}")
    combined = (f"Combined <b>{acca.get('probability', 0):.1%}</b> · "
                f"fair {acca.get('fair_odds', 0):.2f}")
    if acca.get("offered_odds"):
        combined += f" · offered {acca['offered_odds']:.2f}"
    out.append(combined)
    return out


# --------------------------------------------------------------------------
# disk
# --------------------------------------------------------------------------

def ledger_summary():
    """The daily pick's record, or None if there is nothing graded yet.

    Wrapped because a notification must not fail over its own footnote.
    """
    try:
        from . import ledger
        frame = ledger.load()
        return None if frame.empty else ledger.summary(frame)
    except Exception:                                    # noqa: BLE001
        return None


def _state(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:                                   # truncated by a crash
        return {}


def notify(payload=None, only_if_changed=False, dry_run=False, token=None,
           chats=None, state_path=None, full=False):
    """Send the current best pick.

    Returns a dict whose `status` is one of: sent, unchanged, dry-run,
    unconfigured, no-card. Only `sent` touched the network.
    """
    payload = load_picks() if payload is None else payload
    state_path = state_path or STATE_PATH
    if payload is None:
        return {"status": "no-card", "text": "", "fingerprint": "", "chats": []}

    text = format_picks(payload, ledger_summary() if full else None,
                        full=full)
    mark = fingerprint(payload)
    result = {"status": None, "text": text, "fingerprint": mark, "chats": []}

    if only_if_changed and _state(state_path).get("fingerprint") == mark:
        result["status"] = "unchanged"
        return result
    if dry_run:
        result["status"] = "dry-run"
        return result
    if not configured(token, chats):
        result["status"] = "unconfigured"
        return result

    result["chats"] = send_message(text, token=token, chats=chats)
    result["status"] = "sent"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(
        {"fingerprint": mark, "chats": result["chats"],
         "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        indent=2), encoding="utf-8")
    return result
