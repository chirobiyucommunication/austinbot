from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from bot.licensing.issuer import issue_device_license


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUESTS_PATH = PROJECT_ROOT / "licenses" / "activation_requests.json"
CLIENT_REGISTRY_PATH = PROJECT_ROOT / "licenses" / "client_registry.json"

BTN_CREATE = "🆕 Create Request"
BTN_PAY = "💳 Pay"
BTN_MY = "📄 My Requests"
BTN_PROOF = "📤 Submit Payment Proof"
BTN_DEVICE = "🆔 Device ID"
BTN_HELP = "ℹ️ Help"

BTN_ADMIN_PENDING = "📋 Pending"
BTN_ADMIN_ACTIVATE = "✅ Activate"
BTN_ADMIN_REJECT = "⛔ Reject"


def _load_dotenv_file() -> None:
    dotenv_path = PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return

    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _admin_ids() -> set[int]:
    raw = _env("TELEGRAM_ADMIN_IDS")
    if not raw:
        return set()
    result: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if token:
            result.add(int(token))
    return result


def _payment_text() -> str:
    text = _env("TELEGRAM_PAYMENT_INSTRUCTIONS")
    if text:
        return text

    bybit_uid = _env("BYBIT_UID")
    if bybit_uid:
        note = _env("BYBIT_PAYMENT_NOTE", "Send screenshot with transfer details and request ID.")
        return (
            f"Send payment to Bybit UID: {bybit_uid}.\n"
            f"{note}"
        )

    return "Please send payment to admin and wait for approval."


def _license_days(default_days: int = 30) -> int:
    raw = _env("LICENSE_DEFAULT_DAYS", str(default_days))
    try:
        return max(1, int(raw))
    except Exception:
        return default_days


def _payment_provider_token() -> str:
    return _env("TELEGRAM_PAYMENT_PROVIDER_TOKEN")


def _payment_currency(default_currency: str = "USD") -> str:
    value = _env("TELEGRAM_PAYMENT_CURRENCY", default_currency).upper()
    return value or default_currency


def _payment_price_minors(default_value: int = 1000) -> int:
    raw = _env("TELEGRAM_PAYMENT_PRICE_MINORS", str(default_value))
    try:
        return max(1, int(raw))
    except Exception:
        return default_value


def _auto_activate_on_payment() -> bool:
    return _env("TELEGRAM_AUTO_ACTIVATE_ON_PAYMENT", "true").lower() in {"1", "true", "yes", "on"}


def _load_requests() -> dict:
    if not REQUESTS_PATH.exists():
        return {"requests": []}
    try:
        return json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"requests": []}


def _load_client_registry() -> dict:
    if not CLIENT_REGISTRY_PATH.exists():
        return {"clients": []}
    try:
        return json.loads(CLIENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"clients": []}


def _save_client_registry(data: dict) -> None:
    CLIENT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_REGISTRY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _save_requests(data: dict) -> None:
    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _new_request_id() -> str:
    return uuid4().hex[:10]


def _new_client_id() -> str:
    return f"CLI-{uuid4().hex[:10].upper()}"


def _is_draft_device_id(device_id: str) -> bool:
    return str(device_id or "").strip().upper().startswith("DRAFT-")


def _find_request(data: dict, request_id: str) -> dict | None:
    for item in data.get("requests", []):
        if item.get("request_id") == request_id:
            return item
    return None


def _latest_user_request(data: dict, user_id: int, statuses: set[str] | None = None) -> dict | None:
    rows = [r for r in data.get("requests", []) if int(r.get("telegram_user_id", 0)) == user_id]
    if statuses is not None:
        rows = [r for r in rows if str(r.get("status", "")) in statuses]
    if not rows:
        return None
    return rows[-1]


