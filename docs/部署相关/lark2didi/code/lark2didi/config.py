from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / "conf" / ".env"
DEFAULT_BASE_URL = "https://open.feishu.cn/open-apis"
DEFAULT_DIDI_BASE_URL = "https://api.es.xiaojukeji.com"


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def load(cls, env_path: Path | str = DEFAULT_ENV_PATH) -> "FeishuConfig":
        path = Path(env_path)
        file_values = dotenv_values(path) if path.is_file() else {}

        app_id = (
            os.getenv("FEISHU_APP_ID")
            or file_values.get("app_id")
            or file_values.get("FEISHU_APP_ID")
            or ""
        ).strip()
        app_secret = (
            os.getenv("FEISHU_APP_SECRET")
            or file_values.get("app_secret")
            or file_values.get("FEISHU_APP_SECRET")
            or ""
        ).strip()
        base_url = (
            os.getenv("FEISHU_BASE_URL")
            or file_values.get("FEISHU_BASE_URL")
            or DEFAULT_BASE_URL
        ).strip().rstrip("/")

        missing = [
            name
            for name, value in (("app_id", app_id), ("app_secret", app_secret))
            if not value
        ]
        if missing:
            raise ConfigError(
                f"缺少飞书配置: {', '.join(missing)}（读取 {path}）"
            )
        return cls(app_id=app_id, app_secret=app_secret, base_url=base_url)


@dataclass(frozen=True, slots=True)
class DidiConfig:
    client_id: str
    client_secret: str
    sign_key: str
    company_id: str
    base_url: str = DEFAULT_DIDI_BASE_URL
    sign_method: str | None = None
    encrypt_key: str | None = None

    @classmethod
    def load(cls, env_path: Path | str = DEFAULT_ENV_PATH) -> "DidiConfig":
        path = Path(env_path)
        file_values = dotenv_values(path) if path.is_file() else {}

        def pick(*names: str) -> str:
            for name in names:
                env_value = os.getenv(name)
                if env_value:
                    return env_value.strip()
                file_value = file_values.get(name)
                if file_value:
                    return file_value.strip()
            return ""

        client_id = pick("DIDI_CLIENT_ID", "DIDI_Client_ID")
        client_secret = pick("DIDI_CLIENT_SECRET", "DIDI_Client_Secret")
        sign_key = pick("DIDI_SIGN_KEY", "DIDI_Sign_Key")
        company_id = pick("DIDI_COMPANY_ID", "DIDI_Company_ID")
        base_url = (pick("DIDI_BASE_URL") or DEFAULT_DIDI_BASE_URL).rstrip("/")
        sign_method = pick("DIDI_SIGN_METHOD", "DIDI_Sign_Method").lower() or None
        encrypt_key = pick("DIDI_ENCRYPT_KEY", "DIDI_Encrypt_Key") or None

        missing = [
            name
            for name, value in (
                ("DIDI_Client_ID", client_id),
                ("DIDI_Client_Secret", client_secret),
                ("DIDI_Sign_Key", sign_key),
                ("DIDI_Company_ID", company_id),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"缺少滴滴配置: {', '.join(missing)}（读取 {path}）"
            )
        if sign_method not in (None, "md5", "sha256"):
            raise ConfigError("DIDI_Sign_Method 只能是 md5 或 sha256")

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            sign_key=sign_key,
            company_id=company_id,
            base_url=base_url,
            sign_method=sign_method,
            encrypt_key=encrypt_key,
        )
