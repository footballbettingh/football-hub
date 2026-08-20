"""Central config. Reads .env once, exposes typed settings.

Keys live in .env (gitignored), never in source. Environment variables that
are already set win over .env, so CI/prod can override without editing files.

python-dotenv is used when installed; there's a small fallback parser so a
missing dependency degrades to "still works" rather than "import error".
"""

import os
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


FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Telegram: @BotFather issues the token, the chat id comes from
# `python fb.py telegram --whoami`. Both empty means a refresh still runs and
# simply skips the notification.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
TELEGRAM_API_BASE = "https://api.telegram.org"

# Free tier: 10 requests/minute. The API reports what's left in
# X-Requests-Available-Minute and seconds-to-reset in X-RequestCounter-Reset,
# so fetch_data.py steers by those headers rather than a blind sleep.
FOOTBALL_DATA_RATE_LIMIT = 10

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