def _get_or_create_client_id(user_id: int, device_id: str) -> str:
    registry = _load_client_registry()
    rows = registry.setdefault("clients", [])

    for row in rows:
        if int(row.get("telegram_user_id", 0)) == int(user_id) and str(row.get("device_id", "")).strip() == device_id:
            existing = str(row.get("client_id", "")).strip()
            if existing:
                return existing

    client_id = _new_client_id()
    rows.append(
        {
            "client_id": client_id,
            "telegram_user_id": int(user_id),
            "device_id": device_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_client_registry(registry)
    return client_id


def _payment_payload(request_id: str, user_id: int) -> str:
    return f"license:{request_id}:{user_id}"


def _parse_payment_payload(payload: str) -> tuple[str, int] | None:
    parts = (payload or "").split(":")
    if len(parts) != 3 or parts[0] != "license":
        return None
    try:
        return parts[1], int(parts[2])
    except Exception:
        return None


def _find_user_request_for_payment(data: dict, user_id: int, request_id: str = "") -> dict | None:
    if request_id:
        row = _find_request(data, request_id)
        if row is None:
            return None
        if int(row.get("telegram_user_id", 0)) != int(user_id):
            return None
        return row

    user_rows = [
        r
        for r in data.get("requests", [])
        if int(r.get("telegram_user_id", 0)) == int(user_id) and r.get("status") in {"pending_payment", "payment_submitted", "paid"}
    ]
    if not user_rows:
        return None
    return user_rows[-1]


def _manual_payment_text(request_id: str) -> str:
    return (
        f"Bot payment mode for request {request_id}.\n"
        f"Instructions: {_payment_text()}\n\n"
        "After payment, submit proof with /submit_payment "
        f"{request_id}"
    )


def _is_admin(user_id: int) -> bool:
    admins = _admin_ids()
    return user_id in admins


def _admin_bot_token() -> str:
    token = _env("TELEGRAM_ADMIN_BOT_TOKEN")
    if token:
        return token
    return _env("TELEGRAM_BOT_TOKEN")


def _decode_start_device(payload: str) -> tuple[str | None, str | None]:
    if payload.startswith("actj_"):
        token = payload[5:].strip()
        if not token:
            return None, None
        padding = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode((token + padding).encode("utf-8")).decode("utf-8")
            data = json.loads(decoded)
            device_id = str(data.get("device_id", "")).strip() or None
            device_model = str(data.get("device_model", "")).strip() or None
            return device_id, device_model
        except Exception:
            return None, None

    if payload.startswith("actd_"):
        device_id = payload[5:].strip()
        return device_id or None, None

    if not payload.startswith("act_"):
        return None, None
    token = payload[4:].strip()
    if not token:
        return None, None
    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode((token + padding).encode("utf-8")).decode("utf-8")
        return decoded.strip() or None, None
    except Exception:
        return None, None


def _create_activation_request(
    user_id: int,
    username: str,
    device_id: str,
    source: str = "user",
    client_id: str | None = None,
    device_model: str | None = None,
) -> str:
    data = _load_requests()
    request_id = _new_request_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    resolved_client_id = client_id or _get_or_create_client_id(user_id=user_id, device_id=device_id)
    data.setdefault("requests", []).append(
        {
            "request_id": request_id,
            "telegram_user_id": user_id,
            "telegram_username": username,
            "device_id": device_id,
            "device_model": device_model or "",
            "client_id": resolved_client_id,
            "status": "pending_payment",
            "created_at": now_iso,
            "updated_at": now_iso,
            "paid_at": None,
            "activated_at": None,
            "expires_at": None,
            "admin_note": "",
            "source": source,
        }
    )
    _save_requests(data)
    return request_id


def _latest_known_user_device_id(user_id: int) -> str | None:
    data = _load_requests()
    rows = [r for r in data.get("requests", []) if int(r.get("telegram_user_id", 0)) == user_id]
    if not rows:
        registry = _load_client_registry()
        client_rows = [r for r in registry.get("clients", []) if int(r.get("telegram_user_id", 0)) == user_id]
        if not client_rows:
            return None
        for row in reversed(client_rows):
            device_id = str(row.get("device_id", "")).strip()
            if device_id and not _is_draft_device_id(device_id):
                return device_id
        return None
    for row in reversed(rows):
        device_id = str(row.get("device_id", "")).strip()
        if device_id and not _is_draft_device_id(device_id):
            return device_id
    return None


def _user_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CREATE)],
            [KeyboardButton(text=BTN_PAY), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_PROOF), KeyboardButton(text=BTN_DEVICE)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADMIN_PENDING)],
            [KeyboardButton(text=BTN_ADMIN_ACTIVATE), KeyboardButton(text=BTN_ADMIN_REJECT)],
            [KeyboardButton(text=BTN_CREATE), KeyboardButton(text=BTN_PAY)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_PROOF)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _admin_request_actions(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="Mark Paid", callback_data=f"admin:paid:{request_id}")],
            [InlineKeyboardButton(text="Activate", callback_data=f"admin:activate:{request_id}")],
            [InlineKeyboardButton(text="Reject", callback_data=f"admin:reject:{request_id}")],
        ]
    )


