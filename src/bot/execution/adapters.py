from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from abc import ABC, abstractmethod

from bot.core.models import SlideDirection, TradeOutcome, TradeSignal


@dataclass(slots=True)
class ExecutionResult:
    accepted: bool
    message: str
    outcome: TradeOutcome | None = None
    pair: str = ""
    direction: SlideDirection | None = None
    expiry: str = ""
    executed_at: datetime | None = None


class BrokerAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute_signal(self, signal: TradeSignal, stake: float) -> ExecutionResult:
        raise NotImplementedError


class ManualConfirmAdapter(BrokerAdapter):
    @property
    def name(self) -> str:
        return "manual"

    def execute_signal(self, signal: TradeSignal, stake: float) -> ExecutionResult:
        return ExecutionResult(
            accepted=True,
            message=(
                f"Manual mode: execute {signal.direction.value.upper()} on {signal.pair} "
                f"for {signal.expiry} with stake {stake}, then record Win/Loss."
            ),
            pair=signal.pair,
            direction=signal.direction,
            expiry=signal.expiry,
            executed_at=datetime.utcnow(),
        )


class SimulatedAdapter(BrokerAdapter):
    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "simulated"

    def execute_signal(self, signal: TradeSignal, stake: float) -> ExecutionResult:
        win_probability = max(min(signal.confidence, 0.95), 0.05)
        roll = self._rng.random()
        outcome = TradeOutcome.WIN if roll <= win_probability else TradeOutcome.LOSS
        return ExecutionResult(
            accepted=True,
            message=(
                f"Simulated trade on {signal.pair}: {signal.direction.value.upper()} {signal.expiry}, "
                f"stake={stake}, outcome={outcome.value.upper()}"
            ),
            outcome=outcome,
            pair=signal.pair,
            direction=signal.direction,
            expiry=signal.expiry,
            executed_at=datetime.utcnow(),
        )


class BrokerPluginAdapter(BrokerAdapter):
    @property
    def name(self) -> str:
        return "broker_plugin"

    def execute_signal(self, signal: TradeSignal, stake: float) -> ExecutionResult:
        return ExecutionResult(
            accepted=False,
            message="Broker plugin mode selected but no concrete broker adapter is configured yet.",
            pair=signal.pair,
            direction=signal.direction,
            expiry=signal.expiry,
            executed_at=datetime.utcnow(),
        )
