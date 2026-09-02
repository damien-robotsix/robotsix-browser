"""Bitwarden / Vaultwarden vault-unlock and field-decryption primitives.

Vaultwarden's ``client_credentials`` (API-key) flow yields an access token but
**not** the vault symmetric key, so cipher fields come back as encrypted
"EncString" blobs (``2.<iv>|<ct>|<mac>``) instead of plaintext.  This module
derives the symmetric key from the account's master password and decrypts those
blobs.

Unlock chain (the Bitwarden key hierarchy)::

    master_key   = KDF(master_password, email)              # PBKDF2 / Argon2id
    stretched    = HKDF-Expand(master_key)   -> (enc|mac)   # 32 + 32 bytes
    user_key     = decrypt(profile.key,        stretched)   # 64 bytes
    private_key  = decrypt(profile.privateKey, user_key)    # RSA (PKCS8 DER)
    org_key[id]  = rsa_decrypt(org.key,        private_key)  # 64 bytes each

A cipher that lives in an organization collection is encrypted with that
organization's key; a personal cipher is encrypted with the user key.
:class:`Keyring` resolves the correct key per cipher and decrypts each
EncString to plaintext.

This module never logs plaintext; callers decide what (if anything) they
surface.  Decryption failures raise :class:`VaultCryptoError` with a message
that never contains a secret value.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hashes import SHA1, SHA256
from cryptography.hazmat.primitives.serialization import load_der_private_key

#: Bitwarden ``Kdf`` enum values.
KDF_PBKDF2 = 0
KDF_ARGON2ID = 1

#: AES block size / minimum EncString component length (bytes).
_AES_BLOCK = 16
_SHA256_LEN = 32


class VaultCryptoError(RuntimeError):
    """Raised when vault unlock or field decryption fails."""


@dataclass(frozen=True)
class SymmetricKey:
    """A Bitwarden symmetric key split into its AES enc and HMAC mac halves."""

    enc_key: bytes
    mac_key: bytes

    @classmethod
    def from_bytes(cls, raw: bytes) -> SymmetricKey:
        """Split a 64-byte (enc||mac) or 32-byte (enc-only) key blob."""
        if len(raw) == 64:
            return cls(enc_key=raw[:32], mac_key=raw[32:64])
        if len(raw) == 32:
            return cls(enc_key=raw, mac_key=b"")
        raise VaultCryptoError(f"unexpected symmetric key length {len(raw)}")


def _b64(value: str) -> bytes:
    try:
        return base64.b64decode(value)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise VaultCryptoError("malformed base64 in EncString") from exc


def parse_enc_string(enc: str) -> tuple[int, list[bytes]]:
    """Parse an ``<type>.<payload>`` EncString into its type and byte parts."""
    type_str, sep, rest = enc.partition(".")
    if not sep:
        raise VaultCryptoError("malformed EncString: missing type prefix")
    try:
        enc_type = int(type_str)
    except ValueError as exc:
        raise VaultCryptoError("malformed EncString type prefix") from exc
    return enc_type, [_b64(part) for part in rest.split("|")]


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise VaultCryptoError("empty plaintext after decryption")
    pad = data[-1]
    if pad < 1 or pad > _AES_BLOCK or pad > len(data):
        raise VaultCryptoError("invalid PKCS7 padding")
    if data[-pad:] != bytes([pad]) * pad:
        raise VaultCryptoError("invalid PKCS7 padding")
    return data[:-pad]


def _aes_cbc_decrypt(enc_key: bytes, iv: bytes, ct: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    return _pkcs7_unpad(decryptor.update(ct) + decryptor.finalize())


def decrypt_sym(enc: str, key: SymmetricKey) -> bytes:
    """Decrypt an AES-CBC EncString (type 0 or 2) with ``key``.

    Type 2 (AES-256-CBC + HMAC-SHA256) is authenticated: the MAC is verified in
    constant time before decryption.  Type 0 is unauthenticated legacy CBC.
    """
    enc_type, parts = parse_enc_string(enc)
    if enc_type == 2:
        if len(parts) != 3:
            raise VaultCryptoError("type-2 EncString needs iv|ct|mac")
        iv, ct, mac = parts
        if not key.mac_key:
            raise VaultCryptoError("MAC key required to decrypt a type-2 EncString")
        expected = hmac.new(key.mac_key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, mac):
            raise VaultCryptoError("EncString MAC verification failed")
    elif enc_type == 0:
        if len(parts) != 2:
            raise VaultCryptoError("type-0 EncString needs iv|ct")
        iv, ct = parts
    else:
        raise VaultCryptoError(f"unsupported symmetric EncString type {enc_type}")
    return _aes_cbc_decrypt(key.enc_key, iv, ct)


def decrypt_rsa(enc: str, private_key: rsa.RSAPrivateKey) -> bytes:
    """Decrypt an RSA-OAEP EncString (type 4 = SHA-1, type 6 = SHA-256)."""
    enc_type, parts = parse_enc_string(enc)
    if not parts:
        raise VaultCryptoError("empty RSA EncString")
    if enc_type == 4:
        algo: SHA1 | SHA256 = SHA1()
    elif enc_type == 6:
        algo = SHA256()
    else:
        raise VaultCryptoError(f"unsupported RSA EncString type {enc_type}")
    return private_key.decrypt(
        parts[0],
        padding.OAEP(mgf=padding.MGF1(algorithm=algo), algorithm=algo, label=None),
    )


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """RFC-5869 HKDF-Expand with SHA-256 (Bitwarden ``stretchKey``)."""
    blocks = -(-length // _SHA256_LEN)
    okm = b""
    block = b""
    for counter in range(1, blocks + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
    return okm[:length]


def stretch_master_key(master_key: bytes) -> SymmetricKey:
    """Stretch a 32-byte master key into a 32+32 enc/mac symmetric key."""
    return SymmetricKey(
        enc_key=_hkdf_expand(master_key, b"enc", 32),
        mac_key=_hkdf_expand(master_key, b"mac", 32),
    )


def _argon2id(
    password: bytes,
    salt: bytes,
    iterations: int,
    memory_mib: int,
    parallelism: int,
) -> bytes:
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    except ImportError as exc:  # pragma: no cover - depends on cryptography build
        raise VaultCryptoError(
            "Argon2id KDF requires a newer 'cryptography' build"
        ) from exc
    kdf = Argon2id(
        salt=hashlib.sha256(salt).digest(),
        length=32,
        iterations=iterations,
        lanes=parallelism,
        memory_cost=memory_mib * 1024,
    )
    return kdf.derive(password)


def derive_master_key(
    master_password: str,
    email: str,
    *,
    kdf: int,
    iterations: int,
    memory: int | None = None,
    parallelism: int | None = None,
) -> bytes:
    """Derive the 32-byte master key from the master password and email salt."""
    salt = email.strip().lower().encode("utf-8")
    password = master_password.encode("utf-8")
    if kdf == KDF_PBKDF2:
        return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, dklen=32)
    if kdf == KDF_ARGON2ID:
        return _argon2id(password, salt, iterations, memory or 64, parallelism or 4)
    raise VaultCryptoError(f"unsupported KDF type {kdf}")


@dataclass(frozen=True)
class Keyring:
    """Resolves the decryption key for a cipher and decrypts its fields."""

    user_key: SymmetricKey
    org_keys: Mapping[str, SymmetricKey]

    def _key_for(self, organization_id: str | None) -> SymmetricKey:
        if organization_id:
            key = self.org_keys.get(organization_id)
            if key is None:
                raise VaultCryptoError(
                    f"no key available for organization {organization_id!r}"
                )
            return key
        return self.user_key

    def decrypt(self, enc: str | None, organization_id: str | None = None) -> str:
        """Decrypt an EncString to a UTF-8 string (empty input -> empty str)."""
        if not enc:
            return ""
        return decrypt_sym(enc, self._key_for(organization_id)).decode("utf-8")


def unlock(
    *,
    master_password: str,
    email: str,
    kdf: int,
    iterations: int,
    memory: int | None,
    parallelism: int | None,
    protected_key: str,
    protected_private_key: str | None = None,
    organizations: Sequence[Mapping[str, str]] = (),
) -> Keyring:
    """Unlock the vault, returning a :class:`Keyring` for field decryption.

    ``protected_key`` is ``profile.key``, ``protected_private_key`` is
    ``profile.privateKey`` and ``organizations`` is ``profile.organizations``
    from ``GET /api/sync``.  Organization keys are only unlocked when the
    account actually belongs to organizations (org ciphers need them).
    """
    master_key = derive_master_key(
        master_password,
        email,
        kdf=kdf,
        iterations=iterations,
        memory=memory,
        parallelism=parallelism,
    )
    stretched = stretch_master_key(master_key)
    user_key = SymmetricKey.from_bytes(decrypt_sym(protected_key, stretched))

    org_keys: dict[str, SymmetricKey] = {}
    orgs = [org for org in organizations if org.get("id") and org.get("key")]
    if orgs:
        if not protected_private_key:
            raise VaultCryptoError(
                "organizations present but account has no private key to unlock"
            )
        private_der = decrypt_sym(protected_private_key, user_key)
        private_key = load_der_private_key(private_der, password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise VaultCryptoError("account private key is not RSA")
        for org in orgs:
            org_keys[org["id"]] = SymmetricKey.from_bytes(
                decrypt_rsa(org["key"], private_key)
            )
    return Keyring(user_key=user_key, org_keys=org_keys)