def _create_request_options_keyboard(has_linked_device: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_linked_device:
        rows.append([InlineKeyboardButton(text="Use Linked Device ID", callback_data="create:linked")])
    rows.append([InlineKeyboardButton(text="Enter New Device ID", callback_data="create:manual")])
    return InlineKeyboardMarkup(rows)


async def _notify_admins_new_request(context: ContextTypes.DEFAULT_TYPE, request_id: str, user_id: int, username: str, device_id: str, source: str = "user") -> None:
    admin_bot = Bot(token=_admin_bot_token())
    data = _load_requests()
    row = _find_request(data, request_id)
    client_id = str(row.get("client_id", "")).strip() if row else ""
    for admin_id in _admin_ids():
        try:
            await admin_bot.send_message(
                chat_id=admin_id,
                text=(
                    f"New activation request ({source})\n"
                    f"Request ID: {request_id}\n"
                    f"Client ID: {client_id or '-'}\n"
                    f"User: @{username or 'unknown'} ({user_id})\n"
                    f"Device: {device_id}"
                ),
                reply_markup=_admin_request_actions(request_id),
            )
        except Exception:
            pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    if context.args:
        device_id, device_model = _decode_start_device(context.args[0].strip())
        if device_id:
            request_id = _create_activation_request(
                user_id=user.id,
                username=user.username or "",
                device_id=device_id,
                source="desktop_redirect",
                device_model=device_model,
            )

            data = _load_requests()
            row = _find_request(data, request_id)
            client_id = str(row.get("client_id", "")).strip() if row else ""

            pay_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="Pay Now", callback_data=f"pay:{request_id}")]]
            )

            await update.message.reply_text(
                f"Activation request created from desktop app.\n"
                f"Request ID: {request_id}\n"
                f"Client ID: {client_id or '-'}\n"
                f"Device ID: {device_id}\n"
                f"Device Model: {device_model or '-'}\n\n"
                f"Payment instructions:\n{_payment_text()}\n\n"
                "Tap Pay Now to continue.",
                reply_markup=pay_keyboard,
            )

            await _notify_admins_new_request(
                context=context,
                request_id=request_id,
                user_id=user.id,
                username=user.username or "",
                device_id=device_id,
                source="desktop_redirect",
            )
            return

    await update.message.reply_text(
        "To activate your bot license:\n"
        "1) Send /activate_request <DEVICE_ID>\n"
        "2) Pay using /pay <REQUEST_ID>\n"
        "3) Wait for admin approval\n"
        "You can use /my_requests to check status.",
        reply_markup=_user_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_device_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    device_id = _latest_known_user_device_id(update.effective_user.id)
    if not device_id:
        await update.message.reply_text(
            "No linked real device ID found yet.\n"
            "Open desktop app and use Telegram redirect once, or run /activate_request <DEVICE_ID>."
        )
        return

    data = _load_requests()
    row = _latest_user_request(data, update.effective_user.id)
    client_id = ""
    device_model = ""
    if row is not None:
        client_id = str(row.get("client_id", "")).strip()
        device_model = str(row.get("device_model", "")).strip()

    await update.message.reply_text(
        f"Linked Device ID: {device_id}\n"
        f"Device Model: {device_model or '-'}\n"
        f"Client ID: {client_id or '-'}"
    )


async def cmd_activate_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    if not context.args:
        context.user_data["awaiting_device_id_for_request"] = True
        await update.message.reply_text("Send your device ID now (copied from desktop app).")
        return

    device_id = context.args[0].strip()
    if len(device_id) < 8:
        await update.message.reply_text("Invalid device_id")
        return
    if _is_draft_device_id(device_id):
        await update.message.reply_text(
            "Draft device IDs are not allowed. Open desktop app and use Telegram redirect to link real device ID."
        )
        return

    data = _load_requests()
    request_id = _new_request_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    data.setdefault("requests", []).append(
        {
            "request_id": request_id,
            "telegram_user_id": update.effective_user.id,
            "telegram_username": update.effective_user.username or "",
            "device_id": device_id,
            "status": "pending_payment",
            "created_at": now_iso,
            "updated_at": now_iso,
            "paid_at": None,
            "activated_at": None,
            "expires_at": None,
            "admin_note": "",
        }
    )
    _save_requests(data)

    resolved_client_id = _get_or_create_client_id(update.effective_user.id, device_id)
    row = _find_request(data, request_id)
    if row is not None:
        row["client_id"] = resolved_client_id
        _save_requests(data)

    pay_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="Pay Now", callback_data=f"pay:{request_id}")]]
    )

    await update.message.reply_text(
        f"Activation request created.\nRequest ID: {request_id}\n"
        f"Client ID: {resolved_client_id}\n"
        f"Device ID: {device_id}\n\n"
        f"Payment instructions:\n{_payment_text()}\n\n"
        f"Then send /pay {request_id} to pay by Telegram invoice.",
        reply_markup=pay_keyboard,
    )

    await _notify_admins_new_request(
        context=context,
        request_id=request_id,
        user_id=update.effective_user.id,
        username=update.effective_user.username or "",
        device_id=device_id,
        source="user",
    )


