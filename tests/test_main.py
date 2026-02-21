from app.utils import is_valid_url


def test_valid_url():
    assert is_valid_url("https://example.com/post/1")


def test_invalid_url_scheme():
    assert not is_valid_url("ftp://example.com/file")


def test_invalid_url_netloc():
    assert not is_valid_url("https:///abc")
