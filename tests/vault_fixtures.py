"""Helpers to build a synthetic *encrypted* Vaultwarden sync payload for tests.

These mirror how Vaultwarden returns data: cipher / collection fields are
EncString blobs (``2.<iv>|<ct>|<mac>``), the profile carries the master-key
protected user key, the user-key protected RSA private key, and the RSA
protected organization key.  Tests build a vault here, hand the mocked client
the master password + KDF params, and assert the client decrypts back to the
original plaintext — exercising the full unlock chain.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from robotsix_browser.bwcrypto import (
    KDF_PBKDF2,
    SymmetricKey,
    derive_master_key,
    stretch_master_key,
)

MASTER_PASSWORD = "correct horse battery staple"  # noqa: S105 - test fixture
EMAIL = "svc@example.com"
# Deliberately low so the PBKDF2 derivation stays fast in the test suite.
KDF_ITERATIONS = 5000


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def make_enc_string(key: SymmetricKey, plaintext: bytes) -> str:
    """Encrypt ``plaintext`` into a type-2 (AES-CBC + HMAC-SHA256) EncString."""
    iv = os.urandom(16)
    encryptor = Cipher(algorithms.AES(key.enc_key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()
    mac = hmac.new(key.mac_key, iv + ct, hashlib.sha256).digest()
    return f"2.{_b64(iv)}|{_b64(ct)}|{_b64(mac)}"


def make_rsa_enc_string(public_key: rsa.RSAPublicKey, plaintext: bytes) -> str:
    """Encrypt ``plaintext`` into a type-4 (RSA-2048 OAEP-SHA1) EncString."""
    ct = public_key.encrypt(
        plaintext,
        padding.OAEP(mgf=padding.MGF1(SHA1()), algorithm=SHA1(), label=None),
    )
    return f"4.{_b64(ct)}"


def prelogin_body() -> dict[str, Any]:
    """The ``/identity/accounts/prelogin`` response matching this fixture."""
    return {
        "kdf": KDF_PBKDF2,
        "kdfIterations": KDF_ITERATIONS,
        "kdfMemory": None,
        "kdfParallelism": None,
    }


def build_sync(
    *,
    org_id: str,
    collection_id: str,
    collection_name: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an encrypted ``GET /api/sync`` payload.

    ``entries`` is a list of ``{"id", "name", "username", "password"}`` dicts
    (optionally ``"collectionIds"``); each becomes an org-encrypted cipher.
    """
    user_key_raw = os.urandom(64)
    user_key = SymmetricKey.from_bytes(user_key_raw)
    master_key = derive_master_key(
        MASTER_PASSWORD, EMAIL, kdf=KDF_PBKDF2, iterations=KDF_ITERATIONS
    )
    protected_key = make_enc_string(stretch_master_key(master_key), user_key_raw)

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_der = rsa_key.private_bytes(
        Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
    )
    protected_private_key = make_enc_string(user_key, private_der)

    org_key_raw = os.urandom(64)
    org_key = SymmetricKey.from_bytes(org_key_raw)
    org_enc = make_rsa_enc_string(rsa_key.public_key(), org_key_raw)

    ciphers: list[dict[str, Any]] = []
    for entry in entries:
        ciphers.append(
            {
                "id": entry["id"],
                "organizationId": org_id,
                "collectionIds": entry.get("collectionIds", [collection_id]),
                "name": make_enc_string(org_key, entry["name"].encode("utf-8")),
                "login": {
                    "username": make_enc_string(
                        org_key, entry["username"].encode("utf-8")
                    ),
                    "password": make_enc_string(
                        org_key, entry["password"].encode("utf-8")
                    ),
                },
            }
        )

    return {
        "profile": {
            "key": protected_key,
            "privateKey": protected_private_key,
            "organizations": [{"id": org_id, "key": org_enc}],
        },
        "collections": [
            {
                "id": collection_id,
                "organizationId": org_id,
                "name": make_enc_string(org_key, collection_name.encode("utf-8")),
            }
        ],
        "ciphers": ciphers,
    }