async def cmd_submit_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    request_id = ""
    if context.args:
        request_id = context.args[0].strip()

    data = _load_requests()
    if not request_id:
        row = _latest_user_request(
            data,
            user_id=update.effective_user.id,
            statuses={"pending_payment", "payment_submitted", "paid"},
        )
        if row is None:
            await update.message.reply_text("No request found. Create one first with /activate_request <DEVICE_ID>")
            return
        request_id = str(row.get("request_id", "")).strip()

    row = _find_request(data, request_id)
    if row is None or int(row.get("telegram_user_id", 0)) != update.effective_user.id:
        await update.message.reply_text("Request not found for your account.")
        return

    context.user_data["awaiting_payment_proof_request_id"] = request_id
    await update.message.reply_text(
        f"Send payment proof now for request {request_id}.\n"
        "You can send screenshot/photo/document with optional caption (tx hash, wallet, note)."
    )


async def on_payment_proof_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    request_id = str(context.user_data.get("awaiting_payment_proof_request_id", "")).strip()
    if not request_id:
        return

    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None or int(row.get("telegram_user_id", 0)) != update.effective_user.id:
        context.user_data.pop("awaiting_payment_proof_request_id", None)
        return

    proof_type = "text"
    proof_file_id = ""
    note = (update.message.caption or update.message.text or "").strip()

    if update.message.photo:
        proof_type = "photo"
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_type = "document"
        proof_file_id = update.message.document.file_id

    submitted_at = datetime.now(timezone.utc).isoformat()
    row["status"] = "payment_submitted"
    row["updated_at"] = submitted_at
    row["proof_submitted_at"] = submitted_at
    row["proof_type"] = proof_type
    row["proof_file_id"] = proof_file_id
    row["proof_note"] = note

    resolved_device_id = str(row.get("device_id", "")).strip()
    if not resolved_device_id:
        resolved_device_id = _latest_known_user_device_id(update.effective_user.id) or ""
        if resolved_device_id:
            row["device_id"] = resolved_device_id

    resolved_client_id = str(row.get("client_id", "")).strip()

    _save_requests(data)

    context.user_data.pop("awaiting_payment_proof_request_id", None)
    await update.message.reply_text(
        f"Payment proof submitted for {request_id}. Admin will verify and activate your license."
    )

    admin_text = (
        "Payment proof submitted\n"
        f"Request ID: {request_id}\n"
        f"User: @{update.effective_user.username or 'unknown'} ({update.effective_user.id})\n"
        f"Client ID: {resolved_client_id or '-'}\n"
        f"Device ID: {resolved_device_id or '-'}\n"
        f"Note: {note or '-'}"
    )

    admin_bot = Bot(token=_admin_bot_token())
    for admin_id in _admin_ids():
        try:
            if proof_type in {"photo", "document"} and proof_file_id:
                source_file = await context.bot.get_file(proof_file_id)
                content = await source_file.download_as_bytearray()

                if proof_type == "photo":
                    photo_buffer = BytesIO(content)
                    photo_buffer.name = f"proof_{request_id}.jpg"
                    await admin_bot.send_photo(
                        chat_id=admin_id,
                        photo=photo_buffer,
                        caption=admin_text,
                        reply_markup=_admin_request_actions(request_id),
                    )
                else:
                    filename = "payment_proof"
                    if update.message.document is not None and update.message.document.file_name:
                        filename = update.message.document.file_name
                    doc_buffer = BytesIO(content)
                    doc_buffer.name = filename
                    await admin_bot.send_document(
                        chat_id=admin_id,
                        document=doc_buffer,
                        caption=admin_text,
                        reply_markup=_admin_request_actions(request_id),
                    )
            else:
                await admin_bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=_admin_request_actions(request_id),
                )
        except Exception:
            try:
                await admin_bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=_admin_request_actions(request_id),
                )
            except Exception:
                pass


