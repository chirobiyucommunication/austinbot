# Pocket Option Bot (Roadmap Scaffold)

This project implements a production-ready scaffold for your roadmap with **Model A workflow** (signal + user execution/result confirmation), focused on stability and sellable architecture.

## Implemented in this version

- Phase 1: Locked setting model, ranges, defaults, and validation.
- Phase 2: Desktop lifecycle (`Start`, `Pause/Resume`, `Stop`) and profile save/load.
- Phase 3: Session accounting truth layer (profit, counters, streaks, target enforcement).
- Phase 4: Martingale % risk engine with martingale limit and capital guardrail.
- Phase 5: Strategy engine v1 (RSI + trend filter), mode filter (Oscillate/Slide), anti-spam locks.
- Phase 6: OTC pair management (enabled pair list), per-pair expiry constraints, optional hour schedule filter.
- Execution plugin layer: manual confirm, simulated execution, and Pocket Option Selenium broker backend.
- Phase 9 (foundation): SQLite journaling for sessions and trades.

## Locked rule decisions

- Profit stop: session stops when `session_profit >= target_profit`.
- Martingale limit: session stops when limit is reached.
- Disable martingale: every next stake resets to `trade_amount`.
- Guardrail: if next stake exceeds remaining capital, session stops.

## Settings schema (phase 1 lock)

- `trade_capital`: float, default `100.0`, must be > 0
- `target_profit`: float, default `20.0`, must be > 0
- `trade_amount`: float, default `1.0`, > 0 and <= trade_capital
- `stack_method`: string, default `martingale`
- `time_period`: one of `S5,S10,S15,S30,M1,M2,M5`, default `S5`
- `martingale_percent`: float `0..500`, default `80.0`
- `martingale_limit`: int `0..20`, default `5`
- `disable_martingale`: bool, default `False`
- `mode`: `oscillate` or `slide`, default `oscillate`
- `slide_direction`: `buy` or `sell`, default `buy`
- `payout_rate`: float `(0,1]`, default `0.82`

## Run

```powershell
cd C:\Users\USER\source\repos\pocket-option-bot
pip install -r requirements.txt
$env:PYTHONPATH = "src"
python src/main.py
```

Or on Windows, double-click [run.bat](run.bat) for one-click launch.

## Install on Windows PC (build .exe)

Use PyInstaller to package the bot as a desktop executable:

```powershell
Set-Location C:\Users\USER\source\repos\pocket-option-bot
powershell -ExecutionPolicy Bypass -File tools\build\build_windows.ps1
```

Output executable:

- `dist\PocketOptionBot\PocketOptionBot.exe`

To install on another PC, copy the full `dist\PocketOptionBot` folder (not only the `.exe`) and run `PocketOptionBot.exe`.

## Build MSI installer (.msi)

Preferred for production deployment on Windows.

Prerequisites:

- .NET SDK installed (`dotnet` command available)
- Python 3.12 installed

Build MSI (this script builds `.exe`, harvests files, and creates MSI):

```powershell
Set-Location C:\Users\USER\source\repos\pocket-option-bot
powershell -ExecutionPolicy Bypass -File tools\build\build_msi.ps1 -Version 1.0.0
```

Output:

- `dist\PocketOptionBot-1.0.0.msi`

## Pocket Option auto-trade mode

- Set `Execution Mode` to `broker_plugin`.
- Keep `Broker Dry Run` enabled while validating selectors and flow.
- Disable `Broker Dry Run` only when ready for live Selenium clicks.
- Enable `Auto Open On Start` to launch the broker page automatically when session starts.
- Enable `Auto Execute Signals` to execute every generated signal directly without clicking `Execute Last Signal`.
- Login is manual in the opened browser session; no CAPTCHA/2FA bypass is implemented.
- Use `Open Broker Session` then `Check Selectors` in the app's `Broker Calibration` panel.
- If `amount_input`, `buy_button`, or `sell_button` fail health check, update selector fields and save.

If selectors mismatch your Pocket Option UI version, update `broker_selectors` in your profile JSON under `profiles/default.json`.

## Licensing (device-bound)

The bot now enforces a signed license check before `Start`.

1. Generate keys (admin side):

```powershell
$env:PYTHONPATH = "src"
python tools/licensing/generate_keys.py
```

2. Get device ID from app error/status (shown if license missing), or compute with:

```powershell
$env:PYTHONPATH = "src"
python -c "from bot.licensing.device import get_device_fingerprint; print(get_device_fingerprint())"
```

3. Sign a license for that device:

```powershell
$env:PYTHONPATH = "src"
python tools/licensing/sign_license.py --device-id "<DEVICE_ID>" --days 30 --customer "customer-1"
```

This creates `licenses/license.json`. The app validates:
- signature (Ed25519)
- device binding
- expiry (`expires_at`)
- product name match

