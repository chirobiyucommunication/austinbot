from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.licensing.device import get_device_fingerprint

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except Exception:  # pragma: no cover
    serialization = None
    Ed25519PublicKey = None


@dataclass(slots=True)
class LicenseValidationResult:
    valid: bool
    reason: str
    expires_at: str | None = None


class LicenseValidator:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.license_path = self.project_root / "licenses" / "license.json"
        self.public_key_path = self.project_root / "licenses" / "public_key.pem"
        self.client_lock_path = self.project_root / "data" / "client_id.lock"

    def current_device_id(self) -> str:
        return get_device_fingerprint()

    def validate(self) -> LicenseValidationResult:
        if serialization is None or Ed25519PublicKey is None:
            return LicenseValidationResult(False, "Missing dependency: install cryptography package")

        if not self.public_key_path.exists():
            return LicenseValidationResult(False, "Missing public key at licenses/public_key.pem")

        if not self.license_path.exists():
            return LicenseValidationResult(
                False,
                "Missing license file at licenses/license.json",
            )

        try:
            envelope = json.loads(self.license_path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            signature_b64 = envelope["signature"]
        except Exception as exc:
            return LicenseValidationResult(False, f"Invalid license file format: {exc}")

        payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        try:
            signature = base64.b64decode(signature_b64)
        except Exception as exc:
            return LicenseValidationResult(False, f"Invalid signature encoding: {exc}")

        try:
            public_key_pem = self.public_key_path.read_bytes()
            public_key = serialization.load_pem_public_key(public_key_pem)
            if not isinstance(public_key, Ed25519PublicKey):
                return LicenseValidationResult(False, "Public key is not Ed25519")
            public_key.verify(signature, payload_text.encode("utf-8"))
        except Exception as exc:
            return LicenseValidationResult(False, f"Signature verification failed: {exc}")

        licensed_device = str(payload.get("device_id", ""))
        if licensed_device != self.current_device_id():
            return LicenseValidationResult(False, "License is not valid for this device")

        expires_at_text = str(payload.get("expires_at", ""))
        try:
            expires_at = datetime.fromisoformat(expires_at_text.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                return LicenseValidationResult(False, "License has expired", expires_at=expires_at_text)
        except Exception as exc:
            return LicenseValidationResult(False, f"Invalid expires_at value: {exc}")

        product = str(payload.get("product", ""))
        if product != "pocket-option-bot":
            return LicenseValidationResult(False, "License product mismatch")

        client_id = str(payload.get("client_id", "")).strip()
        if client_id:
            try:
                self.client_lock_path.parent.mkdir(parents=True, exist_ok=True)
                if self.client_lock_path.exists():
                    locked = self.client_lock_path.read_text(encoding="utf-8").strip()
                    if locked and locked != client_id:
                        return LicenseValidationResult(False, "Device is locked to a different client ID")
                else:
                    self.client_lock_path.write_text(client_id, encoding="utf-8")
            except Exception as exc:
                return LicenseValidationResult(False, f"Client ID lock check failed: {exc}")

        return LicenseValidationResult(True, "License valid", expires_at=expires_at_text)