async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    provider_token = _payment_provider_token()
    request_id = ""
    if context.args:
        request_id = context.args[0].strip()

    data = _load_requests()

    row = _find_user_request_for_payment(data=data, user_id=update.effective_user.id, request_id=request_id)
    if row is None:
        await update.message.reply_text("No pending request found. Create one first with /activate_request <DEVICE_ID>")
        return

    request_id = str(row.get("request_id", "")).strip()

    if row.get("status") in {"activated", "rejected"}:
        await update.message.reply_text(f"Cannot pay request in status: {row.get('status')}")
        return

    if not provider_token:
        await update.message.reply_text(_manual_payment_text(request_id))
        return

    amount = _payment_price_minors()
    currency = _payment_currency()
    payload = _payment_payload(request_id, update.effective_user.id)

    await update.message.reply_invoice(
        title="Pocket Option Bot License",
        description=f"License activation for request {request_id}",
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=[LabeledPrice(label="License", amount=amount)],
        start_parameter=f"license-{request_id}",
    )


async def on_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    if not query.data.startswith("pay:"):
        return

    request_id = query.data.split(":", 1)[1].strip()
    if not request_id:
        await query.answer("Invalid request", show_alert=True)
        return

    data = _load_requests()
    row = _find_user_request_for_payment(data=data, user_id=update.effective_user.id, request_id=request_id)
    if row is None:
        await query.answer("Request not found", show_alert=True)
        return

    request_id = str(row.get("request_id", "")).strip()

    if row.get("status") in {"activated", "rejected"}:
        await query.answer(f"Request is {row.get('status')}", show_alert=True)
        return

    provider_token = _payment_provider_token()
    if not provider_token:
        await query.answer("Bot payment mode", show_alert=False)
        await context.bot.send_message(chat_id=update.effective_user.id, text=_manual_payment_text(request_id))
        return

    await query.answer()
    amount = _payment_price_minors()
    currency = _payment_currency()
    payload = _payment_payload(request_id, update.effective_user.id)

    if query.message is None:
        return

    await context.bot.send_invoice(
        chat_id=update.effective_user.id,
        title="Pocket Option Bot License",
        description=f"License activation for request {request_id}",
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=[LabeledPrice(label="License", amount=amount)],
        start_parameter=f"license-{request_id}",
    )


