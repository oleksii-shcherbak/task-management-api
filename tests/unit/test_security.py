from app.core.security import hash_password, verify_password


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
