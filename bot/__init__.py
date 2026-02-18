from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_repo_root = Path(__file__).resolve().parents[1]
_src_bot = _repo_root / "src" / "bot"
if _src_bot.exists():
    _src_bot_str = str(_src_bot)
    if _src_bot_str not in __path__:
        __path__.append(_src_bot_str)
