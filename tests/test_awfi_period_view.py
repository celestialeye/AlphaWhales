import asyncio

import main


class _FakeDataService:
    market_last_updated = "2026-08-31"
    is_market_refreshing = False

    @staticmethod
    def get_available_periods(count=20):
        return ["2026-03-31", "2025-12-31"]

    @staticmethod
    async def get_period_cache(period):
        return {"period": period}

    @staticmethod
    def get_qoq_changes(**kwargs):
        return []

    @staticmethod
    def get_ticker_view(**kwargs):
        return [
            {
                "ticker": "AAA",
                "num_holders": 2,
                "total_value_across_funds": 10,
                "median_weight": 3,
            }
        ]

    @staticmethod
    def get_fund_status(**kwargs):
        return []

    @staticmethod
    def get_overview(**kwargs):
        return {"loaded_funds": 1, "total_funds": 1}

    @staticmethod
    def get_near_52_week_low(**kwargs):
        return []

    @staticmethod
    def get_period_cache_status(period):
        return {"state": "ready", "source": "memory"}


class _FakeAwfiService:
    @staticmethod
    def get_period_scores(period, **kwargs):
        return {
            "scores": {
                "AAA": {
                    "252": {
                        "score": 81,
                        "signal": "BUY",
                    }
                }
            },
            "metadata": {
                "state": "READY",
                "requested_period": period,
            },
        }

    @staticmethod
    def get_ticker_history(ticker):
        return [
            {
                "period": "2025-12-31",
                "scores": {"252": {"score": 40, "signal": "HOLD"}},
            }
        ]


def test_period_view_exposes_awfi_without_alpha_sentiment(monkeypatch):
    monkeypatch.setattr(main, "data_service", _FakeDataService())
    monkeypatch.setattr(main, "awfi_service", _FakeAwfiService())

    result = asyncio.run(main.api_period_view("2026-03-31"))

    assert result["awfi_metadata"]["state"] == "READY"
    assert result["tickers"][0]["awfi"]["252"]["score"] == 81
    assert "alpha_sentiment" not in result["tickers"][0]


def test_ticker_response_includes_awfi_horizons(monkeypatch):
    monkeypatch.setattr(main, "data_service", _FakeDataService())
    monkeypatch.setattr(main, "awfi_service", _FakeAwfiService())

    result = asyncio.run(main.api_ticker_specific("AAA"))

    assert result["data"]["awfi"]["252"]["signal"] == "BUY"
    assert result["data"]["awfi_metadata"]["state"] == "READY"
    assert result["data"]["awfi_history"][-1]["period"] == "2026-03-31"


def test_ticker_awfi_history_endpoint_reads_latest_published_snapshot(
    monkeypatch,
):
    monkeypatch.setattr(main, "awfi_service", _FakeAwfiService())

    result = asyncio.run(main.api_ticker_awfi_history(" aaa "))

    assert result["ticker"] == "AAA"
    assert result["history"][0]["period"] == "2025-12-31"
