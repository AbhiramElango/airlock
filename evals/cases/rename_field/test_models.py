from models import User


def test_user_fields():
    assert User(1, "Ada Lovelace", "ada@example.com").full_name == "Ada Lovelace"