async def on_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    if query.data == "create:linked":
        device_id = _latest_known_user_device_id(update.effective_user.id)
        if not device_id:
            await query.answer("No linked device found", show_alert=True)
            return
        await query.answer()
        request_id = _create_activation_request(
            user_id=update.effective_user.id,
            username=update.effective_user.username or "",
            device_id=device_id,
            source="create_linked",
        )
        data = _load_requests()
        row = _find_request(data, request_id)
        client_id = str(row.get("client_id", "")).strip() if row else ""
        pay_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="Pay Now", callback_data=f"pay:{request_id}")]]
        )
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=(
                f"Activation request created.\n"
                f"Request ID: {request_id}\n"
                f"Client ID: {client_id or '-'}\n"
                f"Device ID: {device_id}"
            ),
            reply_markup=pay_keyboard,
        )
        await _notify_admins_new_request(
            context=context,
            request_id=request_id,
            user_id=update.effective_user.id,
            username=update.effective_user.username or "",
            device_id=device_id,
            source="create_linked",
        )
        return

    if query.data == "create:manual":
        context.user_data["awaiting_device_id_for_request"] = True
        await query.answer()
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Send your device ID now (copied from desktop app).",
        )
        return


async def on_admin_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "admin":
        return

    if not _is_admin(update.effective_user.id):
        await query.answer("Unauthorized", show_alert=True)
        return

    action = parts[1]
    request_id = parts[2]
    await query.answer()

    if action == "paid":
        data = _load_requests()
        row = _find_request(data, request_id)
        if row is None:
            await query.edit_message_reply_markup(reply_markup=None)
            return
        row["status"] = "paid"
        row["paid_at"] = datetime.now(timezone.utc).isoformat()
        row["updated_at"] = row["paid_at"]
        _save_requests(data)
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"Marked paid: {request_id}")
        return

    if action == "activate":
        ok, message = await _activate_and_send_license_by_request_id(request_id=request_id, context=context)
        await context.bot.send_message(chat_id=update.effective_user.id, text=message)
        return

    if action == "reject":
        data = _load_requests()
        row = _find_request(data, request_id)
        if row is None:
            return
        row["status"] = "rejected"
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        row["admin_note"] = "Rejected by admin (button)"
        _save_requests(data)
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"Rejected: {request_id}")
        try:
            await context.bot.send_message(
                chat_id=int(row.get("telegram_user_id")),
                text=f"Your activation request {request_id} was rejected.",
            )
        except Exception:
            pass


async def _activate_and_send_license_by_request_id(
    request_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    days: int | None = None,
) -> tuple[bool, str]:
    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        return False, "Request not found"

    if row.get("status") not in {"paid", "pending_payment"}:
        return False, f"Cannot activate from status: {row.get('status')}"

    effective_days = days if days is not None else _license_days()

    with TemporaryDirectory() as tmp_dir:
        out_path = Path(tmp_dir) / f"license_{request_id}.json"
        try:
            license_path, expires_at = issue_device_license(
                project_root=PROJECT_ROOT,
                device_id=str(row.get("device_id", "")),
                customer=str(row.get("telegram_user_id", "customer")),
                client_id=str(row.get("client_id", "")).strip() or None,
                days=effective_days,
                out_path=out_path,
            )
        except Exception as exc:
            return False, f"Activation failed: {exc}"

        row["status"] = "activated"
        row["activated_at"] = datetime.now(timezone.utc).isoformat()
        row["updated_at"] = row["activated_at"]
        row["expires_at"] = expires_at
        _save_requests(data)

        target_chat_id = int(row.get("telegram_user_id"))
        caption = (
            f"Your license is activated.\n"
            f"Request ID: {request_id}\n"
            f"Expires at: {expires_at}\n\n"
            "Save this as licenses/license.json in your bot folder."
        )

        try:
            with license_path.open("rb") as file_obj:
                await context.bot.send_document(chat_id=target_chat_id, document=file_obj, filename="license.json", caption=caption)
        except Exception as exc:
            return False, f"Activated but failed to send file: {exc}"

        for admin_id in _admin_ids():
            try:
                await context.bot.send_message(chat_id=admin_id, text=f"License activated and sent: {request_id}")
            except Exception:
                pass

        return True, f"Activated and sent license: {request_id}"


