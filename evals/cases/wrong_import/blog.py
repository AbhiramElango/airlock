from text_utils import slugfy


def post_url(title):
    return "/posts/" + slugfy(title)


if __name__ == "__main__":
    print(post_url("Hello World!"))
