from __future__ import annotations

from bot.core.models import BotSettings, ExecutionMode
from bot.execution.adapters import BrokerAdapter, ManualConfirmAdapter, SimulatedAdapter
from bot.execution.pocket_option_selenium import PocketOptionSeleniumAdapter


def build_adapter(settings: BotSettings) -> BrokerAdapter:
    if settings.execution_mode == ExecutionMode.MANUAL:
        return ManualConfirmAdapter()
    if settings.execution_mode == ExecutionMode.SIMULATED:
        return SimulatedAdapter()
    return PocketOptionSeleniumAdapter(settings)
