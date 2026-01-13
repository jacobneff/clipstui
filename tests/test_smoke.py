from clipstui import __version__


def test_import_package() -> None:
    assert isinstance(__version__, str)
