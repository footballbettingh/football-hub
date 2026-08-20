"""Value Bets — a Poisson model, an honest backtest, and a static site.

Layered so each piece can be tested without the one above it:

    config          settings + API keys from .env
    teams           provider-neutral team-name keys
    model           the Poisson/Dixon-Coles model
    sources.*       one module per external data provider
    backtest        walk-forward simulation over matches + odds
    insights        derived findings (calibration, edge decay, best pick)
    site.*          static multi-page HTML build

Nothing below `sources` touches the network; nothing below `site` renders HTML.
"""

__version__ = "0.3.0"
