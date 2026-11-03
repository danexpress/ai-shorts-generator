import os
from dataclasses import replace

import pytest
from conftest import rows
from cryptography.fernet import Fernet
from social_helpers import callback, connect

from app.errors import ApiError
from app.social.db_models import SocialAccount
from app.social.security import KMS_CONTEXT, KMS_PREFIX, TokenCipher

KEY_ID = "arn:aws:kms:us-east-1:111122223333:key/test"


class FakeKMS:
    """Enforces the key ID and encryption context the way AWS KMS does."""

    def __init__(self):
        self.wrapped, self.calls, self.down = {}, [], False

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):
        self.calls.append("generate")
        if self.down:
            raise RuntimeError("KMS unavailable")
        assert (KeyId, KeySpec, EncryptionContext) == (KEY_ID, "AES_256", KMS_CONTEXT)
        plaintext, blob = os.urandom(32), os.urandom(48)
        self.wrapped[blob] = (KeyId, plaintext)
        return {"Plaintext": plaintext, "CiphertextBlob": blob, "KeyId": KeyId}

    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId=None):
        self.calls.append("decrypt")
        key_id, plaintext = self.wrapped[CiphertextBlob]
        if EncryptionContext != KMS_CONTEXT or (KeyId and KeyId != key_id):
            raise RuntimeError("InvalidCiphertextException")
        return {"Plaintext": plaintext, "KeyId": key_id}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def kms_cipher(kms, keys="", clock=None, **options):
    return TokenCipher(keys, KEY_ID, kms_client=kms, clock=clock or Clock(), **options)


def test_kms_envelope_round_trip_keeps_the_owner_binding():
    kms = FakeKMS()
    cipher = kms_cipher(kms)
    stored = cipher.encrypt({"access": "secret-token"}, "usr_1", "youtube")
    assert stored.startswith(KMS_PREFIX) and "secret-token" not in stored
    assert cipher.decrypt(stored, "usr_1", "youtube") == {"access": "secret-token"}
    for owner, platform in [("usr_2", "youtube"), ("usr_1", "x")]:
        with pytest.raises(ApiError, match="reconnected"):
            cipher.decrypt(stored, owner, platform)


def test_data_keys_are_reused_briefly_and_unwrapped_once_per_process():
    kms, clock = FakeKMS(), Clock()
    cipher = kms_cipher(kms, clock=clock, reuse_seconds=300, reuse_count=50)
    values = [cipher.encrypt(f"v{i}", "u", "x") for i in range(50)]
    assert kms.calls.count("generate") == 1  # every upload checkpoint would otherwise call KMS
    cipher.encrypt("one more", "u", "x")
    assert kms.calls.count("generate") == 2  # reuse count reached
    clock.now += 301
    cipher.encrypt("later", "u", "x")
    assert kms.calls.count("generate") == 3  # reuse window expired
    another_process = kms_cipher(kms)
    assert [another_process.decrypt(v, "u", "x") for v in values] == [f"v{i}" for i in range(50)]
    assert kms.calls.count("decrypt") == 1  # cached after the first unwrap


def test_existing_fernet_values_still_decrypt_after_enabling_kms():
    key = Fernet.generate_key().decode()
    legacy = TokenCipher(key).encrypt("old-token", "u", "tiktok")
    upgraded = kms_cipher(FakeKMS(), keys=key)
    assert upgraded.decrypt(legacy, "u", "tiktok") == "old-token"
    assert upgraded.encrypt("new-token", "u", "tiktok").startswith(KMS_PREFIX)


def test_kms_failures_fail_closed():
    kms = FakeKMS()
    cipher = kms_cipher(kms)
    stored = cipher.encrypt("token", "u", "x")
    kms.down = True
    with pytest.raises(ApiError) as raised:
        kms_cipher(kms).encrypt("token", "u", "x")  # never falls back to plaintext
    assert raised.value.code == "SOCIAL_UNAVAILABLE"
    other_key = TokenCipher("", "arn:aws:kms:us-east-1:111122223333:key/other", kms_client=kms)
    with pytest.raises(ApiError) as raised:
        other_key.decrypt(stored, "u", "x")  # data key from a different KMS key
    assert raised.value.code == "SOCIAL_UNAVAILABLE"


def test_kms_alone_is_a_valid_configuration(social):
    service, _adapter = social
    TokenCipher("", KEY_ID).check()  # no Fernet keys needed
    with pytest.raises(ApiError):
        TokenCipher("", "").check()
    kms_only = replace(service.settings, encryption_keys="", kms_key_id=KEY_ID)
    assert kms_only.available("youtube")
    assert not replace(kms_only, kms_key_id="").available("youtube")


def test_connected_accounts_store_kms_wrapped_tokens(client, headers, social, database):
    service, _adapter = social
    service.cipher = kms_cipher(FakeKMS())
    state, binding = connect(client, headers)
    assert callback(client, state, binding).status_code == 200
    account = rows(database, SocialAccount)[0]
    assert account["access_token_encrypted"].startswith(KMS_PREFIX)
    assert account["refresh_token_encrypted"].startswith(KMS_PREFIX)
    assert service.token(account["id"], account["user_id"], "youtube") == "private-access"
