from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
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
