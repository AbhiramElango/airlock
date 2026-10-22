import re


def slugify(title):
    """'Hello World!' -> 'hello-world'"""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
