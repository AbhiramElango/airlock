from dataclasses import dataclass


@dataclass
class User:
    id: int
    full_name: str
    email: str
