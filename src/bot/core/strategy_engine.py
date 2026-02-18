from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import BotMode, BotSettings, SlideDirection, TradeSignal
from .otc_pair_manager import OTCPairManager


@dataclass(slots=True)
class PairState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    cooldown_until: datetime | None = None
    active_trade_until: datetime | None = None


class StrategyEngine:
    def __init__(self, settings: BotSettings, pair_manager: OTCPairManager) -> None:
        self.settings = settings
        self.pair_manager = pair_manager
        self.states: dict[str, PairState] = {}
        self.rsi_period = 14
        self.short_ma_period = 5
        self.long_ma_period = 20

    def on_price(self, pair: str, price: float, timestamp: datetime | None = None) -> TradeSignal | None:
        now = timestamp or datetime.utcnow()
        if not self.pair_manager.can_trade(pair=pair, expiry=self.settings.time_period, when=now):
            return None

        state = self.states.setdefault(pair, PairState())
        state.prices.append(price)

        if state.active_trade_until and now < state.active_trade_until:
            return None

        if state.cooldown_until and now < state.cooldown_until:
            return None

        if len(state.prices) < self.long_ma_period + 1:
            return None

        prices = list(state.prices)
        rsi = self._compute_rsi(prices, self.rsi_period)
        if rsi is None:
            return None

        short_ma = sum(prices[-self.short_ma_period:]) / self.short_ma_period
        long_ma = sum(prices[-self.long_ma_period:]) / self.long_ma_period
        separation = abs(short_ma - long_ma) / max(long_ma, 0.0000001)

        signal_direction: SlideDirection | None = None
        reason = ""
        if rsi <= 30 and short_ma > long_ma:
            signal_direction = SlideDirection.BUY
            reason = f"RSI oversold ({rsi:.1f}) + uptrend"
        elif rsi >= 70 and short_ma < long_ma:
            signal_direction = SlideDirection.SELL
            reason = f"RSI overbought ({rsi:.1f}) + downtrend"

        if signal_direction is None:
            return None

        if self.settings.mode == BotMode.SLIDE and signal_direction != self.settings.slide_direction:
            return None

        confidence = self._confidence(rsi=rsi, separation=separation)
        signal = TradeSignal(
            pair=pair,
            direction=signal_direction,
            expiry=self.settings.time_period,
            confidence=confidence,
            timestamp=now,
            reason=reason,
        )

        lock_seconds = self._expiry_to_seconds(self.settings.time_period)
        state.active_trade_until = now + timedelta(seconds=lock_seconds)
        state.cooldown_until = now + timedelta(seconds=max(lock_seconds, 5))
        return signal

    @staticmethod
    def _compute_rsi(prices: list[float], period: int) -> float | None:
        if len(prices) < period + 1:
            return None

        deltas = [prices[i] - prices[i - 1] for i in range(len(prices) - period, len(prices))]
        gains = [delta for delta in deltas if delta > 0]
        losses = [-delta for delta in deltas if delta < 0]
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _confidence(rsi: float, separation: float) -> float:
        rsi_extreme = max(abs(rsi - 50) / 50, 0)
        trend_strength = min(separation * 100, 1.0)
        return round(min((0.6 * rsi_extreme) + (0.4 * trend_strength), 0.99), 2)

    @staticmethod
    def _expiry_to_seconds(expiry: str) -> int:
        mapping = {
            "S5": 5,
            "S10": 10,
            "S15": 15,
            "S30": 30,
            "M1": 60,
            "M2": 120,
            "M5": 300,
        }
        return mapping.get(expiry.upper(), 5)