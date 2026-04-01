import time

import jwt

from app.config import settings
from app.core.security import (
    create_state_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
    verify_state_token,
)


def test_hash_password_returns_string():
    test_password = "my_test_password"
    hashed = hash_password(test_password)
    assert isinstance(hashed, str)


def test_hash_password_uses_random_salt():
    test_password = "my_test_password"
    hashed1 = hash_password(test_password)
    hashed2 = hash_password(test_password)
    assert hashed1 != hashed2  # Due to salting, hashes should differ


def test_hash_password_handles_unicode_passwords():
    unicode_password = "pǎşşŵøŕđ!@#$%^&*()"
    hashed = hash_password(unicode_password)
    assert isinstance(hashed, str)


def test_hash_password_handles_empty_password():
    empty_password = ""
    hashed = hash_password(empty_password)
    assert isinstance(hashed, str)


def test_hash_password_uses_bcrypt_format():
    test_password = "my_test_password"
    hashed = hash_password(test_password)
    assert hashed.startswith("$2b$")  # bcrypt hashes start with $2b$
    assert "$12$" in hashed  # bcrypt with 12 rounds should include $12$


def test_verify_password_correct():
    test_password = "my_test_password"
    hashed = hash_password(test_password)
    assert verify_password(test_password, hashed) is True


def test_verify_password_incorrect():
    test_password = "my_test_password"
    hashed = hash_password(test_password)
    assert verify_password("wrong_password", hashed) is False


def test_verify_password_empty():
    test_password = "my_test_password"
    hashed = hash_password(test_password)
    assert verify_password("", hashed) is False


def test_verify_password_with_invalid_hash():
    test_password = "my_test_password"
    invalid_hash = "not_a_valid_hash"
    assert verify_password(test_password, invalid_hash) is False


def test_generate_refresh_token_returns_string():
    token = generate_refresh_token()
    assert isinstance(token, str)


def test_generate_refresh_token_is_unique():
    # Two calls must never return the same value (needed for security)
    token1 = generate_refresh_token()
    token2 = generate_refresh_token()
    assert token1 != token2


def test_generate_refresh_token_has_sufficient_length():
    # 32 bytes of urlsafe base64 = 43 characters minimum
    token = generate_refresh_token()
    assert len(token) >= 43


def test_hash_token_returns_string():
    result = hash_token("some_token")
    assert isinstance(result, str)


def test_hash_token_is_64_characters():
    # SHA-256 always produces a 64-character hex string
    result = hash_token("some_token")
    assert len(result) == 64


def test_hash_token_is_deterministic():
    # Same input must always produce same hash (needed for DB lookup)
    token = "same_token"
    assert hash_token(token) == hash_token(token)


def test_hash_token_different_inputs_produce_different_hashes():
    assert hash_token("token_a") != hash_token("token_b")


# --- State Token (OAuth CSRF) ---


def test_create_state_token_returns_string():
    token = create_state_token()
    assert isinstance(token, str)


def test_create_state_token_is_valid_jwt():
    token = create_state_token()
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert "exp" in payload


def test_create_state_token_has_oauth_state_type():
    token = create_state_token()
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert payload["type"] == "oauth_state"


def test_create_state_token_expires_within_10_minutes():
    before = time.time()
    token = create_state_token()
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    assert payload["exp"] <= before + 600 + 2  # 10 min + 2s clock tolerance


def test_verify_state_token_returns_true_for_valid_token():
    token = create_state_token()
    assert verify_state_token(token) is True


def test_verify_state_token_returns_false_for_garbage():
    assert verify_state_token("not.a.jwt") is False


def test_verify_state_token_returns_false_for_expired_token():
    expired = jwt.encode(
        {"type": "oauth_state", "exp": time.time() - 1},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert verify_state_token(expired) is False


def test_verify_state_token_returns_false_for_wrong_type():
    wrong_type = jwt.encode(
        {"type": "access", "exp": time.time() + 600},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert verify_state_token(wrong_type) is False
