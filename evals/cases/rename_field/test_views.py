from models import User
from views import profile_header


def test_profile_header():
    assert profile_header(User(1, "Ada Lovelace", "ada@example.com")) == "Welcome back, Ada Lovelace!"
