"""Unit tests for the Bitwarden vault-unlock / decrypt primitives."""

from __future__ import annotations

import os

import pytest

import vault_fixtures as vf
from robotsix_browser import bwcrypto
from robotsix_browser.bwcrypto import (
    KDF_PBKDF2,
    SymmetricKey,
    VaultCryptoError,
    decrypt_sym,
    stretch_master_key,
    unlock,
)


def _random_key() -> SymmetricKey:
    return SymmetricKey.from_bytes(os.urandom(64))


def test_enc_string_round_trip() -> None:
    key = _random_key()
    blob = vf.make_enc_string(key, b"hello vault")
    assert blob.startswith("2.")
    assert decrypt_sym(blob, key) == b"hello vault"


def test_decrypt_sym_rejects_tampered_mac() -> None:
    key = _random_key()
    blob = vf.make_enc_string(key, b"secret")
    # Flip a character in the ciphertext component to break the MAC.
    iv, ct, mac = blob[2:].split("|")
    tampered = f"2.{iv}|{'A' * len(ct)}|{mac}"
    with pytest.raises(VaultCryptoError, match="MAC verification failed"):
        decrypt_sym(tampered, key)


def test_decrypt_sym_wrong_key_fails() -> None:
    blob = vf.make_enc_string(_random_key(), b"secret")
    with pytest.raises(VaultCryptoError):
        decrypt_sym(blob, _random_key())


def test_parse_enc_string_requires_type_prefix() -> None:
    with pytest.raises(VaultCryptoError, match="missing type prefix"):
        bwcrypto.parse_enc_string("no-prefix-here")


def test_stretch_master_key_lengths() -> None:
    stretched = stretch_master_key(os.urandom(32))
    assert len(stretched.enc_key) == 32
    assert len(stretched.mac_key) == 32


def test_unlock_decrypts_org_cipher_end_to_end() -> None:
    """The full chain (master key -> user key -> RSA private key -> org key)."""
    sync = vf.build_sync(
        org_id="org-1",
        collection_id="col-123",
        collection_name="Chat",
        entries=[
            {
                "id": "entry-1",
                "name": "linkedin.com",
                "username": "alice@example.com",
                "password": "hunter2",
            }
        ],
    )
    profile = sync["profile"]
    keyring = unlock(
        master_password=vf.MASTER_PASSWORD,
        email=vf.EMAIL,
        kdf=KDF_PBKDF2,
        iterations=vf.KDF_ITERATIONS,
        memory=None,
        parallelism=None,
        protected_key=profile["key"],
        protected_private_key=profile["privateKey"],
        organizations=profile["organizations"],
    )
    cipher = sync["ciphers"][0]
    org_id = cipher["organizationId"]
    assert keyring.decrypt(cipher["name"], org_id) == "linkedin.com"
    assert keyring.decrypt(cipher["login"]["password"], org_id) == "hunter2"


def test_unlock_wrong_master_password_fails() -> None:
    sync = vf.build_sync(
        org_id="org-1",
        collection_id="col-123",
        collection_name="Chat",
        entries=[],
    )
    profile = sync["profile"]
    with pytest.raises(VaultCryptoError):
        unlock(
            master_password="wrong-password",
            email=vf.EMAIL,
            kdf=KDF_PBKDF2,
            iterations=vf.KDF_ITERATIONS,
            memory=None,
            parallelism=None,
            protected_key=profile["key"],
            protected_private_key=profile["privateKey"],
            organizations=profile["organizations"],
        )
