from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import BotMode, BotSettings, ExecutionMode, SlideDirection


class SettingsManager:
    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = profiles_dir
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.last_used_path = self.profiles_dir / "last_used.json"

    def save_profile(self, settings: BotSettings, profile_name: str = "default") -> Path:
        settings.validate()
        profile_path = self.profiles_dir / f"{profile_name}.json"
        data = asdict(settings)
        data["mode"] = settings.mode.value
        data["slide_direction"] = settings.slide_direction.value
        data["execution_mode"] = settings.execution_mode.value
        profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.last_used_path.write_text(json.dumps({"profile": profile_name}), encoding="utf-8")
        return profile_path

    def load_profile(self, profile_name: str = "default") -> BotSettings:
        profile_path = self.profiles_dir / f"{profile_name}.json"
        if not profile_path.exists():
            settings = BotSettings()
            self.save_profile(settings, profile_name=profile_name)
            return settings

        data = json.loads(profile_path.read_text(encoding="utf-8"))
        settings = BotSettings(
            trade_capital=float(data.get("trade_capital", 100.0)),
            target_profit=float(data.get("target_profit", 20.0)),
            trade_amount=float(data.get("trade_amount", 1.0)),
            stack_method=str(data.get("stack_method", "martingale")),
            time_period=str(data.get("time_period", "S5")),
            martingale_percent=float(data.get("martingale_percent", 80.0)),
            martingale_limit=int(data.get("martingale_limit", 5)),
            disable_martingale=bool(data.get("disable_martingale", False)),
            mode=BotMode(data.get("mode", "oscillate")),
            slide_direction=SlideDirection(data.get("slide_direction", "buy")),
            payout_rate=float(data.get("payout_rate", 0.82)),
            enabled_pairs=[str(item) for item in data.get("enabled_pairs", ["EURUSD_otc", "GBPUSD_otc"])],
            pair_expiry_rules={
                str(pair): [str(expiry).upper() for expiry in expiries]
                for pair, expiries in data.get(
                    "pair_expiry_rules",
                    {
                        "EURUSD_otc": ["S5", "S10", "S15", "S30", "M1", "M2", "M5"],
                        "GBPUSD_otc": ["S5", "S10", "S15", "S30", "M1", "M2"],
                        "USDJPY_otc": ["S5", "S10", "S15", "S30", "M1"],
                        "AUDUSD_otc": ["S5", "S10", "S15", "M1"],
                    },
                ).items()
            },
            schedule_enabled=bool(data.get("schedule_enabled", False)),
            schedule_start_hour=int(data.get("schedule_start_hour", 0)),
            schedule_end_hour=int(data.get("schedule_end_hour", 23)),
            execution_mode=ExecutionMode(data.get("execution_mode", "manual")),
            broker_dry_run=bool(data.get("broker_dry_run", True)),
            auto_open_broker_on_start=bool(data.get("auto_open_broker_on_start", True)),
            auto_execute_signals=bool(data.get("auto_execute_signals", True)),
            pocket_option_url=str(
                data.get("pocket_option_url", "https://pocketoption.com/en/cabinet/demo-quick-high-low/")
            ),
            broker_selectors={
                str(k): str(v)
                for k, v in data.get(
                    "broker_selectors",
                    {
                        "amount_input": "input[type='text'][inputmode='decimal']",
                        "buy_button": "button[data-test='button-buy'], .btn-call",
                        "sell_button": "button[data-test='button-sell'], .btn-put",
                        "pair_dropdown": ".current-symbol",
                        "pair_search": "input[type='search']",
                        "pair_item": "[data-symbol='{pair}']",
                        "expiry_dropdown": ".expiration-select",
                        "expiry_item": "[data-expiration='{expiry}']",
                    },
                ).items()
            },
        )
        settings.validate()
        return settings

    def load_last_used(self) -> BotSettings:
        if not self.last_used_path.exists():
            return self.load_profile("default")

        payload = json.loads(self.last_used_path.read_text(encoding="utf-8"))
        profile = str(payload.get("profile", "default"))
        return self.load_profile(profile)
