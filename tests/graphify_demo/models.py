"""Data models — imported by both main and utils."""


class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email


class Order:
    def __init__(self, user: User, item: str, qty: int):
        self.user = user
        self.item = item
        self.qty = qty
