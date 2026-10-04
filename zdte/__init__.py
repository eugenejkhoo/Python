"""Rule-based 0DTE options trading bots.

The package is organised as a small pipeline:

    data feed  ->  indicators  ->  signals  ->  strategy  ->  risk  ->  broker
                                                      \\-> ledger -> dashboard

Nothing here is AI or machine learning. Every decision is a fixed rule
evaluated once per completed 1-minute candle.
"""

__version__ = "0.1.0"
