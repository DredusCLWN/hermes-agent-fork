"""Config — imported by no one, but imports models."""
from models import User


def get_default_user() -> User:
    return User(name="Guest", email="guest@test.com")
