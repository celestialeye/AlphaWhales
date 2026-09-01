import pandas as pd

from data_service import DataService


def _build_service():
    service = DataService.__new__(DataService)
    service.ticker_market_cache = {}
    service.cache = {
        "fund": {
            "status": "loaded",
            "metadata": {"report_period": "2026-06-30"},
        }
    }
    service.market_insights = {
        "SPGI": {
            "current_price": 442.89,
            "low_52_week": 361.03,
            "low_52_week_date": "2026-04-07",
            "pct_above_low": 22.67,
            "price_as_of": "2026-08-28",
            "quarter_market_metrics": {
                "2026-06-30": {
                    "current_price": 385.30,
                    "low_52_week": 361.03,
                    "pct_above_low": 6.72,
                },
                "2026-03-31": {
                    "current_price": 402.40,
                    "low_52_week": 361.03,
                    "pct_above_low": 11.46,
                },
            },
        }
    }
    service.get_ticker_view = lambda fund_cache=None: [
        {
            "ticker": "SPGI",
            "issuer": "S&P Global Inc",
            "num_holders": 7,
            "holders": [{"portfolio_weight": 19.92}],
        }
    ]
    return service


def test_near_52_week_low_uses_latest_market_data_for_current_cache():
    service = _build_service()

    result = service.get_near_52_week_low()

    assert result[0]["current_price"] == 442.89
    assert result[0]["low_52_week"] == 361.03
    assert result[0]["low_52_week_date"] == "2026-04-07"
    assert result[0]["pct_above_low"] == 22.67
    assert result[0]["price_as_of"] == "2026-08-28"


def test_near_52_week_low_uses_latest_market_data_for_historical_cache():
    service = _build_service()
    historical_cache = {
        "fund": {
            "status": "loaded",
            "metadata": {"report_period": "2026-03-31"},
        }
    }

    result = service.get_near_52_week_low(fund_cache=historical_cache)

    assert result[0]["current_price"] == 442.89
    assert result[0]["low_52_week"] == 361.03
    assert result[0]["low_52_week_date"] == "2026-04-07"
    assert result[0]["pct_above_low"] == 22.67
    assert result[0]["price_as_of"] == "2026-08-28"


def test_serialize_price_history_sorts_deduplicates_and_rounds():
    prices = pd.DataFrame({
        "date": ["2026-08-28", "2026-08-27", "2026-08-28", None],
        "close": [513.531, 505.06, 513.529, 999.0],
    })

    result = DataService._serialize_price_history(prices)

    assert result == [
        {"date": "2026-08-27", "close": 505.06},
        {"date": "2026-08-28", "close": 513.53},
    ]


def test_serialize_company_news_sorts_deduplicates_and_rejects_unsafe_urls():
    result = DataService._serialize_company_news([
        {
            "date": "2026-08-30T12:00:00Z",
            "title": " Broadcom launches new AI chip ",
            "url": "https://example.com/earlier",
            "source": "Example",
            "summary": " Earlier summary ",
        },
        {
            "date": "2026-08-31T14:00:00Z",
            "title": "AVGO reports latest quarterly earnings",
            "url": "https://example.com/latest",
            "source": "Example News",
            "summary": "Latest summary",
        },
        {
            "date": "2026-08-31T13:00:00Z",
            "title": "Broadcom announces acquisition",
            "url": "javascript:alert(1)",
        },
        {
            "date": "2026-08-31T14:00:00Z",
            "title": "AVGO reports latest quarterly earnings",
            "url": "https://example.com/latest",
            "source": "Duplicate",
        },
        {
            "date": "2026-08-31T15:00:00Z",
            "title": "Meta changes teen safety controls",
            "url": "https://example.com/unrelated",
            "summary": "Broadcom supplies chips to many technology companies.",
        },
        {
            "date": "2026-08-31T16:00:00Z",
            "title": "Is Broadcom stock a buy?",
            "url": "https://example.com/opinion",
            "summary": "A valuation opinion without a company event.",
        },
    ], ticker="AVGO", company_name="Broadcom Inc.")

    assert result == [
        {
            "published_at": "2026-08-31T14:00:00Z",
            "title": "AVGO reports latest quarterly earnings",
            "source": "Example News",
            "url": "https://example.com/latest",
            "summary": "Latest summary",
        },
        {
            "published_at": "2026-08-30T12:00:00Z",
            "title": "Broadcom launches new AI chip",
            "source": "Example",
            "url": "https://example.com/earlier",
            "summary": "Earlier summary",
        },
    ]


def test_holding_market_context_compares_latest_and_reported_prices():
    service = _build_service()
    service._load_ticker_market_data_from_disk = lambda ticker: None

    result = service._get_holding_market_context("SPGI", 385.30)

    assert result == {
        "reported_price": 385.30,
        "current_price": 442.89,
        "current_vs_reported_pct": 14.95,
        "low_52_week": 361.03,
        "low_52_week_date": "2026-04-07",
        "pct_above_low": 22.67,
        "market_price_as_of": "2026-08-28",
    }