## Telegram activation flow (separate payment bot + admin bot)

This project supports two separate Telegram bots:

- Payment bot (client-facing): request creation, payments, proof submission
- Admin bot (admin-facing): pending list, paid/activate/reject/deactivate actions

### 1) Install dependency

`python-telegram-bot` is included in `requirements.txt`.

```powershell
cd C:\Users\USER\source\repos\pocket-option-bot
pip install -r requirements.txt
```

### 2) Configure environment

Set values directly in `.env` (the bots auto-load this file at startup).

Set these variables in `.env` (or export in shell) before running the Telegram activation bot:

```powershell
$env:PYTHONPATH = "src"
$env:TELEGRAM_BOT_TOKEN = "<payment_bot_token>"
$env:TELEGRAM_ADMIN_BOT_TOKEN = "<admin_bot_token>"
$env:TELEGRAM_ADMIN_IDS = "123456789,987654321"
$env:TELEGRAM_PAYMENT_INSTRUCTIONS = "Send payment to UPI/Wallet XXXX and include your Request ID"
$env:LICENSE_DEFAULT_DAYS = "30"
$env:TELEGRAM_PAYMENT_PROVIDER_TOKEN = "<telegram_provider_token>"
$env:TELEGRAM_PAYMENT_CURRENCY = "USD"
$env:TELEGRAM_PAYMENT_PRICE_MINORS = "1000"
$env:TELEGRAM_AUTO_ACTIVATE_ON_PAYMENT = "true"
$env:BYBIT_UID = "<your_bybit_uid>"
$env:BYBIT_PAYMENT_NOTE = "Send screenshot with transfer details and Request ID"
```

Optional (used by app invalid-license popup):

```powershell
$env:TELEGRAM_ACTIVATION_BOT = "your_bot_username"
```

Optional (desktop push notifications):

```powershell
$env:APP_ANNOUNCEMENT_URL = "https://your-domain.com/app_announcements.json"
```

If `APP_ANNOUNCEMENT_URL` is not set, desktop app reads local `licenses/app_announcements.json`.

### 3) Run both bots

```powershell
$env:PYTHONPATH = "src"
python tools/licensing/telegram_activation_bot.py
python tools/licensing/telegram_admin_bot.py
```

### 3.1) Keep bots online 24/7 (even when your PC is off)

Run bots on an always-on Linux VPS with `systemd`:

```bash
cd /opt/pocket-option-bot
sudo bash tools/deploy/linux/setup_telegram_bots.sh /opt/pocket-option-bot ubuntu
```

Then edit server env file with real tokens:

```bash
sudo nano /etc/pocket-option-bot/bot.env
```

Useful service commands:

```bash
sudo systemctl status pocket-option-activation-bot --no-pager
sudo systemctl status pocket-option-admin-bot --no-pager
sudo journalctl -u pocket-option-activation-bot -f
sudo journalctl -u pocket-option-admin-bot -f
sudo systemctl restart pocket-option-activation-bot
sudo systemctl restart pocket-option-admin-bot
```

### 4) User flow

- User opens your Telegram bot and sends:
	- `/activate_request <DEVICE_ID>`
- Bot creates request ID and returns payment instructions.
- User taps `Pay` or sends `/pay <REQUEST_ID>`:
	- If `TELEGRAM_PAYMENT_PROVIDER_TOKEN` is set: bot sends Telegram invoice and auto-detects success.
	- If `TELEGRAM_PAYMENT_PROVIDER_TOKEN` is empty: bot switches to Bot payment mode and shows `TELEGRAM_PAYMENT_INSTRUCTIONS`.
- In Bot payment mode, user submits proof with:
	- `/submit_payment <REQUEST_ID>`
- Admin receives request notification.

Admin notifications are sent through the separate admin bot token.

Invoice mode: when payment is successful, bot auto-detects it and marks request as paid.
If `TELEGRAM_AUTO_ACTIVATE_ON_PAYMENT=true`, license is auto-generated and sent immediately.

Bot payment mode: admin reviews proof, then marks paid and activates from the admin bot.

If `TELEGRAM_PAYMENT_INSTRUCTIONS` is empty and `BYBIT_UID` is set, the bot automatically shows Bybit UID payment instructions.

### 5) Admin flow

- Run commands in admin bot chat:
	- `/pending` -> list requests
	- `/paid <request_id>` -> mark payment received
	- `/activate <request_id> [days]` -> generate + send `license.json`
	- `/reject <request_id> [reason]` -> reject request
	- `/deactivate <request_id> [reason]` -> mark request/license deactivated
	- `/broadcast <message>` -> push update message to all users (client bot) and publish app announcement payload

User saves received file to `licenses/license.json` in bot folder.

## Next implementation blocks

1. Add robust selector calibration wizard + UI health checks for broker plugin
2. Analytics dashboard + CSV export + installer/licensing phases