async def on_precheckout_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if query is None:
        return

    parsed = _parse_payment_payload(query.invoice_payload)
    if parsed is None:
        await query.answer(ok=False, error_message="Invalid payment payload")
        return

    request_id, user_id = parsed
    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None or int(row.get("telegram_user_id", 0)) != user_id:
        await query.answer(ok=False, error_message="Activation request not found")
        return

    await query.answer(ok=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.successful_payment is None or update.effective_user is None:
        return

    parsed = _parse_payment_payload(update.message.successful_payment.invoice_payload)
    if parsed is None:
        await update.message.reply_text("Payment received, but request mapping failed. Contact admin.")
        return

    request_id, payload_user_id = parsed
    if payload_user_id != update.effective_user.id:
        await update.message.reply_text("Payment user mismatch. Contact admin.")
        return

    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        await update.message.reply_text("Payment received, but request was not found. Contact admin.")
        return

    paid_at = datetime.now(timezone.utc).isoformat()
    row["status"] = "paid"
    row["paid_at"] = paid_at
    row["updated_at"] = paid_at
    _save_requests(data)

    await update.message.reply_text(f"Payment detected for request {request_id}.")

    admin_bot = Bot(token=_admin_bot_token())
    for admin_id in _admin_ids():
        try:
            await admin_bot.send_message(chat_id=admin_id, text=f"Payment auto-detected for request {request_id}")
        except Exception:
            pass

    if _auto_activate_on_payment():
        ok, message = await _activate_and_send_license_by_request_id(request_id, context=context)
        if ok:
            await update.message.reply_text("License has been activated and sent to you.")
        else:
            await update.message.reply_text(f"Payment received; activation pending admin action. {message}")


async def cmd_my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    data = _load_requests()
    rows = [r for r in data.get("requests", []) if r.get("telegram_user_id") == update.effective_user.id]
    if not rows:
        await update.message.reply_text("No requests found.")
        return

    lines = []
    for row in rows[-10:]:
        lines.append(
            f"{row.get('request_id')} | {row.get('status')} | device={row.get('device_id')} | exp={row.get('expires_at') or '-'}"
        )
    await update.message.reply_text("\n".join(lines))


async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    text = (update.message.text or "").strip()
    if text.startswith("/"):
        return

    if context.user_data.get("awaiting_device_id_for_request"):
        context.user_data.pop("awaiting_device_id_for_request", None)
        device_id = text
        if len(device_id) < 8:
            await update.message.reply_text("Invalid device ID. Try again with /activate_request <DEVICE_ID>.")
            return
        if _is_draft_device_id(device_id):
            await update.message.reply_text(
                "Draft device IDs are not allowed. Open desktop app and use Telegram redirect to link real device ID."
            )
            return

        request_id = _create_activation_request(
            user_id=update.effective_user.id,
            username=update.effective_user.username or "",
            device_id=device_id,
            source="manual_text",
        )
        data = _load_requests()
        row = _find_request(data, request_id)
        client_id = str(row.get("client_id", "")).strip() if row else ""
        pay_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="Pay Now", callback_data=f"pay:{request_id}")]]
        )
        await update.message.reply_text(
            f"Activation request created.\n"
            f"Request ID: {request_id}\n"
            f"Client ID: {client_id or '-'}\n"
            f"Device ID: {device_id}",
            reply_markup=pay_keyboard,
        )
        await _notify_admins_new_request(
            context=context,
            request_id=request_id,
            user_id=update.effective_user.id,
            username=update.effective_user.username or "",
            device_id=device_id,
            source="manual_text",
        )
        return

    if text == BTN_CREATE:
        user = update.effective_user
        if user is None:
            return
        has_linked_device = _latest_known_user_device_id(user.id) is not None
        await update.message.reply_text(
            "Create request options:\n"
            "- Use linked device ID\n"
            "- Enter new device ID",
            reply_markup=_create_request_options_keyboard(has_linked_device=has_linked_device),
        )
        return
    if text == BTN_PAY:
        await cmd_pay(update, context)
        return
    if text == BTN_MY:
        await cmd_my_requests(update, context)
        return
    if text == BTN_PROOF:
        await cmd_submit_payment(update, context)
        return
    if text == BTN_DEVICE:
        await cmd_device_id(update, context)
        return
    if text == BTN_HELP:
        await cmd_help(update, context)
        return


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return

    data = _load_requests()
    rows = [r for r in data.get("requests", []) if r.get("status") in {"pending_payment", "payment_submitted", "paid"}]
    if not rows:
        await update.message.reply_text("No pending requests.")
        return

    lines = []
    for row in rows[-20:]:
        lines.append(
            f"{row.get('request_id')} | {row.get('status')} | user={row.get('telegram_user_id')} | device={row.get('device_id')}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /paid <request_id>")
        return

    request_id = context.args[0].strip()
    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        await update.message.reply_text("Request not found")
        return

    row["status"] = "paid"
    row["paid_at"] = datetime.now(timezone.utc).isoformat()
    row["updated_at"] = row["paid_at"]
    _save_requests(data)

    await update.message.reply_text(f"Marked as paid: {request_id}")


