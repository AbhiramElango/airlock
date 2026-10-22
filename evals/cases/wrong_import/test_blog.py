from blog import post_url


def test_post_url():
    assert post_url("Hello World!") == "/posts/hello-world"
