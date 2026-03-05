from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import create_access_token, decode_access_token


def test_returns_string():
    token = create_access_token(data={"sub": "testuser"})
    assert isinstance(token, str)


def test_payload_is_preserved():
    payload = {"sub": "testuser", "role": "admin"}
    token = create_access_token(data=payload)
    decoded = decode_access_token(token)
    assert decoded["sub"] == payload["sub"]
    assert decoded["role"] == payload["role"]


def test_iat_is_present():
    token = create_access_token(data={"sub": "testuser"})
    decoded = decode_access_token(token)
    assert "iat" in decoded
    iat = datetime.fromtimestamp(decoded["iat"], tz=UTC)
    assert iat <= datetime.now(UTC)  # Issued at should be in the past


def test_expires_delta_is_respected():
    expires_in = timedelta(minutes=10)
    token = create_access_token(data={"sub": "testuser"}, expires_delta=expires_in)
    decoded = decode_access_token(token)
    assert "exp" in decoded
    exp = datetime.fromtimestamp(decoded["exp"], tz=UTC)
    expected_exp = datetime.now(UTC) + expires_in
    # Allow a small margin for processing time
    assert abs((exp - expected_exp).total_seconds()) < 5


def test_exp_is_in_the_future():
    token = create_access_token(data={"sub": "testuser"})
    decoded = decode_access_token(token)
    exp = datetime.fromtimestamp(decoded["exp"], tz=UTC)
    assert exp > datetime.now(UTC)


def test_expired_token_raises_exception():
    expired_token = create_access_token(
        data={"sub": "testuser"}, expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token)


def test_invalid_token_raises_exception():
    invalid_token = "this.is.not.a.valid.token"
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(invalid_token)


def test_wrong_secret_is_rejected():
    fake_token = jwt.encode({"sub": "123"}, "wrongsecret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(fake_token)


def test_tampered_token_raises_exception():
    token = create_access_token(data={"sub": "testuser"})
    parts = token.split(".")
    tampered_payload = jwt.encode(
        {"sub": "hacker"}, "fakekey", algorithm="HS256"
    ).split(".")[1]
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered_token)
