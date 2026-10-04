from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from installer.update import verify_app_update_manifest_signature


SIGNING_KEY_ENVIRONMENT = "INSTALLER_SIGNING_KEY"


def sign_manifest(manifest_path: Path, output_path: Path, private_key_pem: str) -> None:
    """Write a detached base64 Ed25519 signature the installer will accept."""

    if not private_key_pem.strip():
        raise ValueError(f"{SIGNING_KEY_ENVIRONMENT} is not set")
    private_key = load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError(f"{SIGNING_KEY_ENVIRONMENT} must be an Ed25519 private key")
    payload = Path(manifest_path).read_bytes()
    signature = base64.b64encode(private_key.sign(payload)) + b"\n"
    # Fails when the key does not match the public key embedded in the app.
    verify_app_update_manifest_signature(payload, signature)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(signature)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sign the installer self-update manifest with the PEM key in "
            f"${SIGNING_KEY_ENVIRONMENT}"
        )
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    sign_manifest(
        arguments.manifest,
        arguments.output,
        os.environ.get(SIGNING_KEY_ENVIRONMENT, ""),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
