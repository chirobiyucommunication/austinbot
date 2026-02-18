from __future__ import annotations

from datetime import datetime

from .models import (
    BotSettings,
    LifecycleState,
    SessionStats,
    StopReason,
    TradeOutcome,
    TradeRecord,
)
from .risk_engine import RiskEngine


class SessionEngine:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self.risk = RiskEngine(settings)
        self.stats = SessionStats()

    def start(self) -> None:
        if self.stats.state == LifecycleState.RUNNING:
            return
        self.stats = SessionStats(
            state=LifecycleState.RUNNING,
            start_balance=self.settings.trade_capital,
            current_stake=self.settings.trade_amount,
            current_mode=self.settings.mode,
            current_direction=self.settings.slide_direction,
            started_at=datetime.utcnow(),
        )

    def pause(self) -> None:
        if self.stats.state == LifecycleState.RUNNING:
            self.stats.state = LifecycleState.PAUSED

    def resume(self) -> None:
        if self.stats.state == LifecycleState.PAUSED:
            self.stats.state = LifecycleState.RUNNING

    def stop(self, reason: StopReason = StopReason.USER_STOP) -> None:
        self.stats.state = LifecycleState.STOPPED
        self.stats.stop_reason = reason
        self.stats.stopped_at = datetime.utcnow()

    def apply_trade_outcome(self, outcome: TradeOutcome, pair: str = "OTC") -> TradeRecord:
        if self.stats.state != LifecycleState.RUNNING:
            raise RuntimeError("Session is not running")

        stake = self.stats.current_stake
        pnl = round(stake * self.settings.payout_rate, 2) if outcome == TradeOutcome.WIN else -stake
        self.stats.trades_taken += 1
        self.stats.session_profit = round(self.stats.session_profit + pnl, 2)

        if outcome == TradeOutcome.WIN:
            self.stats.wins += 1
            self.stats.loss_streak = 0
            self.stats.martingale_step = 0
        else:
            self.stats.losses += 1
            self.stats.loss_streak += 1
            self.stats.martingale_step += 1

        record = TradeRecord(
            pair=pair,
            direction=self.stats.current_direction,
            stake=stake,
            expiry=self.settings.time_period,
            outcome=outcome,
            pnl=pnl,
        )

        self._enforce_stop_rules(outcome)
        return record

    def _enforce_stop_rules(self, outcome: TradeOutcome) -> None:
        if self.stats.session_profit >= self.settings.target_profit:
            self.stop(StopReason.TARGET_PROFIT_REACHED)
            return

        if outcome == TradeOutcome.LOSS and self.risk.martingale_stop_triggered(self.stats.martingale_step):
            self.stop(StopReason.MARTINGALE_LIMIT_REACHED)
            return

        if self.stats.state != LifecycleState.RUNNING:
            return

        remaining = self.settings.trade_capital + self.stats.session_profit
        next_stake = self.risk.next_stake(
            base_stake=self.settings.trade_amount,
            last_stake=self.stats.current_stake,
            last_was_loss=(outcome == TradeOutcome.LOSS),
        )

        if self.risk.exceeds_capital_guardrail(next_stake, remaining):
            self.stop(self.risk.guardrail_reason())
            return

        self.stats.current_stake = next_stake
