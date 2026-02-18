from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def issue_device_license(
    project_root: Path,
    device_id: str,
    customer: str,
    client_id: str | None = None,
    days: int = 30,
    out_path: Path | None = None,
) -> tuple[Path, str]:
    private_key_path = project_root / "licenses" / "private_key.pem"
    if not private_key_path.exists():
        raise FileNotFoundError("Missing licenses/private_key.pem. Run generate_keys.py first")

    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("Private key is not Ed25519")

    expires_at = datetime.now(timezone.utc) + timedelta(days=int(days))
    expires_at_iso = expires_at.isoformat()
    payload = {
        "product": "pocket-option-bot",
        "customer": customer,
        "device_id": device_id,
        "expires_at": expires_at_iso,
    }
    if client_id:
        payload["client_id"] = client_id
    payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = private_key.sign(payload_text.encode("utf-8"))

    envelope = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("utf-8"),
    }

    target = out_path or (project_root / "licenses" / "license.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return target, expires_at_iso
