from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_value = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_value.encode("utf-8"), 120_000)
    return salt_value, digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected_hash)


class GitHubConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    repo: str = Field(min_length=1, max_length=300)
    token: str = Field(min_length=1, max_length=300)
    branch: str = Field(default="main", min_length=1, max_length=120)


class SetupBootstrapRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    github_repo: str = Field(min_length=1, max_length=300)
    github_token: str = Field(min_length=1, max_length=300)
    github_branch: str = Field(default="main", min_length=1, max_length=120)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str | None = Field(default=None, min_length=1, max_length=120)
    new_password: str | None = Field(default=None, min_length=8, max_length=200)
    github_repo: str | None = Field(default=None, min_length=1, max_length=300)
    github_token: str | None = Field(default=None, min_length=1, max_length=300)
    github_branch: str | None = Field(default=None, min_length=1, max_length=120)
    ha_url: str | None = Field(default=None, min_length=1, max_length=300)
    ha_token: str | None = Field(default=None, min_length=1, max_length=300)
    ha_zwave_path: str | None = Field(default=None, min_length=1, max_length=120)
    ha_verify_ssl: bool | None = None


@dataclass
class StoredSettings:
    username: str
    password_salt: str
    password_hash: str
    github_repo: str
    github_token: str
    github_branch: str = "main"
    ha_url: str | None = None
    ha_token: str | None = None
    ha_zwave_path: str = "/api/nodes"
    ha_verify_ssl: bool = True

    def masked(self) -> dict:
        token = self.github_token
        if len(token) <= 8:
            masked_token = "*" * len(token)
        else:
            masked_token = f"{token[:4]}…{token[-4:]}"
        ha_token = self.ha_token or ""
        if not ha_token:
            masked_ha_token = ""
        elif len(ha_token) <= 8:
            masked_ha_token = "*" * len(ha_token)
        else:
            masked_ha_token = f"{ha_token[:4]}…{ha_token[-4:]}"
        return {
            "username": self.username,
            "github_repo": self.github_repo,
            "github_branch": self.github_branch,
            "github_token_masked": masked_token,
            "ha_url": self.ha_url,
            "ha_zwave_path": self.ha_zwave_path,
            "ha_verify_ssl": self.ha_verify_ssl,
            "ha_token_masked": masked_ha_token,
        }
