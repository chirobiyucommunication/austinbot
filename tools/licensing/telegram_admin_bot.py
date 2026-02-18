from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from bot.licensing.issuer import issue_device_license


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUESTS_PATH = PROJECT_ROOT / "licenses" / "activation_requests.json"
CLIENT_REGISTRY_PATH = PROJECT_ROOT / "licenses" / "client_registry.json"
ANNOUNCEMENTS_PATH = PROJECT_ROOT / "licenses" / "app_announcements.json"


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


def _license_days(default_days: int = 30) -> int:
    raw = _env("LICENSE_DEFAULT_DAYS", str(default_days))
    try:
        return max(1, int(raw))
    except Exception:
        return default_days


def _load_requests() -> dict:
    if not REQUESTS_PATH.exists():
        return {"requests": []}
    try:
        return json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"requests": []}


def _save_requests(data: dict) -> None:
    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_client_registry() -> dict:
    if not CLIENT_REGISTRY_PATH.exists():
        return {"clients": []}
    try:
        return json.loads(CLIENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"clients": []}


def _save_announcement(data: dict) -> None:
    ANNOUNCEMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANNOUNCEMENTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _client_bot_token() -> str:
    return _env("TELEGRAM_BOT_TOKEN")


def _find_request(data: dict, request_id: str) -> dict | None:
    for item in data.get("requests", []):
        if item.get("request_id") == request_id:
            return item
    return None


def _is_draft_device_id(device_id: str) -> bool:
    return str(device_id or "").strip().upper().startswith("DRAFT-")


def _resolve_real_device_id_for_user(data: dict, user_id: int) -> str | None:
    for row in reversed(data.get("requests", [])):
        if int(row.get("telegram_user_id", 0)) != int(user_id):
            continue
        device_id = str(row.get("device_id", "")).strip()
        if device_id and not _is_draft_device_id(device_id):
            return device_id

    registry = _load_client_registry()
    for row in reversed(registry.get("clients", [])):
        if int(row.get("telegram_user_id", 0)) != int(user_id):
            continue
        device_id = str(row.get("device_id", "")).strip()
        if device_id and not _is_draft_device_id(device_id):
            return device_id

    return None


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


def _admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/pending")],
            [KeyboardButton(text="/activate <request_id>"), KeyboardButton(text="/reject <request_id> <reason>")],
            [KeyboardButton(text="/paid <request_id>"), KeyboardButton(text="/deactivate <request_id> <reason>")],
            [KeyboardButton(text="/help")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _actions(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="Mark Paid", callback_data=f"admin:paid:{request_id}")],
            [InlineKeyboardButton(text="Activate", callback_data=f"admin:activate:{request_id}")],
            [InlineKeyboardButton(text="Reject", callback_data=f"admin:reject:{request_id}")],
            [InlineKeyboardButton(text="Deactivate", callback_data=f"admin:deactivate:{request_id}")],
        ]
    )


