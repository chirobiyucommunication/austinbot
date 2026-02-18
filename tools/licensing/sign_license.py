from __future__ import annotations

import argparse
from pathlib import Path

from bot.licensing.issuer import issue_device_license


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign a device-bound bot license")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--customer", default="default-customer")
    parser.add_argument("--out", default="licenses/license.json")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / args.out
    license_path, expires_at = issue_device_license(
        project_root=project_root,
        device_id=args.device_id,
        customer=args.customer,
        days=args.days,
        out_path=output_path,
    )
    print(f"License written to: {license_path}")
    print(f"Expires at: {expires_at}")


if __name__ == "__main__":
    main()
