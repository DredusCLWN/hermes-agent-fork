"""Main entry point — imports from utils and models."""
from utils import format_output, validate_input
from models import User, Order


def main():
    user = User(name="Alice", email="alice@test.com")
    order = Order(user=user, item="Widget", qty=2)

    if not validate_input(order):
        print("Invalid input")
        return

    print(format_output(user, order))


if __name__ == "__main__":
    main()
