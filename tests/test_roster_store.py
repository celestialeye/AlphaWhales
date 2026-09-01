from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_service import DataService
from roster_store import (
    ALLOWED_ROSTER_GROUPS,
    RosterStore,
    normalize_cik,
    validate_roster,
)


def roster_entry(cik: str, *, exception: bool = False) -> dict:
    return {
        "group": "Quality Growth",
        "cik": cik,
        "name": f"Manager {cik}",
        "manager": f"Manager {cik}",
        "annotation": "Screening selection",
        "is_exception": exception,
        "roster_reason": "Test",
    }


class RosterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "roster.json"
        self.path.write_text(
            json.dumps([roster_entry("1")]),
            encoding="utf-8",
        )
        self.runtime = []
        self.store = RosterStore(self.path, self.runtime)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_upsert_and_remove_update_disk_and_runtime_roster(self):
        result = self.store.upsert_many([
            roster_entry("2", exception=True),
        ])
        self.assertEqual(result["added"], ["0000000002"])
        self.assertEqual(len(self.runtime), 2)
        self.assertTrue(self.runtime[1]["is_exception"])

        result = self.store.remove_many(["1"])
        self.assertEqual(result["removed"], ["0000000001"])
        self.assertEqual(
            [item["cik"] for item in self.runtime],
            ["0000000002"],
        )
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted, self.runtime)

    def test_existing_entry_can_be_flagged_without_reordering(self):
        self.store.upsert_many([roster_entry("2")])
        self.store.upsert_many([roster_entry("1", exception=True)])
        self.assertEqual(
            [item["cik"] for item in self.runtime],
            ["0000000001", "0000000002"],
        )
        self.assertTrue(self.runtime[0]["is_exception"])

    def test_removed_identity_chain_is_restored_when_manager_is_readded(self):
        original = {
            **roster_entry("1"),
            "historical_ciks": ["2"],
        }
        self.store.upsert_many([original])
        self.store.remove_many(["1"])

        self.store.upsert_many([roster_entry("1")])

        self.assertEqual(
            self.runtime[0]["historical_ciks"],
            ["0000000002"],
        )
        archived = json.loads(
            self.store.archive_path.read_text(encoding="utf-8")
        )
        self.assertEqual(archived, [])

    def test_invalid_cik_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid SEC CIK"):
            normalize_cik("not-a-cik")

    def test_roster_uses_investment_style_groups(self):
        self.assertEqual(
            ALLOWED_ROSTER_GROUPS,
            {
                "Value & Contrarian",
                "Quality Growth",
                "Technology & Innovation",
                "Opportunistic & Concentrated",
                "Diversified & Systematic",
            },
        )
        legacy_entry = {
            **roster_entry("2"),
            "group": "2026 expansion",
        }
        with self.assertRaisesRegex(ValueError, "Unsupported roster group"):
            validate_roster([legacy_entry])

    def test_fund_fingerprint_changes_with_filing_chain(self):
        fund = roster_entry("1")
        changed = {**fund, "historical_ciks": ["2"]}
        self.assertNotEqual(
            DataService._fund_fingerprint(fund),
            DataService._fund_fingerprint(changed),
        )

    def test_busy_market_refresh_reports_no_refresh(self):
        service = DataService.__new__(DataService)
        service.is_market_refreshing = True
        self.assertFalse(asyncio.run(service.refresh_market_insights()))

    def test_added_manager_is_queued_during_another_refresh(self):
        service = DataService.__new__(DataService)
        service.cache = {"0000000001": {}}
        service.pending_roster_refresh_ciks = set()
        service.is_refreshing = True
        asyncio.run(service.refresh_funds(["0000000001"]))
        self.assertEqual(
            service.pending_roster_refresh_ciks,
            {"0000000001"},
        )

    def test_full_refresh_requests_are_coalesced_before_shared_lock(self):
        async def scenario():
            service = DataService.__new__(DataService)
            service.cache = {"0000000001": {}}
            service.is_refreshing = False
            service._full_refresh_pending = False
            service._refresh_lock = asyncio.Lock()
            service.last_updated = None
            refresh_count = 0

            async def refresh_fund(cik):
                nonlocal refresh_count
                refresh_count += 1

            async def refresh_market_insights():
                return True

            async def broadcast_event(event):
                return None

            service.refresh_fund = refresh_fund
            service.refresh_market_insights = refresh_market_insights
            service.broadcast_event = broadcast_event

            await service._refresh_lock.acquire()
            with patch(
                "data_service.FUND_MANAGERS",
                [{"cik": "0000000001"}],
            ):
                first = asyncio.create_task(service.refresh_all())
                await asyncio.sleep(0)
                second = asyncio.create_task(service.refresh_all())
                await asyncio.sleep(0)
                service._refresh_lock.release()
                results = await asyncio.gather(first, second)
            return results, refresh_count

        results, refresh_count = asyncio.run(scenario())

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(refresh_count, 1)


if __name__ == "__main__":
    unittest.main()
