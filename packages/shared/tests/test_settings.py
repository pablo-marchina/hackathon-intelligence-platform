from hackathon_shared.settings import Settings


def test_cors_string_is_split() -> None:
    settings = Settings(cors_origins="https://a.example, https://b.example")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_log_level_normalized() -> None:
    settings = Settings(log_level="debug")
    assert settings.log_level == "DEBUG"
