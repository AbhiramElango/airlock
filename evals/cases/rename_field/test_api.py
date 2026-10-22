from api import user_json
from models import User


def test_user_json():
    assert user_json(User(1, "Ada", "ada@example.com")) == {"id": 1, "name": "Ada", "email": "ada@example.com"}
