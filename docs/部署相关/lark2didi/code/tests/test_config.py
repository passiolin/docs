from __future__ import annotations

from pathlib import Path

import pytest

from lark2didi.config import ConfigError, DidiConfig, FeishuConfig


def test_load_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_BASE_URL", raising=False)

    env = tmp_path / ".env"
    env.write_text('app_id="cli_x"\napp_secret="s3cret"\n', encoding="utf-8")

    config = FeishuConfig.load(env)

    assert config.app_id == "cli_x"
    assert config.app_secret == "s3cret"
    assert config.base_url == "https://open.feishu.cn/open-apis"


def test_env_vars_override_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "cli_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "env_secret")

    env = tmp_path / ".env"
    env.write_text('app_id="cli_file"\napp_secret="file_secret"\n', encoding="utf-8")

    config = FeishuConfig.load(env)

    assert config.app_id == "cli_env"
    assert config.app_secret == "env_secret"


def test_missing_values_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    env = tmp_path / ".env"
    env.write_text('app_id=""\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        FeishuConfig.load(env)


def test_didi_config_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DIDI_CLIENT_ID",
        "DIDI_Client_ID",
        "DIDI_CLIENT_SECRET",
        "DIDI_Client_Secret",
        "DIDI_SIGN_KEY",
        "DIDI_Sign_Key",
        "DIDI_COMPANY_ID",
        "DIDI_Company_ID",
        "DIDI_BASE_URL",
        "DIDI_SIGN_METHOD",
        "DIDI_Sign_Method",
        "DIDI_ENCRYPT_KEY",
        "DIDI_Encrypt_Key",
    ):
        monkeypatch.delenv(name, raising=False)

    env = tmp_path / ".env"
    env.write_text(
        'DIDI_Client_ID="cid"\n'
        'DIDI_Client_Secret="secret"\n'
        'DIDI_Sign_Key="sk"\n'
        'DIDI_Company_ID="company"\n',
        encoding="utf-8",
    )

    config = DidiConfig.load(env)

    assert config.client_id == "cid"
    assert config.client_secret == "secret"
    assert config.sign_key == "sk"
    assert config.company_id == "company"
    assert config.base_url == "https://api.es.xiaojukeji.com"
    assert config.sign_method is None


def test_didi_config_rejects_invalid_sign_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIDI_SIGN_METHOD", "sha1")

    env = tmp_path / ".env"
    env.write_text(
        'DIDI_Client_ID="cid"\n'
        'DIDI_Client_Secret="secret"\n'
        'DIDI_Sign_Key="sk"\n'
        'DIDI_Company_ID="company"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="DIDI_Sign_Method"):
        DidiConfig.load(env)
