import bcrypt


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Args:
        plain_password: The plain-text password to hash

    Returns:
        The hashed password as a string (ready for database storage)
    """
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a hashed password.

    Args:
        plain_password: The plain-text password to verify
        hashed_password: The stored hash from the database

    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        # This can happen if the hashed_password is not a valid bcrypt hash
        return False
