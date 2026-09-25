"""Central config. Reads .env once, exposes typed settings.

Keys live in .env (gitignored), never in source. Environment variables that
are already set win over .env, so CI/prod can override without editing files.

python-dotenv is used when installed; there's a small fallback parser so a
missing dependency degrades to "still works" rather than "import error".
"""

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _load_env(path: Path) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


_load_env(ENV_PATH)


# Read by nothing now, but an old .env or CI secret may still set it, and it
# stays among SECRET_NAMES below so a stray print of it is scrubbed.
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Telegram: @BotFather issues the token, the chat id comes from
# `python fb.py telegram --whoami`. Both empty means a refresh still runs and
# simply skips the notification.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API_BASE = "https://api.telegram.org"

# Refuse to spend paid Odds API credits below this floor, so an accidental
# loop can't burn the whole monthly quota.
ODDS_API_MIN_CREDITS = int(os.environ.get("ODDS_API_MIN_CREDITS", "50"))

DATA_DIR = PROJECT_ROOT / os.environ.get("DATA_DIR", "data")
SITE_DIR = PROJECT_ROOT / os.environ.get("SITE_DIR", "site")
RAW_DIR = DATA_DIR / "raw"


def require(*names: str) -> None:
    """Fail fast with an actionable message instead of a 403 later."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            f"Missing config: {', '.join(missing)}.\n"
            f"Add them as KEY=value lines in {ENV_PATH}, or export them."
        )


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)


# -- keeping them out of the logs --------------------------------------------

# Two of the three credentials travel in the URL itself — the Odds API's as a
# query parameter, Telegram's in the path — and requests quotes the URL in the
# message of every error it raises. Printed, that message put the key into
# logs\run-*.log in plain text, and into the Actions log wherever the value
# did not happen to match a registered secret exactly.
SECRET_NAMES = ("ODDS_API_KEY", "TELEGRAM_BOT_TOKEN", "FOOTBALL_DATA_KEY")

# The shapes, for a key that is not the configured one: a Client handed its
# own, or a token pasted in by hand.
_SECRET_SHAPES = (
    re.compile(r"(apiKey=)[^&\s'\"]+"),
    re.compile(r"(/bot)\d+:[\w-]+"),
)


def redact(text, *also) -> str:
    """`text` with every credential in it replaced by ***.

    The configured values by value, anything shaped like one of them by shape,
    and `also` — a key a caller holds that config does not — by value too.
    """
    text = str(text)
    for value in [globals().get(name) for name in SECRET_NAMES] + list(also):
        if value:
            text = text.replace(str(value), "***")
    for shape in _SECRET_SHAPES:
        text = shape.sub(r"\1***", text)
    return text


def scrubbed(exc, *also):
    """The same kind of error with the credentials taken out of its message.

    Raise it `from None`: the original travels as the new one's context, and
    a traceback prints the context in full — URL, key and all.
    """
    message = redact(exc, *also)
    # The nearest class that can be built from a message alone, so a caller's
    # `except RequestException` still catches it: requests' JSONDecodeError
    # wants a document and a position as well, its parent does not.
    for kind in type(exc).__mro__:
        try:
            return kind(message)
        except TypeError:
            continue
    return RuntimeError(message)
