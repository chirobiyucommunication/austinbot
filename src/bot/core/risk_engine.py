from __future__ import annotations

from .models import BotSettings, StopReason


class RiskEngine:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings

    def next_stake(self, base_stake: float, last_stake: float, last_was_loss: bool) -> float:
        if self.settings.disable_martingale:
            return base_stake

        if not last_was_loss:
            return base_stake

        return round(last_stake * (1 + self.settings.martingale_percent / 100), 2)

    def martingale_stop_triggered(self, current_step: int) -> bool:
        if self.settings.disable_martingale:
            return False
        return current_step >= self.settings.martingale_limit and self.settings.martingale_limit > 0

    def exceeds_capital_guardrail(self, next_stake: float, remaining_capital: float) -> bool:
        return next_stake > remaining_capital

    @staticmethod
    def guardrail_reason() -> StopReason:
        return StopReason.CAPITAL_GUARDRAIL
