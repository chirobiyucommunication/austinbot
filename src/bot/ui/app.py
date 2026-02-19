from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
import tkinter as tk
import urllib.request
from tkinter import font, messagebox

from bot.core.controller import BotController
from bot.core.models import BotMode, BotSettings, ExecutionMode, SlideDirection


class BotApp:
    def __init__(self, root: tk.Tk, project_root: Path, controller: BotController | None = None) -> None:
        self.root = root
        self.controller = controller or BotController(project_root)
        self.root.title("Trading GUI")
        self.root.geometry("360x680")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.color_bg = "#081326"
        self.color_panel = "#0d1c35"
        self.color_border = "#2d6cff"
        self.color_text = "#ffffff"
        self.color_input = "#0f2442"
        self.color_start = "#1fa64b"
        self.color_put = "#cc3b3b"
        self.color_stop = "#e48a24"
        self.color_pause = "#4ca3ff"
        self.color_link = "#72b7ff"

        self.root.configure(bg=self.color_bg)

        self.trade_capital_var = tk.StringVar(value=str(self.controller.settings.trade_capital))
        self.target_profit_var = tk.StringVar(value=str(self.controller.settings.target_profit))
        self.trade_amount_var = tk.StringVar(value=str(self.controller.settings.trade_amount))
        self.stack_method_var = tk.StringVar(value="Mart")
        self.time_period_var = tk.StringVar(value=self.controller.settings.time_period)
        self.mart_limit_var = tk.StringVar(value=str(self.controller.settings.martingale_limit))
        self.disable_mart_var = tk.BooleanVar(value=self.controller.settings.disable_martingale)
        self.mode_var = tk.StringVar(value=self.controller.settings.mode.value)
        self.slide_direction_var = tk.StringVar(value=self.controller.settings.slide_direction.value)
        self.mart_action_var = tk.StringVar(value="STOP")

        self.status_var = tk.StringVar(value="Ready")
        self.pin_window_var = tk.BooleanVar(value=True)
        self._attempts_window: tk.Toplevel | None = None
        self._attempts_text: tk.Text | None = None
        self._attempts_refresh_job: str | None = None
        self._announcement_last_seen_id = self._load_last_seen_announcement_id()

        self._build_ui()
        self.mode_var.trace_add("write", lambda *_: self._render_primary_action_buttons())
        self._schedule_status_refresh()
        self._schedule_announcement_refresh()
        self.root.after(500, self._open_pocket_option_on_launch)

    def _announcement_state_path(self) -> Path:
        return self.controller.project_root / "data" / "announcement_seen.txt"

    def _load_last_seen_announcement_id(self) -> str:
        try:
            path = self._announcement_state_path()
            if not path.exists():
                return ""
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _save_last_seen_announcement_id(self, announcement_id: str) -> None:
        try:
            path = self._announcement_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(announcement_id.strip(), encoding="utf-8")
        except Exception:
            pass

    def _load_announcement(self) -> dict | None:
        url = os.getenv("APP_ANNOUNCEMENT_URL", "").strip()
        if url:
            try:
                with urllib.request.urlopen(url, timeout=4) as response:
                    payload = response.read().decode("utf-8")
                data = json.loads(payload)
                if isinstance(data, dict):
                    return data
            except Exception:
                return None

        local_path = self.controller.project_root / "licenses" / "app_announcements.json"
        if not local_path.exists():
            return None
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _schedule_announcement_refresh(self) -> None:
        try:
            announcement = self._load_announcement()
            if announcement:
                announcement_id = str(announcement.get("id", "")).strip()
                message = str(announcement.get("message", "")).strip()
                title = str(announcement.get("title", "Update")).strip() or "Update"
                if announcement_id and message and announcement_id != self._announcement_last_seen_id:
                    self._announcement_last_seen_id = announcement_id
                    self._save_last_seen_announcement_id(announcement_id)
                    messagebox.showinfo(f"{title}", message)
        except Exception:
            pass
        finally:
            self.root.after(15000, self._schedule_announcement_refresh)

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, bg=self.color_bg)
        container.pack(fill="both", expand=True, padx=14, pady=10)

        self._build_branding(container)
        self._build_mode_selector(container)
        self._build_main_settings_panel(container)
        self._build_martingale_panel(container)
        self._build_action_buttons(container)
        self._build_bottom_link(container)

    def _build_branding(self, parent: tk.Frame) -> None:
        skull_font = font.Font(family="Segoe UI Symbol", size=28, weight="bold")
        title_font = font.Font(family="Segoe UI", size=16, weight="bold")

        tk.Label(parent, text="☠", fg=self.color_text, bg=self.color_bg, font=skull_font).pack(pady=(2, 0))
        tk.Label(parent, text="Austin Maxi Bot", fg=self.color_text, bg=self.color_bg, font=title_font).pack(pady=(2, 4))

        divider = tk.Frame(parent, bg=self.color_border, height=2, width=86)
        divider.pack(pady=(0, 10))

    def _build_mode_selector(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=self.color_bg)
        row.pack(fill="x", pady=(0, 10))

        tk.Radiobutton(
            row,
            text="Oscillate",
            value="oscillate",
            variable=self.mode_var,
            fg=self.color_text,
            bg=self.color_bg,
            selectcolor=self.color_input,
            activebackground=self.color_bg,
            activeforeground=self.color_text,
            highlightthickness=0,
            bd=0,
        ).pack(side="left", expand=True)

        tk.Radiobutton(
            row,
            text="Slide",
            value="slide",
            variable=self.mode_var,
            fg=self.color_text,
            bg=self.color_bg,
            selectcolor=self.color_input,
            activebackground=self.color_bg,
            activeforeground=self.color_text,
            highlightthickness=0,
            bd=0,
        ).pack(side="left", expand=True)

    def _panel(self, parent: tk.Frame, pady: tuple[int, int] = (0, 10)) -> tk.Frame:
        panel = tk.Frame(parent, bg=self.color_panel, highlightbackground=self.color_border, highlightthickness=1, bd=0)
        panel.pack(fill="x", pady=pady)
        return panel

    def _styled_entry(self, parent: tk.Frame, variable: tk.StringVar, width: int = 10) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=self.color_input,
            fg=self.color_text,
            insertbackground=self.color_text,
            relief="solid",
            bd=1,
            highlightthickness=0,
            justify="center",
        )

    def _styled_menu(self, parent: tk.Frame, variable: tk.StringVar, values: list[str], width: int = 8) -> tk.OptionMenu:
        menu = tk.OptionMenu(parent, variable, *values)
        menu.config(
            width=width,
            bg=self.color_input,
            fg=self.color_text,
            activebackground=self.color_panel,
            activeforeground=self.color_text,
            highlightthickness=0,
            bd=1,
            relief="solid",
        )
        menu["menu"].config(bg=self.color_input, fg=self.color_text, activebackground=self.color_panel, activeforeground=self.color_text)
        return menu

    def _build_main_settings_panel(self, parent: tk.Frame) -> None:
        panel = self._panel(parent)
        rows = [
            ("Trade Capital", self.trade_capital_var, "entry"),
            ("Target Profit", self.target_profit_var, "entry"),
            ("Trade Amount", self.trade_amount_var, "entry"),
            ("Stack Method", self.stack_method_var, "menu"),
            ("Time Period", self.time_period_var, "menu_time"),
        ]

        for idx, (label_text, var, kind) in enumerate(rows):
            tk.Label(panel, text=label_text, fg=self.color_text, bg=self.color_panel, anchor="w", width=14).grid(
                row=idx, column=0, padx=(10, 0), pady=6, sticky="w"
            )
            tk.Label(panel, text=":", fg=self.color_text, bg=self.color_panel).grid(row=idx, column=1, pady=6, sticky="w")

            if kind == "entry":
                self._styled_entry(panel, var, width=10).grid(row=idx, column=2, padx=(8, 10), pady=6, sticky="e")
            elif kind == "menu":
                self._styled_menu(panel, var, ["Mart"], width=8).grid(row=idx, column=2, padx=(8, 10), pady=6, sticky="e")
            else:
                self._styled_menu(panel, var, ["S5", "S10", "S15", "S30", "M1", "M2", "M5"], width=8).grid(
                    row=idx, column=2, padx=(8, 10), pady=6, sticky="e"
                )

        panel.grid_columnconfigure(2, weight=1)

    def _build_martingale_panel(self, parent: tk.Frame) -> None:
        panel = self._panel(parent)

        tk.Label(panel, text="Martingale Limit", fg=self.color_text, bg=self.color_panel, anchor="w", width=14).grid(
            row=0, column=0, padx=(10, 0), pady=6, sticky="w"
        )
        tk.Label(panel, text=":", fg=self.color_text, bg=self.color_panel).grid(row=0, column=1, pady=6, sticky="w")

        spin = tk.Spinbox(
            panel,
            from_=0,
            to=20,
            textvariable=self.mart_limit_var,
            width=5,
            bg=self.color_input,
            fg=self.color_text,
            insertbackground=self.color_text,
            relief="solid",
            bd=1,
            highlightthickness=0,
        )
        spin.grid(row=0, column=2, padx=(8, 6), pady=6, sticky="w")

        self._styled_menu(panel, self.mart_action_var, ["STOP"], width=7).grid(row=0, column=3, padx=(0, 10), pady=6, sticky="w")

        tk.Checkbutton(
            panel,
            text="Disable Martingale",
            variable=self.disable_mart_var,
            fg=self.color_text,
            bg=self.color_panel,
            activebackground=self.color_panel,
            activeforeground=self.color_text,
            selectcolor=self.color_input,
            highlightthickness=0,
            bd=0,
        ).grid(row=1, column=2, columnspan=2, padx=(8, 10), pady=(4, 8), sticky="w")

    def _build_action_buttons(self, parent: tk.Frame) -> None:
        self.primary_btn_font = font.Font(family="Segoe UI", size=11, weight="bold")
        self.primary_action_frame = tk.Frame(parent, bg=self.color_bg)
        self.primary_action_frame.pack(fill="x", pady=(4, 8))
        self._render_primary_action_buttons()

        row = tk.Frame(parent, bg=self.color_bg)
        row.pack(fill="x")

        tk.Button(
            row,
            text="Stop",
            command=self._stop,
            bg=self.color_stop,
            fg=self.color_text,
            activebackground=self.color_stop,
            activeforeground=self.color_text,
            relief="flat",
            bd=0,
            height=2,
            font=self.primary_btn_font,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 5))

        tk.Button(
            row,
            text="Pause",
            command=self._pause,
            bg=self.color_pause,
            fg=self.color_text,
            activebackground=self.color_pause,
            activeforeground=self.color_text,
            relief="flat",
            bd=0,
            height=2,
            font=self.primary_btn_font,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        tk.Label(parent, textvariable=self.status_var, fg=self.color_text, bg=self.color_bg).pack(pady=(8, 0))

        tk.Checkbutton(
            parent,
            text="Pin window",
            variable=self.pin_window_var,
            command=self._toggle_topmost,
            fg=self.color_text,
            bg=self.color_bg,
            activebackground=self.color_bg,
            activeforeground=self.color_text,
            selectcolor=self.color_input,
            highlightthickness=0,
            bd=0,
        ).pack(anchor="w", pady=(6, 0))

    def _build_bottom_link(self, parent: tk.Frame) -> None:
        tools_row = tk.Frame(parent, bg=self.color_bg)
        tools_row.pack(side="bottom", pady=(8, 0))

        tk.Button(
            tools_row,
            text="Last 10 Attempts",
            command=self._show_last_attempts,
            bg=self.color_input,
            fg=self.color_text,
            activebackground=self.color_panel,
            activeforeground=self.color_text,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=8,
            pady=3,
        ).pack()

        tk.Button(
            tools_row,
            text="Copy Device ID",
            command=self._copy_device_id,
            bg=self.color_input,
            fg=self.color_text,
            activebackground=self.color_panel,
            activeforeground=self.color_text,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=8,
            pady=3,
        ).pack(pady=(6, 0))

        link_font = font.Font(family="Segoe UI", size=9, underline=True)
        link = tk.Label(
            parent,
            text="See Usage Tips",
            fg=self.color_link,
            bg=self.color_bg,
            cursor="hand2",
            font=link_font,
        )
        link.pack(side="bottom", pady=(12, 0))
        link.bind("<Button-1>", lambda _: self._show_usage_tips())

    def _render_primary_action_buttons(self) -> None:
        for widget in self.primary_action_frame.winfo_children():
            widget.destroy()

        if self.mode_var.get() == BotMode.SLIDE.value:
            tk.Button(
                self.primary_action_frame,
                text="Call",
                command=self._start_call,
                bg=self.color_start,
                fg=self.color_text,
                activebackground=self.color_start,
                activeforeground=self.color_text,
                relief="flat",
                bd=0,
                height=2,
                font=self.primary_btn_font,
                cursor="hand2",
            ).pack(side="left", fill="x", expand=True, padx=(0, 5))

            tk.Button(
                self.primary_action_frame,
                text="Put",
                command=self._start_put,
                bg=self.color_put,
                fg=self.color_text,
                activebackground=self.color_put,
                activeforeground=self.color_text,
                relief="flat",
                bd=0,
                height=2,
                font=self.primary_btn_font,
                cursor="hand2",
            ).pack(side="left", fill="x", expand=True, padx=(5, 0))
        else:
            tk.Button(
                self.primary_action_frame,
                text="Start",
                command=self._start,
                bg=self.color_start,
                fg=self.color_text,
                activebackground=self.color_start,
                activeforeground=self.color_text,
                relief="flat",
                bd=0,
                height=2,
                font=self.primary_btn_font,
                cursor="hand2",
            ).pack(fill="x")

    def _save_settings(self) -> None:
        try:
            settings = BotSettings(
                trade_capital=float(self.trade_capital_var.get()),
                target_profit=float(self.target_profit_var.get()),
                trade_amount=float(self.trade_amount_var.get()),
                stack_method=self.stack_method_var.get().lower(),
                time_period=self.time_period_var.get().upper(),
                martingale_percent=self.controller.settings.martingale_percent,
                martingale_limit=int(self.mart_limit_var.get()),
                disable_martingale=self.disable_mart_var.get(),
                mode=BotMode(self.mode_var.get()),
                slide_direction=SlideDirection(self.slide_direction_var.get()),
                payout_rate=self.controller.settings.payout_rate,
                enabled_pairs=self.controller.settings.enabled_pairs,
                pair_expiry_rules=self.controller.settings.pair_expiry_rules,
                schedule_enabled=self.controller.settings.schedule_enabled,
                schedule_start_hour=self.controller.settings.schedule_start_hour,
                schedule_end_hour=self.controller.settings.schedule_end_hour,
                execution_mode=self.controller.settings.execution_mode,
                broker_dry_run=self.controller.settings.broker_dry_run,
                auto_open_broker_on_start=self.controller.settings.auto_open_broker_on_start,
                auto_execute_signals=self.controller.settings.auto_execute_signals,
                pocket_option_url=self.controller.settings.pocket_option_url,
                broker_selectors=self.controller.settings.broker_selectors,
            )
            settings.validate()
            self.controller.update_settings(settings)
            self.status_var.set("Settings updated")
        except Exception as exc:
            self.status_var.set(f"Settings error: {exc}")

    def _start(self) -> None:
        self._save_settings()
        result = self.controller.start()
        self.status_var.set(result)

    def _open_pocket_option_on_launch(self) -> None:
        if self.controller.settings.execution_mode != ExecutionMode.BROKER_PLUGIN:
            return

        def _worker() -> None:
            last_message = ""
            for _ in range(3):
                try:
                    message = self.controller.open_broker_session()
                    last_message = message or ""
                    if message and not message.lower().startswith("failed") and "not installed" not in message.lower():
                        self.root.after(0, lambda msg=message: self.status_var.set(msg))
                        return
                except Exception as exc:
                    last_message = f"Failed to open Pocket Option: {exc}"
                time.sleep(1.0)

            if last_message:
                self.root.after(0, lambda msg=last_message: self.status_var.set(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _start_call(self) -> None:
        self.slide_direction_var.set(SlideDirection.BUY.value)
        self._start()

    def _start_put(self) -> None:
        self.slide_direction_var.set(SlideDirection.SELL.value)
        self._start()

    def _pause(self) -> None:
        self.status_var.set(self.controller.pause())

    def _stop(self) -> None:
        self.status_var.set(self.controller.stop())

    def _show_usage_tips(self) -> None:
        messagebox.showinfo(
            "Usage Tips",
            "1) Choose mode: Oscillate or Slide.\n"
            "2) Set capital, target, amount, and period.\n"
            "3) Press Start to begin session.\n"
            "4) Use Pause and Stop for control.",
        )

    def _show_last_attempts(self) -> None:
        if self._attempts_window is not None and self._attempts_window.winfo_exists():
            self._attempts_window.lift()
            self._attempts_window.focus_force()
            self._refresh_last_attempts_view()
            return

        window = tk.Toplevel(self.root)
        window.title("Last 10 Attempts")
        window.geometry("680x320")
        window.configure(bg=self.color_bg)

        text = tk.Text(
            window,
            wrap="word",
            bg=self.color_input,
            fg=self.color_text,
            insertbackground=self.color_text,
            relief="solid",
            bd=1,
            highlightthickness=0,
        )
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.configure(state="disabled")

        self._attempts_window = window
        self._attempts_text = text
        self._attempts_window.protocol("WM_DELETE_WINDOW", self._close_last_attempts_window)
        self._refresh_last_attempts_view()

    def _close_last_attempts_window(self) -> None:
        if self._attempts_refresh_job is not None:
            try:
                self.root.after_cancel(self._attempts_refresh_job)
            except Exception:
                pass
            self._attempts_refresh_job = None

        if self._attempts_window is not None and self._attempts_window.winfo_exists():
            self._attempts_window.destroy()

        self._attempts_window = None
        self._attempts_text = None

    def _refresh_last_attempts_view(self) -> None:
        if self._attempts_window is None or not self._attempts_window.winfo_exists() or self._attempts_text is None:
            self._attempts_refresh_job = None
            return

        content = "No execution attempts logged yet."
        try:
            attempts = self.controller.recent_execution_attempts(limit=10)
            if attempts:
                content = "\n".join(attempts)
        except Exception as exc:
            content = f"Failed to load attempts: {exc}"

        self._attempts_text.configure(state="normal")
        self._attempts_text.delete("1.0", tk.END)
        self._attempts_text.insert("1.0", content)
        self._attempts_text.configure(state="disabled")

        self._attempts_refresh_job = self.root.after(2000, self._refresh_last_attempts_view)

    def _copy_device_id(self) -> None:
        try:
            device_id = self.controller.device_id()
            self.root.clipboard_clear()
            self.root.clipboard_append(device_id)
            self.root.update_idletasks()
            self.status_var.set("Device ID copied")
        except Exception as exc:
            self.status_var.set(f"Copy failed: {exc}")

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.pin_window_var.get())

    def _schedule_status_refresh(self) -> None:
        try:
            state = self.controller.session.stats.state.value
            if state == "running":
                message = self.controller.last_execution_message or "Session running"
                self.status_var.set(message)
            elif state == "paused":
                self.status_var.set("Session paused")
            elif state == "stopped":
                if self.controller.session.stats.stop_reason is not None:
                    reason = self.controller.session.stats.stop_reason.value
                    if reason == "target_profit_reached":
                        self.status_var.set(
                            f"🎉 Congratulations! Take profit reached ({self.controller.session.stats.session_profit})."
                        )
                    else:
                        self.status_var.set(f"Session stopped: {reason}")
                elif self.status_var.get().strip() == "":
                    self.status_var.set("Ready")
        except Exception:
            pass
        finally:
            self.root.after(800, self._schedule_status_refresh)
