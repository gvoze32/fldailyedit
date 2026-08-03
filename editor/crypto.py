"""
pesXdecrypter wrapper — handles decrypt/encrypt of the edit file via subprocess.

The edit file (edit00000000) is encrypted. pesXdecrypter splits it into blocks:
  encryption header, file header, thumbnail, description, data (data.dat), serial

We edit data.dat, then re-encrypt everything back.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class CryptoError(Exception):
    """Raised when decrypt/encrypt fails."""
    pass


def decrypt(edit_file_path: Path) -> Path:
    """
    Decrypt an edit file into a temporary directory.

    Args:
        edit_file_path: Path to the encrypted edit00000000 file.

    Returns:
        Path to the temp directory containing the decrypted blocks.
        The actual game data is in <temp_dir>/data.dat

    Raises:
        CryptoError: If decryption fails.
        FileNotFoundError: If the edit file or decrypter binary doesn't exist.
    """
    edit_file_path = Path(edit_file_path)
    if not edit_file_path.exists():
        raise FileNotFoundError(f"Edit file not found: {edit_file_path}")

    decrypter = _find_binary("decrypter")
    if not decrypter:
        raise FileNotFoundError(
            f"pesXdecrypter binary not found. "
            f"Looked in: {config.DECRYPTER_BIN}, and PATH. "
            f"Download from https://github.com/the4chancup/pesXdecrypter/releases "
            f"or compile from source."
        )

    # Create temp directory for decrypted output
    temp_dir = Path(tempfile.mkdtemp(prefix="fldailyedit_dec_"))

    try:
        logger.info(f"Decrypting {edit_file_path} → {temp_dir}")
        result = subprocess.run(
            [str(decrypter), str(edit_file_path), str(temp_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise CryptoError(
                f"Decryption failed (exit code {result.returncode}):\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        # Verify data.dat was created
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            # Some versions output with different names — check for any .dat file
            dat_files = list(temp_dir.glob("*.dat"))
            if not dat_files:
                raise CryptoError(
                    f"Decryption produced no .dat files in {temp_dir}. "
                    f"Contents: {list(temp_dir.iterdir())}"
                )
            # Use the largest .dat file (likely the data block)
            data_dat = max(dat_files, key=lambda f: f.stat().st_size)
            logger.info(f"data.dat not found, using largest .dat: {data_dat.name}")

        logger.info(f"Decrypted successfully. Data: {data_dat} ({data_dat.stat().st_size:,} bytes)")
        return temp_dir

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CryptoError("Decryption timed out after 60 seconds")
    except Exception:
        # Clean up temp dir on failure
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def encrypt(decrypted_dir: Path, output_path: Path) -> Path:
    """
    Re-encrypt decrypted blocks back into an edit file.

    Args:
        decrypted_dir: Directory containing the decrypted blocks (from decrypt()).
        output_path: Where to write the encrypted output file.

    Returns:
        Path to the new encrypted file.

    Raises:
        CryptoError: If encryption fails.
    """
    decrypted_dir = Path(decrypted_dir)
    output_path = Path(output_path)

    if not decrypted_dir.exists():
        raise FileNotFoundError(f"Decrypted directory not found: {decrypted_dir}")

    encrypter = _find_binary("encrypter")
    if not encrypter:
        raise FileNotFoundError(
            f"pesXencrypter binary not found. "
            f"Looked in: {config.ENCRYPTER_BIN}, and PATH."
        )

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(temp_fd)
    temp_output = Path(temp_name)
    verify_dir: Path | None = None

    try:
        logger.info(f"Encrypting {decrypted_dir} → {output_path}")
        result = subprocess.run(
            [str(encrypter), str(decrypted_dir), str(temp_output)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise CryptoError(
                f"Encryption failed (exit code {result.returncode}):\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        if not temp_output.exists() or temp_output.stat().st_size == 0:
            raise CryptoError(f"Encryption completed but output file was not created: {temp_output}")

        # Decrypt the candidate and compare every logical block before replacing
        # an existing output. This catches wrong-key, truncation, and packaging
        # failures that a zero exit code alone cannot detect.
        verify_dir = decrypt(temp_output)
        block_names = (
            "encryptHeader.dat",
            "header.dat",
            "description.dat",
            "logo.png",
            "data.dat",
            "version.txt",
        )
        for block_name in block_names:
            source_block = decrypted_dir / block_name
            verified_block = verify_dir / block_name
            if not source_block.exists() or not verified_block.exists():
                raise CryptoError(f"Round-trip verification is missing block: {block_name}")
            if source_block.read_bytes() != verified_block.read_bytes():
                raise CryptoError(f"Round-trip verification failed for block: {block_name}")

        os.replace(temp_output, output_path)

        logger.info(f"Encrypted successfully: {output_path} ({output_path.stat().st_size:,} bytes)")
        return output_path

    except subprocess.TimeoutExpired:
        raise CryptoError("Encryption timed out after 60 seconds")
    finally:
        if verify_dir is not None:
            cleanup_temp(verify_dir)
        if temp_output.exists():
            temp_output.unlink()


def cleanup_temp(temp_dir: Path):
    """Remove a temporary decryption directory."""
    if temp_dir and temp_dir.exists() and "fldailyedit_dec_" in str(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.debug(f"Cleaned up temp dir: {temp_dir}")


def _find_binary(name_prefix: str) -> Path | None:
    """
    Find the pesXdecrypter/encrypter binary.

    Search order:
    1. Configured path in config (DECRYPTER_BIN / ENCRYPTER_BIN)
    2. vendor/pesXdecrypter/ directory (with common name variants)
    3. System PATH
    """
    # 1. Config path
    configured = config.DECRYPTER_BIN if "decrypt" in name_prefix else config.ENCRYPTER_BIN
    if configured.exists():
        return configured

    # 2. Vendor directory — try common name variants
    vendor_dir = config.VENDOR_DIR / "pesXdecrypter"
    if vendor_dir.exists():
        candidates = [
            f"{name_prefix}21",
            f"{name_prefix}21.exe",
            f"{name_prefix}_21",
            f"{name_prefix}2021",
            f"pesX{name_prefix}",
            name_prefix,
        ]
        for candidate in candidates:
            path = vendor_dir / candidate
            if path.exists():
                return path

    # 3. System PATH
    which = shutil.which(f"{name_prefix}21") or shutil.which(name_prefix)
    if which:
        return Path(which)

    return None
