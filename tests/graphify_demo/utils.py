"""Utility functions — imported by main."""
from models import User, Order


def format_output(user: User, order: Order) -> str:
    return f"{user.name} ordered {order.qty}x {order.item}"


def validate_input(order: Order) -> bool:
    if order.qty <= 0:
        return False
    if not order.item:
        return False
    return True