async def _activate_and_send(context: ContextTypes.DEFAULT_TYPE, request_id: str, days: int | None = None) -> tuple[bool, str]:
    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        return False, "Request not found"

    device_id = str(row.get("device_id", "")).strip()
    if _is_draft_device_id(device_id):
        replacement = _resolve_real_device_id_for_user(data=data, user_id=int(row.get("telegram_user_id", 0)))
        if replacement:
            row["device_id"] = replacement
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            row["admin_note"] = "Auto-replaced legacy draft device ID with real linked device ID"
            _save_requests(data)
            device_id = replacement
        else:
            return False, "Cannot activate draft device ID. Ask user to link real desktop device ID via app redirect."

    if row.get("status") not in {"paid", "pending_payment", "payment_submitted"}:
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

        caption = (
            f"Your license is activated.\n"
            f"Request ID: {request_id}\n"
            f"Expires at: {expires_at}\n\n"
            "Save this as licenses/license.json in your bot folder."
        )

        try:
            with license_path.open("rb") as file_obj:
                await context.bot.send_document(
                    chat_id=int(row.get("telegram_user_id")),
                    document=file_obj,
                    filename="license.json",
                    caption=caption,
                )
        except Exception as exc:
            return False, f"Activated but failed to send file: {exc}"

    return True, f"Activated and sent license: {request_id}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    await update.message.reply_text(
        "Admin bot ready.\n"
        "Use /pending to review requests and action buttons.",
        reply_markup=_admin_menu(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return

    data = _load_requests()
    rows = [r for r in data.get("requests", []) if r.get("status") in {"pending_payment", "payment_submitted", "paid", "activated"}]
    if not rows:
        await update.message.reply_text("No requests found.")
        return

    for row in rows[-20:]:
        request_id = str(row.get("request_id", ""))
        text = (
            f"{request_id} | {row.get('status')}\n"
            f"user={row.get('telegram_user_id')}\n"
            f"client_id={row.get('client_id') or '-'}\n"
            f"device={row.get('device_id')}\n"
            f"exp={row.get('expires_at') or '-'}"
        )
        await update.message.reply_text(text, reply_markup=_actions(request_id))


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
    await update.message.reply_text(f"Marked paid: {request_id}")


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

    ok, message = await _activate_and_send(context=context, request_id=request_id, days=days)
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


async def cmd_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /deactivate <request_id> [reason]")
        return

    request_id = context.args[0].strip()
    reason = " ".join(context.args[1:]).strip() or "Deactivated by admin"

    data = _load_requests()
    row = _find_request(data, request_id)
    if row is None:
        await update.message.reply_text("Request not found")
        return

    row["status"] = "deactivated"
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    row["admin_note"] = reason
    _save_requests(data)

    await update.message.reply_text(f"Deactivated: {request_id}")
    try:
        await context.bot.send_message(chat_id=int(row.get("telegram_user_id")), text=f"Your activation request {request_id} was deactivated.\nReason: {reason}")
    except Exception:
        pass


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    message = " ".join(context.args).strip()
    if not message:
        await update.message.reply_text("Message cannot be empty.")
        return

    data = _load_requests()
    user_ids: set[int] = set()
    for row in data.get("requests", []):
        try:
            user_ids.add(int(row.get("telegram_user_id", 0)))
        except Exception:
            continue
    user_ids.discard(0)

    announcement = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": "Update",
        "message": message,
        "source": "telegram_admin_broadcast",
    }
    _save_announcement(announcement)

    token = _client_bot_token()
    if not token:
        await update.message.reply_text(
            f"Announcement saved for app notifications ({ANNOUNCEMENTS_PATH.name}), but TELEGRAM_BOT_TOKEN is missing for user broadcast."
        )
        return

    client_bot = context.bot if token == _env("TELEGRAM_ADMIN_BOT_TOKEN") else None
    if client_bot is None:
        from telegram import Bot

        client_bot = Bot(token=token)

    sent = 0
    failed = 0
    outbound_text = f"📢 Update\n\n{message}"
    for user_id in user_ids:
        try:
            await client_bot.send_message(chat_id=user_id, text=outbound_text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"Broadcast complete. sent={sent} failed={failed} users={len(user_ids)}"
    )


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    if not _is_admin(update.effective_user.id):
        await query.answer("Unauthorized", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "admin":
        return

    action = parts[1]
    request_id = parts[2]
    await query.answer()

    if action == "paid":
        data = _load_requests()
        row = _find_request(data, request_id)
        if row is None:
            return
        row["status"] = "paid"
        row["paid_at"] = datetime.now(timezone.utc).isoformat()
        row["updated_at"] = row["paid_at"]
        _save_requests(data)
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"Marked paid: {request_id}")
        return

    if action == "activate":
        _, message = await _activate_and_send(context=context, request_id=request_id)
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
            await context.bot.send_message(chat_id=int(row.get("telegram_user_id")), text=f"Your activation request {request_id} was rejected.")
        except Exception:
            pass
        return

    if action == "deactivate":
        data = _load_requests()
        row = _find_request(data, request_id)
        if row is None:
            return
        row["status"] = "deactivated"
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        row["admin_note"] = "Deactivated by admin (button)"
        _save_requests(data)
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"Deactivated: {request_id}")
        try:
            await context.bot.send_message(chat_id=int(row.get("telegram_user_id")), text=f"Your activation request {request_id} was deactivated.")
        except Exception:
            pass


def main() -> None:
    _load_dotenv_file()

    token = _env("TELEGRAM_ADMIN_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_ADMIN_BOT_TOKEN")
    if not _admin_ids():
        raise RuntimeError("Missing TELEGRAM_ADMIN_IDS")

    async def _post_init(application: Application) -> None:
        commands = [
            BotCommand("start", "Show admin menu"),
            BotCommand("help", "Show help"),
            BotCommand("pending", "List requests"),
            BotCommand("paid", "Mark as paid"),
            BotCommand("activate", "Activate request"),
            BotCommand("reject", "Reject request"),
            BotCommand("deactivate", "Deactivate request"),
            BotCommand("broadcast", "Broadcast update to users"),
        ]
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())

    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("paid", cmd_paid))
    app.add_handler(CommandHandler("activate", cmd_activate))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("deactivate", cmd_deactivate))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^admin:"))

    print("Telegram admin bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
