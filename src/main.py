from pathlib import Path
import os
import shutil
import sys
import time
import tkinter as tk
from tkinter import ttk

from bot.core.controller import BotController
from bot.core.models import ExecutionMode
from bot.ui.app import BotApp


def _runtime_roots() -> tuple[Path, Path]:
    if getattr(sys, "frozen", False):
        install_root = Path(sys.executable).resolve().parent
        local_appdata = Path(os.getenv("LOCALAPPDATA", str(install_root))).resolve()
        workspace_root = local_appdata / "PocketOptionBot"
        workspace_root.mkdir(parents=True, exist_ok=True)
        return install_root, workspace_root

    project_root = Path(__file__).resolve().parent.parent
    return project_root, project_root


def _bootstrap_runtime_workspace(install_root: Path, workspace_root: Path) -> None:
    folders_to_seed = ["profiles", "licenses", "data"]
    for folder_name in folders_to_seed:
        src = install_root / folder_name
        dst = workspace_root / folder_name
        if dst.exists():
            continue
        if src.exists() and src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)

    src_env = install_root / ".env"
    dst_env = workspace_root / ".env"
    if not dst_env.exists() and src_env.exists():
        try:
            shutil.copy2(src_env, dst_env)
        except Exception:
            pass


def _set_loading(progress_var: tk.DoubleVar, status_var: tk.StringVar, value: float, text: str, splash: tk.Tk) -> None:
    progress_var.set(max(0.0, min(100.0, value)))
    status_var.set(f"{text} ({int(progress_var.get())}%)")
    splash.update_idletasks()


def _launch_with_splash(project_root: Path) -> BotController | None:
    splash = tk.Tk()
    splash.title("Launching")
    splash.geometry("360x150")
    splash.resizable(False, False)
    splash.configure(bg="#081326")
    splash.attributes("-topmost", True)

    progress_var = tk.DoubleVar(value=0.0)
    status_var = tk.StringVar(value="Starting... (0%)")

    ttk.Style(splash).theme_use("clam")
    ttk.Style(splash).configure("Launch.Horizontal.TProgressbar", troughcolor="#0d1c35", background="#2d6cff", bordercolor="#0d1c35")

    tk.Label(splash, text="Launching browser", fg="#ffffff", bg="#081326", font=("Segoe UI", 12, "bold")).pack(pady=(14, 6))
    ttk.Progressbar(splash, style="Launch.Horizontal.TProgressbar", length=300, mode="determinate", variable=progress_var).pack(pady=(2, 8))
    tk.Label(splash, textvariable=status_var, fg="#ffffff", bg="#081326", font=("Segoe UI", 10)).pack()

    _set_loading(progress_var, status_var, 10, "Initializing bot", splash)
    controller = BotController(project_root)

    if controller.settings.execution_mode != ExecutionMode.BROKER_PLUGIN:
        _set_loading(progress_var, status_var, 100, "Launch complete", splash)
        time.sleep(0.2)
        splash.destroy()
        return controller

    _set_loading(progress_var, status_var, 30, "Launching browser", splash)
    open_msg = controller.open_broker_session()
    if open_msg.lower().startswith("failed") or "not installed" in open_msg.lower():
        _set_loading(progress_var, status_var, 100, open_msg, splash)
        time.sleep(1.5)
        splash.destroy()
        return None

    _set_loading(progress_var, status_var, 45, "Opening Pocket Option site", splash)

    pulse = 45
    while True:
        try:
            splash.update()
        except tk.TclError:
            return None

        if controller.is_broker_logged_in():
            _set_loading(progress_var, status_var, 100, "Pocket Option logged in", splash)
            time.sleep(0.35)
            splash.destroy()
            return controller

        pulse = 46 + ((pulse - 45 + 2) % 49)
        _set_loading(progress_var, status_var, pulse, "Waiting for Pocket Option login", splash)
        time.sleep(0.5)


def main() -> None:
    install_root, project_root = _runtime_roots()
    _bootstrap_runtime_workspace(install_root=install_root, workspace_root=project_root)
    controller = _launch_with_splash(project_root)
    if controller is None:
        return

    root = tk.Tk()
    BotApp(root, project_root, controller=controller)
    root.mainloop()


if __name__ == "__main__":
    main()