async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /activate <request_id> [days]")
        return

    request_id = context.args[0].strip()
    days = _license_days()
    if len(context.args) > 1:
        try:
            days = max(1, int(context.args[1]))
        except Exception:
            pass

    ok, message = await _activate_and_send_license_by_request_id(request_id=request_id, context=context, days=days)
    await update.message.reply_text(message)


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject <request_id> [reason]")
        return

    request_id = context.args[0].strip()
    reason = " ".join(context.args[1:]).strip() or "Rejected by admin"

    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        await update.message.reply_text("Request not found")
        return

    row["status"] = "rejected"
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    row["admin_note"] = reason
    _save_requests(data)

    await update.message.reply_text(f"Rejected: {request_id}")

    try:
        await context.bot.send_message(chat_id=int(row.get("telegram_user_id")), text=f"Your activation request {request_id} was rejected.\nReason: {reason}")
    except Exception:
        pass


def main() -> None:
    _load_dotenv_file()

    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not _admin_ids():
        raise RuntimeError("Missing TELEGRAM_ADMIN_IDS (comma separated Telegram user IDs)")

    async def _post_init(application: Application) -> None:
        user_commands = [
            BotCommand("start", "Show menu"),
            BotCommand("help", "Show help"),
            BotCommand("device_id", "Show linked real device ID"),
            BotCommand("activate_request", "Create activation request"),
            BotCommand("my_requests", "My request status"),
            BotCommand("pay", "Pay for a request"),
            BotCommand("submit_payment", "Submit wallet/bybit payment proof"),
        ]
        await application.bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("device_id", cmd_device_id))
    app.add_handler(CommandHandler("activate_request", cmd_activate_request))
    app.add_handler(CommandHandler("pay", cmd_pay))
    app.add_handler(CommandHandler("submit_payment", cmd_submit_payment))
    app.add_handler(CommandHandler("my_requests", cmd_my_requests))
    app.add_handler(CallbackQueryHandler(on_pay_callback, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(on_create_callback, pattern=r"^create:"))
    app.add_handler(PreCheckoutQueryHandler(on_precheckout_query))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
            on_payment_proof_message,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))

    print("Telegram activation bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
