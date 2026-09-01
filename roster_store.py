from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy
from pathlib import Path


ALLOWED_ROSTER_GROUPS = {
    "Value & Contrarian",
    "Quality Growth",
    "Technology & Innovation",
    "Opportunistic & Concentrated",
    "Diversified & Systematic",
}


def normalize_cik(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits or len(digits) > 10:
        raise ValueError(f"Invalid SEC CIK: {value!r}")
    return digits.zfill(10)


def fund_fingerprint(fund: dict) -> str:
    payload = {
        "cik": fund["cik"],
        "historical_ciks": fund.get("historical_ciks", []),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_roster(entries: list[dict]) -> list[dict]:
    if not isinstance(entries, list):
        raise ValueError("Roster data must be a list")

    normalized = []
    seen = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("Each roster entry must be an object")
        cik = normalize_cik(raw.get("cik", ""))
        if cik in seen:
            raise ValueError(f"Duplicate roster CIK: {cik}")
        seen.add(cik)

        group = str(raw.get("group", "")).strip()
        if group not in ALLOWED_ROSTER_GROUPS:
            raise ValueError(f"Unsupported roster group for {cik}: {group!r}")

        name = str(raw.get("name", "")).strip()
        manager = str(raw.get("manager", "")).strip()
        if not name or not manager:
            raise ValueError(f"Roster entry {cik} requires name and manager")

        historical_ciks = []
        for historical_cik in raw.get("historical_ciks", []):
            value = normalize_cik(historical_cik)
            if value != cik and value not in historical_ciks:
                historical_ciks.append(value)

        entry = {
            "group": group,
            "cik": cik,
            "name": name,
            "manager": manager,
            "annotation": str(raw.get("annotation", "")).strip(),
            "is_exception": bool(raw.get("is_exception", False)),
            "roster_reason": str(raw.get("roster_reason", "")).strip(),
        }
        if historical_ciks:
            entry["historical_ciks"] = historical_ciks
        normalized.append(entry)
    return normalized


def load_roster(path: str | Path) -> list[dict]:
    roster_path = Path(path)
    if not roster_path.exists():
        raise FileNotFoundError(f"Roster configuration not found: {roster_path}")
    return validate_roster(
        json.loads(roster_path.read_text(encoding="utf-8"))
    )


class RosterStore:
    def __init__(
        self,
        path: str | Path,
        runtime_roster: list[dict],
        archive_path: str | Path | None = None,
    ):
        self.path = Path(path)
        self.archive_path = (
            Path(archive_path)
            if archive_path is not None
            else self.path.with_name("roster_archive.json")
        )
        self.runtime_roster = runtime_roster
        self._lock = threading.RLock()
        self.archived_by_cik = self._load_archive()
        self.runtime_roster[:] = load_roster(self.path)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return deepcopy(self.runtime_roster)

    def upsert_many(self, entries: list[dict]) -> dict:
        with self._lock:
            current = self.snapshot()
            by_cik = {item["cik"]: item for item in current}
            order = [item["cik"] for item in current]
            added = []
            updated = []
            for entry in validate_roster(entries):
                cik = entry["cik"]
                archived = self.archived_by_cik.get(cik)
                if archived and not entry.get("historical_ciks"):
                    entry = {
                        **entry,
                        **(
                            {
                                "historical_ciks": archived[
                                    "historical_ciks"
                                ]
                            }
                            if archived.get("historical_ciks")
                            else {}
                        ),
                    }
                if cik in by_cik:
                    updated.append(cik)
                else:
                    order.append(cik)
                    added.append(cik)
                by_cik[cik] = entry
                self.archived_by_cik.pop(cik, None)
            roster = [by_cik[cik] for cik in order]
            self._persist(roster)
            self._persist_archive()
            return {"added": added, "updated": updated, "roster": roster}

    def remove_many(self, ciks: list[str]) -> dict:
        normalized = {normalize_cik(cik) for cik in ciks}
        with self._lock:
            current = self.snapshot()
            removed = [
                item["cik"] for item in current
                if item["cik"] in normalized
            ]
            for item in current:
                if item["cik"] in normalized:
                    self.archived_by_cik[item["cik"]] = deepcopy(item)
            roster = [
                item for item in current
                if item["cik"] not in normalized
            ]
            self._persist(roster)
            self._persist_archive()
            return {"removed": removed, "roster": roster}

    def _load_archive(self) -> dict[str, dict]:
        if not self.archive_path.is_file():
            return {}
        archived = validate_roster(
            json.loads(self.archive_path.read_text(encoding="utf-8"))
        )
        return {item["cik"]: item for item in archived}

    def _persist_archive(self) -> None:
        archived = [
            self.archived_by_cik[cik]
            for cik in sorted(self.archived_by_cik)
        ]
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.archive_path.with_suffix(
            f"{self.archive_path.suffix}.tmp"
        )
        temporary_path.write_text(
            json.dumps(archived, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.archive_path)

    def _persist(self, roster: list[dict]) -> None:
        validated = validate_roster(roster)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(validated, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, self.path)
        self.runtime_roster[:] = deepcopy(validated)
