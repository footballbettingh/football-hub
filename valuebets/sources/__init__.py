"""External data providers. One module per provider, each responsible for its
own auth, rate limiting, quota accounting and raw-response caching.

    odds_api            current/upcoming odds (API key, 500 credits/month)
    football_data_uk    results with CLOSING odds, the main leagues (free CSVs)
    football_data_world the same for 16 more countries, poorer columns (free CSVs)

Every fetcher returns a DataFrame in the project's own schema, so callers never
see provider-specific field names.
"""
