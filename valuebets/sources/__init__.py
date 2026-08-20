"""External data providers. One module per provider, each responsible for its
own auth, rate limiting, quota accounting and raw-response caching.

    football_data_org   match results + fixtures (API key, 10 req/min)
    odds_api            current/upcoming odds (API key, 500 credits/month)
    football_data_uk    historical CLOSING odds (free CSVs, no key)

Every fetcher returns a DataFrame in the project's own schema, so callers never
see provider-specific field names.
"""
