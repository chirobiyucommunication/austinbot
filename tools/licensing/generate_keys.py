from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    licenses_dir = project_root / "licenses"
    licenses_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    (licenses_dir / "private_key.pem").write_bytes(private_bytes)
    (licenses_dir / "public_key.pem").write_bytes(public_bytes)
    print("Generated keys in licenses/: private_key.pem, public_key.pem")


if __name__ == "__main__":
    main()
