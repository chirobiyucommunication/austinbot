from __future__ import annotations

import base64
import json
from pathlib import Path
from datetime import datetime
import os
import threading
import time

from bot.core.models import BotMode, BotSettings, ExecutionMode, SlideDirection, StopReason, TradeOutcome, TradeSignal
from bot.core.otc_pair_manager import OTCPairManager
from bot.core.session_engine import SessionEngine
from bot.core.settings_manager import SettingsManager
from bot.core.strategy_engine import StrategyEngine
from bot.execution.factory import build_adapter
from bot.execution.pocket_option_selenium import PocketOptionSeleniumAdapter
from bot.licensing.device import get_device_model
from bot.licensing.validator import LicenseValidationResult, LicenseValidator
from bot.storage.journal import Journal


class BotController:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.settings_manager = SettingsManager(self.project_root / "profiles")
        self.settings = self.settings_manager.load_last_used()
        self.session = SessionEngine(self.settings)
        self.pair_manager = OTCPairManager(self.settings)
        self.strategy = StrategyEngine(self.settings, self.pair_manager)
        self.execution_adapter = build_adapter(self.settings)
        self.license_validator = LicenseValidator(self.project_root)
        self.journal = Journal(self.project_root / "data" / "journal.db")
        self.last_signal: TradeSignal | None = None
        self.last_execution_message = "none"
        self.last_license_validation: LicenseValidationResult | None = None
        self._auto_trade_thread: threading.Thread | None = None
        self._auto_trade_running = False
        self._oscillate_next_direction = SlideDirection.BUY
        self._next_trade_at = 0.0
        self._trade_cooldown_seconds = 65.0
        self._broker_start_balance: float | None = None
        self._broker_last_balance: float | None = None

    def update_settings(self, settings: BotSettings, profile: str = "default") -> None:
        settings.validate()
        previous_adapter = self.execution_adapter
        self.settings = settings
        self.settings_manager.save_profile(settings, profile_name=profile)
        self.session = SessionEngine(self.settings)
        self.pair_manager = OTCPairManager(self.settings)
        self.strategy = StrategyEngine(self.settings, self.pair_manager)

        if (
            settings.execution_mode == ExecutionMode.BROKER_PLUGIN
            and isinstance(previous_adapter, PocketOptionSeleniumAdapter)
        ):
            previous_adapter.settings = settings
            self.execution_adapter = previous_adapter
        else:
            self.execution_adapter = build_adapter(self.settings)

    def start(self) -> str:
        license_check = self.license_validator.validate()
        self.last_license_validation = license_check
        if not license_check.valid:
            return self.license_activation_message(license_check.reason)

        self.session.start()
        self._next_trade_at = 0.0
        self._broker_start_balance = None
        self._broker_last_balance = None

        if isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
            try:
                bal = self.execution_adapter.get_account_balance()
                if bal is not None:
                    self._broker_start_balance = bal
                    self._broker_last_balance = bal
            except Exception:
                pass

        self._start_auto_trade_loop()
        if self.settings.execution_mode == ExecutionMode.BROKER_PLUGIN and self.settings.auto_open_broker_on_start:
            broker_msg = self.open_broker_session()
            if broker_msg.lower().startswith("failed"):
                return f"Session started | {broker_msg}"
        return "Session started"

    def pause(self) -> str:
        if self.session.stats.state.value == "paused":
            self.session.resume()
            self._start_auto_trade_loop()
            return "Session resumed"
        self._stop_auto_trade_loop()
        self.session.pause()
        return "Session paused"

    def stop(self) -> str:
        self._stop_auto_trade_loop()
        self.session.stop()
        self.journal.log_session(self.session.stats)
        return "Session stopped"

    def record_win(self, pair: str = "OTC") -> str:
        trade = self.session.apply_trade_outcome(TradeOutcome.WIN, pair=pair)
        self.journal.log_trade(trade)
        if self.session.stats.state.value == "stopped":
            self.journal.log_session(self.session.stats)
            return f"WIN logged (+{trade.pnl}). Session stopped: {self.session.stats.stop_reason.value}"
        return f"WIN logged (+{trade.pnl})"

    def record_loss(self, pair: str = "OTC") -> str:
        trade = self.session.apply_trade_outcome(TradeOutcome.LOSS, pair=pair)
        self.journal.log_trade(trade)
        if self.session.stats.state.value == "stopped":
            self.journal.log_session(self.session.stats)
            return f"LOSS logged ({trade.pnl}). Session stopped: {self.session.stats.stop_reason.value}"
        return f"LOSS logged ({trade.pnl})"

    def set_mode(self, mode: BotMode, direction: SlideDirection | None = None) -> None:
        self.settings.mode = mode
        if direction is not None:
            self.settings.slide_direction = direction
        self.settings_manager.save_profile(self.settings)
        self.pair_manager = OTCPairManager(self.settings)
        self.strategy = StrategyEngine(self.settings, self.pair_manager)

    def set_execution_mode(self, mode_value: str) -> None:
        self.settings.execution_mode = ExecutionMode(mode_value)
        self.settings_manager.save_profile(self.settings)
        self.execution_adapter = build_adapter(self.settings)

    def feed_price(self, pair: str, price: float, timestamp: datetime | None = None) -> TradeSignal | None:
        signal = self.strategy.on_price(pair=pair, price=price, timestamp=timestamp)
        if signal is not None:
            self.last_signal = signal
            self.journal.log_signal(signal)
            if self.settings.auto_execute_signals:
                self.last_execution_message = self.execute_last_signal()
        return signal

    def execute_last_signal(self) -> str:
        if self.session.stats.state.value != "running":
            return "Cannot execute signal: session is not running"
        if self.last_signal is None:
            return "No signal to execute"

        apply_message = (
            f"Applying stake={self.session.stats.current_stake} expiry={self.last_signal.expiry} "
            f"direction={self.last_signal.direction.value.upper()}"
        )
        self.last_execution_message = apply_message

        self.journal.log_signal(
            TradeSignal(
                pair=self.last_signal.pair,
                direction=self.last_signal.direction,
                expiry=self.last_signal.expiry,
                confidence=self.last_signal.confidence,
                timestamp=datetime.utcnow(),
                reason=f"execution-attempt | {apply_message}",
            )
        )
        result = self.execution_adapter.execute_signal(self.last_signal, self.session.stats.current_stake)
        self.last_execution_message = result.message
        if not result.accepted:
            return result.message

        if result.outcome is None:
            return result.message

        if result.outcome == TradeOutcome.WIN:
            return f"{result.message} | {self.record_win(pair=result.pair)}"
        return f"{result.message} | {self.record_loss(pair=result.pair)}"

    def open_broker_session(self) -> str:
        if not isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
            return "Broker session open is available only in broker_plugin mode"
        return self.execution_adapter.open_session()

    def is_broker_logged_in(self) -> bool:
        if not isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
            return True
        return self.execution_adapter.is_logged_in()

    def run_selector_health_check(self) -> str:
        if not isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
            return "Selector check is available only in broker_plugin mode"
        checks = self.execution_adapter.selector_health_check()
        ok = [name for name, passed in checks.items() if passed]
        bad = [name for name, passed in checks.items() if not passed]
        return (
            f"Selector health: ok={len(ok)} fail={len(bad)} | "
            f"OK: {', '.join(ok) if ok else 'none'} | FAIL: {', '.join(bad) if bad else 'none'}"
        )

    def recent_execution_attempts(self, limit: int = 10) -> list[str]:
        rows = self.journal.recent_execution_attempts(limit=limit)
        if not rows:
            return []

        lines: list[str] = []
        for row in rows:
            detail = row["reason"].replace("execution-attempt | ", "").strip()
            lines.append(
                f"{row['timestamp']} | {row['pair']} {row['direction'].upper()} {row['expiry']} | {detail}"
            )
        return lines

    def device_id(self) -> str:
        return self.license_validator.current_device_id()

    def activation_bot_username(self) -> str:
        return os.getenv("TELEGRAM_ACTIVATION_BOT", "austinpaymentbot").strip().lstrip("@")

    def activation_bot_url(self) -> str | None:
        username = self.activation_bot_username()
        if not username:
            return None
        payload = self._activation_start_payload()
        return f"https://t.me/{username}?start={payload}"

    def _activation_start_payload(self) -> str:
        payload = {
            "device_id": self.device_id(),
            "device_model": self.device_model(),
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
        return f"actj_{encoded.rstrip('=')}"

    def device_model(self) -> str:
        return get_device_model()

    def license_activation_message(self, reason: str) -> str:
        parts = [
            f"License invalid: {reason}",
            f"Device ID: {self.device_id()}",
            f"License path: {self.license_validator.license_path}",
            f"Public key path: {self.license_validator.public_key_path}",
        ]

        activation_url = self.activation_bot_url()
        if activation_url:
            parts.append(f"Activation bot: {activation_url}")
            parts.append(f"Telegram command: /activate_request {self.device_id()}")
            parts.append("After request creation, pay with: /pay <REQUEST_ID>")

        return " | ".join(parts)

    def recheck_license(self) -> str:
        self.last_license_validation = self.license_validator.validate()
        if self.last_license_validation.valid:
            return f"License valid (expires: {self.last_license_validation.expires_at})"
        return self.license_activation_message(self.last_license_validation.reason)

    def status_text(self) -> str:
        stats = self.session.stats
        direction = stats.current_direction.value if stats.current_mode == BotMode.SLIDE else "both"
        target_remaining = round(self.settings.target_profit - stats.session_profit, 2)
        last_signal_text = (
            f"{self.last_signal.pair} {self.last_signal.direction.value.upper()} {self.last_signal.expiry} @ {self.last_signal.confidence}"
            if self.last_signal
            else "none"
        )
        schedule_text = (
            f"{self.settings.schedule_start_hour:02d}:00-{self.settings.schedule_end_hour:02d}:00"
            if self.settings.schedule_enabled
            else "disabled"
        )
        if self.last_license_validation is None:
            license_text = "not-checked"
        elif self.last_license_validation.valid:
            license_text = f"valid (exp: {self.last_license_validation.expires_at})"
        else:
            license_text = f"invalid ({self.last_license_validation.reason})"
        return (
            f"State: {stats.state.value}\\n"
            f"License: {license_text}\\n"
            f"Profit: {stats.session_profit}\\n"
            f"Target remaining: {target_remaining}\\n"
            f"Trades: {stats.trades_taken} | Wins: {stats.wins} | Losses: {stats.losses}\\n"
            f"Current stake: {stats.current_stake}\\n"
            f"Execution mode: {self.settings.execution_mode.value}\\n"
            f"Broker dry run: {self.settings.broker_dry_run} ({self.execution_adapter.name})\\n"
            f"Auto open on start: {self.settings.auto_open_broker_on_start}\\n"
            f"Auto execute signals: {self.settings.auto_execute_signals}\\n"
            f"Mode: {stats.current_mode.value} ({direction})\\n"
            f"Enabled pairs: {', '.join(self.settings.enabled_pairs)}\\n"
            f"Schedule: {schedule_text}\\n"
            f"Last signal: {last_signal_text}\\n"
            f"Last execution: {self.last_execution_message}"
        )

    def _start_auto_trade_loop(self) -> None:
        if self._auto_trade_running:
            return
        self._auto_trade_running = True
        self._auto_trade_thread = threading.Thread(target=self._auto_trade_worker, daemon=True)
        self._auto_trade_thread.start()

    def _stop_auto_trade_loop(self) -> None:
        self._auto_trade_running = False
        self._next_trade_at = 0.0

    def _stop_for_target_profit(self, profit: float) -> None:
        self._stop_auto_trade_loop()
        self.session.stats.session_profit = profit
        self.session.stop(reason=StopReason.TARGET_PROFIT_REACHED)
        self.last_execution_message = (
            f"🎉 Congratulations! Take profit reached: {profit} >= {self.settings.target_profit}."
        )
        self.journal.log_session(self.session.stats)

    def _check_broker_take_profit(self) -> bool:
        if not isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
            return False

        balance = self.execution_adapter.get_account_balance()
        if balance is None:
            return False

        self._broker_last_balance = balance
        if self._broker_start_balance is None:
            self._broker_start_balance = balance
            return False

        broker_profit = round(balance - self._broker_start_balance, 2)
        self.session.stats.session_profit = broker_profit

        if broker_profit >= self.settings.target_profit:
            self._stop_for_target_profit(broker_profit)
            return True

        return False

    def _auto_trade_worker(self) -> None:
        while self._auto_trade_running:
            try:
                if self.session.stats.state.value != "running":
                    time.sleep(0.35)
                    continue

                if self.settings.execution_mode != ExecutionMode.BROKER_PLUGIN:
                    time.sleep(0.5)
                    continue

                if not isinstance(self.execution_adapter, PocketOptionSeleniumAdapter):
                    time.sleep(0.5)
                    continue

                if self._check_broker_take_profit():
                    continue

                now = time.monotonic()
                if now < self._next_trade_at:
                    time.sleep(0.25)
                    continue

                pair = self.settings.enabled_pairs[0] if self.settings.enabled_pairs else "OTC"
                direction = self._next_click_direction()
                signal = TradeSignal(
                    pair=pair,
                    direction=direction,
                    expiry=self.settings.time_period,
                    confidence=1.0,
                    timestamp=datetime.utcnow(),
                    reason="direct-click-loop",
                )
                self.last_signal = signal
                self.journal.log_signal(signal)
                self.last_execution_message = self.execute_last_signal()
                self._next_trade_at = time.monotonic() + self._trade_cooldown_seconds
            except Exception as exc:
                self.last_execution_message = f"Auto loop error: {exc}"
            finally:
                time.sleep(0.2)

    def _next_click_direction(self) -> SlideDirection:
        if self.settings.mode == BotMode.SLIDE:
            return self.settings.slide_direction

        direction = self._oscillate_next_direction
        self._oscillate_next_direction = (
            SlideDirection.SELL if direction == SlideDirection.BUY else SlideDirection.BUY
        )
        return direction
