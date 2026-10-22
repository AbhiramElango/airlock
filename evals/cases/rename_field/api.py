from models import User


def user_json(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email}
