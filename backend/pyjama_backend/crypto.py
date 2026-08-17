"""Workspace encryption (Phase 2 — Epic J).

Key hierarchy (IMPLEMENTATION_PLAN §9.2), MVP shape:
  - Each workspace gets a random 256-bit Workspace Data Encryption Key (WDEK).
  - The WDEK is stored in the OS keystore (Keychain, or the 0600 fallback file),
    keyed by workspace id — never beside the encrypted data.
  - Partition files are encrypted with AES-256-GCM under the WDEK, a fresh random
    nonce per file. On-disk layout per partition: nonce(12) || ciphertext+tag.

Parquet is written to an in-memory Arrow buffer and only the ciphertext touches
disk, so no plaintext dataset file is ever created (§9.4). Wrapping the WDEK with
a separate app master key is a later refinement; storing the WDEK in the OS
secure store already keeps keys separate from data.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .keystore import KeyStore

NONCE_LEN = 12


def wdek_key_name(workspace_id: str) -> str:
    return f"wdek:{workspace_id}"


def new_wdek() -> bytes:
    """Random 256-bit workspace data encryption key."""
    return os.urandom(32)


def load_or_create_wdek(keystore: KeyStore, workspace_id: str) -> bytes:
    name = wdek_key_name(workspace_id)
    existing = keystore.get(name)
    if existing:
        return base64.b64decode(existing)
    wdek = new_wdek()
    keystore.set(name, base64.b64encode(wdek).decode("ascii"))
    return wdek


def get_wdek(keystore: KeyStore, workspace_id: str) -> bytes:
    existing = keystore.get(wdek_key_name(workspace_id))
    if not existing:
        raise KeyError(f"no WDEK for workspace {workspace_id}")
    return base64.b64decode(existing)


def encrypt(wdek: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(wdek).encrypt(nonce, plaintext, None)
    return nonce + ct


def decrypt(wdek: bytes, blob: bytes) -> bytes:
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    return AESGCM(wdek).decrypt(nonce, ct, None)
