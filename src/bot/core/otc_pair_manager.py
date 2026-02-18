from __future__ import annotations

from datetime import datetime

from .models import BotSettings


class OTCPairManager:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings

    def available_pairs(self) -> list[str]:
        return sorted(self.settings.pair_expiry_rules.keys())

    def enabled_pairs(self) -> list[str]:
        return list(self.settings.enabled_pairs)

    def is_pair_enabled(self, pair: str) -> bool:
        return pair in self.settings.enabled_pairs

    def is_expiry_allowed(self, pair: str, expiry: str) -> bool:
        allowed = self.settings.pair_expiry_rules.get(pair, [])
        return expiry.upper() in allowed

    def is_within_schedule(self, when: datetime) -> bool:
        if not self.settings.schedule_enabled:
            return True

        hour = when.hour
        start = self.settings.schedule_start_hour
        end = self.settings.schedule_end_hour

        if start <= end:
            return start <= hour <= end
        return hour >= start or hour <= end

    def can_trade(self, pair: str, expiry: str, when: datetime) -> bool:
        if not self.is_pair_enabled(pair):
            return False
        if not self.is_expiry_allowed(pair, expiry):
            return False
        return self.is_within_schedule(when)