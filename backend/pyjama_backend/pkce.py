"""PKCE (RFC 7636) for the OAuth U2M authorization-code flow."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from .logging_setup import Secret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def s256_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


@dataclass
class Pkce:
    verifier: Secret
    challenge: str
    state: str

    @classmethod
    def generate(cls) -> "Pkce":
        verifier = _b64url(secrets.token_bytes(32))  # 43-char, within spec
        return cls(
            verifier=Secret(verifier),
            challenge=s256_challenge(verifier),
            state=_b64url(secrets.token_bytes(16)),
        )
