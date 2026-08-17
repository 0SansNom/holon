#!/usr/bin/env python3
"""Generate an RSA key pair for Holon JWT RS256.

Prints env-ready snippets (JSON kid→PEM with escaped newlines). Does not
write secrets to disk unless --out-dir is set.

Usage:
  python3 scripts/gen_jwt_rsa_keys.py
  python3 scripts/gen_jwt_rsa_keys.py --kid 2026-08 --out-dir ./jwt-keys
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kid", default="default", help="JWT kid (default: default)")
    parser.add_argument("--bits", type=int, default=2048, help="RSA modulus bits")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="If set, write private.pem / public.pem (mode 0600 / 0644)",
    )
    args = parser.parse_args()

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("cryptography is required: pip install cryptography", file=sys.stderr)
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=args.bits)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        priv_path = args.out_dir / "private.pem"
        pub_path = args.out_dir / "public.pem"
        priv_path.write_text(private_pem)
        priv_path.chmod(0o600)
        pub_path.write_text(public_pem)
        pub_path.chmod(0o644)
        print(f"wrote {priv_path} and {pub_path}", file=sys.stderr)

    private_map = {args.kid: private_pem}
    public_map = {args.kid: public_pem}
    print("# Paste into .env / K8s Secret (JSON; \\n escapes are OK):")
    print("HOLON_JWT_ALG=RS256")
    print(f"HOLON_JWT_ACTIVE_KID={args.kid}")
    print(f"HOLON_JWT_PRIVATE_KEYS={json.dumps(private_map)}")
    print(f"HOLON_JWT_PUBLIC_KEYS={json.dumps(public_map)}")
    print("# Production posture (optional):")
    print("HOLON_JWT_REQUIRE_ASYMMETRIC=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
