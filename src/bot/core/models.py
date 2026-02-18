from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class BotMode(str, Enum):
    OSCILLATE = "oscillate"
    SLIDE = "slide"


class SlideDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"


class LifecycleState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class StopReason(str, Enum):
    TARGET_PROFIT_REACHED = "target_profit_reached"
    MARTINGALE_LIMIT_REACHED = "martingale_limit_reached"
    CAPITAL_GUARDRAIL = "capital_guardrail"
    USER_STOP = "user_stop"


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"


class ExecutionMode(str, Enum):
    MANUAL = "manual"
    SIMULATED = "simulated"
    BROKER_PLUGIN = "broker_plugin"


@dataclass(slots=True)
class BotSettings:
    trade_capital: float = 100.0
    target_profit: float = 20.0
    trade_amount: float = 1.0
    stack_method: str = "martingale"
    time_period: str = "S5"
    martingale_percent: float = 80.0
    martingale_limit: int = 5
    disable_martingale: bool = False
    mode: BotMode = BotMode.OSCILLATE
    slide_direction: SlideDirection = SlideDirection.BUY
    payout_rate: float = 0.82
    enabled_pairs: list[str] = field(default_factory=lambda: ["EURUSD_otc", "GBPUSD_otc"])
    pair_expiry_rules: dict[str, list[str]] = field(
        default_factory=lambda: {
            "EURUSD_otc": ["S5", "S10", "S15", "S30", "M1", "M2", "M5"],
            "GBPUSD_otc": ["S5", "S10", "S15", "S30", "M1", "M2"],
            "USDJPY_otc": ["S5", "S10", "S15", "S30", "M1"],
            "AUDUSD_otc": ["S5", "S10", "S15", "M1"],
        }
    )
    schedule_enabled: bool = False
    schedule_start_hour: int = 0
    schedule_end_hour: int = 23
    execution_mode: ExecutionMode = ExecutionMode.BROKER_PLUGIN
    broker_dry_run: bool = False
    auto_open_broker_on_start: bool = True
    auto_execute_signals: bool = True
    pocket_option_url: str = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
    broker_selectors: dict[str, str] = field(
        default_factory=lambda: {
            "amount_input": "input[type='text'][autocomplete='off']",
            "buy_button": "xpath=//span[contains(@class,'switch-state-block__item')][.//span[contains(@class,'payout__text') and normalize-space()='Buy']]",
            "sell_button": "xpath=//span[contains(@class,'switch-state-block__item')][.//span[contains(@class,'payout__text') and normalize-space()='Sell']]",
            "pair_dropdown": ".current-symbol",
            "pair_search": "input[type='search']",
            "pair_item": "[data-symbol='{pair}']",
            "expiry_dropdown": ".expiration-select",
            "expiry_item": "[data-expiration='{expiry}']",
            "expiry_value": "div.value__val",
            "expiry_minute_input": "xpath=(//input[@type='text' and @autocomplete='off'])[last()-1]",
            "expiry_second_input": "xpath=(//input[@type='text' and @autocomplete='off'])[last()]",
        }
    )

    def validate(self) -> None:
        if self.trade_capital <= 0:
            raise ValueError("Trade Capital must be > 0")
        if self.target_profit <= 0:
            raise ValueError("Target Profit must be > 0")
        if self.trade_amount <= 0:
            raise ValueError("Trade Amount must be > 0")
        if self.trade_amount > self.trade_capital:
            raise ValueError("Trade Amount cannot exceed Trade Capital")
        if self.martingale_percent < 0 or self.martingale_percent > 500:
            raise ValueError("Martingale % must be between 0 and 500")
        if self.martingale_limit < 0 or self.martingale_limit > 20:
            raise ValueError("Martingale Limit must be between 0 and 20")
        if self.time_period not in {"S5", "S10", "S15", "S30", "M1", "M2", "M5"}:
            raise ValueError("Time Period must be one of S5,S10,S15,S30,M1,M2,M5")
        if self.payout_rate <= 0 or self.payout_rate > 1.0:
            raise ValueError("Payout rate must be between 0 and 1")
        if not self.enabled_pairs:
            raise ValueError("At least one OTC pair must be enabled")
        valid_expiries = {"S5", "S10", "S15", "S30", "M1", "M2", "M5"}
        for pair in self.enabled_pairs:
            if pair not in self.pair_expiry_rules:
                raise ValueError(f"Enabled pair '{pair}' is not in pair expiry rules")
        for pair, expiry_list in self.pair_expiry_rules.items():
            if not expiry_list:
                raise ValueError(f"Pair '{pair}' must define at least one allowed expiry")
            for expiry in expiry_list:
                if expiry not in valid_expiries:
                    raise ValueError(f"Pair '{pair}' has invalid expiry '{expiry}'")
        if self.schedule_start_hour < 0 or self.schedule_start_hour > 23:
            raise ValueError("Schedule start hour must be between 0 and 23")
        if self.schedule_end_hour < 0 or self.schedule_end_hour > 23:
            raise ValueError("Schedule end hour must be between 0 and 23")
        if self.execution_mode not in {ExecutionMode.MANUAL, ExecutionMode.SIMULATED, ExecutionMode.BROKER_PLUGIN}:
            raise ValueError("Execution mode must be manual, simulated, or broker_plugin")
        if not self.pocket_option_url.startswith("http"):
            raise ValueError("Pocket Option URL must start with http/https")
        required_selectors = {"amount_input", "buy_button", "sell_button"}
        if not required_selectors.issubset(set(self.broker_selectors.keys())):
            raise ValueError("Broker selectors must include amount_input, buy_button, sell_button")


@dataclass(slots=True)
class SessionStats:
    state: LifecycleState = LifecycleState.STOPPED
    start_balance: float = 0.0
    session_profit: float = 0.0
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    current_stake: float = 0.0
    current_mode: BotMode = BotMode.OSCILLATE
    current_direction: SlideDirection = SlideDirection.BUY
    loss_streak: int = 0
    martingale_step: int = 0
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    stop_reason: StopReason | None = None


@dataclass(slots=True)
class TradeSignal:
    pair: str
    direction: SlideDirection
    expiry: str
    confidence: float
    timestamp: datetime
    reason: str


@dataclass(slots=True)
class TradeRecord:
    pair: str
    direction: SlideDirection
    stake: float
    expiry: str
    outcome: TradeOutcome
    pnl: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
