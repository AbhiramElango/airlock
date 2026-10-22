from models import User


def profile_header(user: User) -> str:
    return f"Welcome back, {user.name}!"
