"""Settings: config.toml for behaviour, environment (.env) for secrets."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

FACTIONS = ("alliance", "horde", "blackwater")


@dataclass(frozen=True)
class Fees:
    faction_cut: float = 0.05
    neutral_cut: float = 0.15
    deposit_rate: float = 0.30
    neutral_deposit_rate: float = 0.75
    min_deposit: int = 100
    listing_hours: int = 24


@dataclass(frozen=True)
class Tuning:
    market_fraction: float = 0.15       # "market price" = mean of the cheapest 15% of units
    lookback_days: float = 7
    stats_retention_days: float = 7
    max_gap_hours: float = 3.0          # longer gaps between snapshots: outcomes are unknowable
    noncommodity_tolerance: float = 1.2 # gear sells up to 20% above the cheapest survivor
    horizon_days: float = 1.0
    max_levels: int = 40
    dip_threshold: float = 0.25
    dip_target: float = 0.90            # resell a dip at 90% of the typical price
    min_history_snaps: int = 6
    min_profit: int = 100
    min_roi: float = 0.05
    export_min_sold: int = 3            # Blackwater sales seen before an EXPORT is suggested
    ladder_levels: int = 25             # order-book depth exported per item


@dataclass(frozen=True)
class Settings:
    root: Path
    region: str = "eu"
    ruleset: int = 2
    locale: str = "en_GB"
    home_faction: str = "alliance"
    houses: dict[str, int] = field(default_factory=lambda: {f: 0 for f in FACTIONS})
    fees: Fees = field(default_factory=Fees)
    tuning: Tuning = field(default_factory=Tuning)
    client_id: str = ""
    client_secret: str = ""

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "forever_ah.db"

    @property
    def dist_path(self) -> Path:
        return self.root / "dist"


def _pick(cls, raw: dict):
    """Build a dataclass from a TOML table, rejecting unknown keys so typos surface."""
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} setting(s) in config.toml: {sorted(unknown)}")
    return cls(**raw)


def load_settings(root: Path | None = None) -> Settings:
    root = Path(root or os.environ.get("FOREVER_AH_HOME") or Path.cwd()).resolve()
    load_dotenv(root / ".env")
    raw: dict = {}
    cfg = root / "config.toml"
    if cfg.exists():
        raw = tomllib.loads(cfg.read_text(encoding="utf-8"))
    houses = {f: 0 for f in FACTIONS}
    houses.update({k: int(v) for k, v in raw.get("houses", {}).items() if k in FACTIONS})
    home = str(raw.get("home_faction", "alliance")).lower()
    if home not in FACTIONS:
        raise ValueError(f"home_faction in config.toml must be one of {list(FACTIONS)}, not {home!r}")
    return Settings(
        root=root,
        region=str(raw.get("region", "eu")).lower(),
        ruleset=int(raw.get("ruleset", 2)),
        locale=str(raw.get("locale", "en_GB")),
        home_faction=home,
        houses=houses,
        fees=_pick(Fees, raw.get("fees", {})),
        tuning=_pick(Tuning, raw.get("tuning", {})),
        client_id=os.environ.get("BLIZZARD_CLIENT_ID", ""),
        client_secret=os.environ.get("BLIZZARD_CLIENT_SECRET", ""),
    )
