from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid


def _run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=4, check=False)
        if result.returncode != 0:
            return ""
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _windows_machine_guid() -> str:
    if platform.system().lower() != "windows":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value).strip()
    except Exception:
        return ""


def _first_non_header_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in {"model", "name", "version"}:
            continue
        return cleaned
    return ""


def _windows_device_model() -> str:
    if platform.system().lower() != "windows":
        return ""

    model_raw = _run_command(["wmic", "computersystem", "get", "model"])
    model = _first_non_header_line(model_raw)
    if model:
        return model

    ps_model = _run_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_ComputerSystem).Model",
        ]
    )
    return _first_non_header_line(ps_model)


def get_device_model() -> str:
    windows_model = _windows_device_model()
    if windows_model:
        return windows_model

    system = platform.system() or "UnknownOS"
    machine = platform.machine() or "UnknownArch"
    node = platform.node() or "UnknownHost"
    return f"{system} | {machine} | {node}"


def get_device_fingerprint() -> str:
    machine_guid = ""
    if os.getenv("DEVICE_ID_USE_MACHINE_GUID", "false").strip().lower() in {"1", "true", "yes", "on"}:
        machine_guid = _windows_machine_guid()
    raw = "|".join(
        [
            platform.system(),
            platform.release(),
            platform.machine(),
            platform.node(),
            str(uuid.getnode()),
            machine_guid,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
