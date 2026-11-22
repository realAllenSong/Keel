"""Remember successful statement archive passwords separately from ledger data.

Callers remember a code only after a successful unlock or an explicit user save.
Secrets uses the Keel Keychain service, with PF_SECRETS_FILE for tests/headless use.
"""

from __future__ import annotations

import datetime as dt
import threading

from pf.connectors.base import Secrets
from pf.importers.archive import PasswordProvider
from pf.importers.archive import provider_for_member as provider_for_member

_PROVIDERS: tuple[PasswordProvider, ...] = ("alipay", "wechat", "cmb", "ningbo", "other")
_LOCK = threading.Lock()


class ArchivePasswords:
    """A bounded, newest-first password history with metadata-only status."""

    @staticmethod
    def _key(provider: str) -> str:
        if provider not in _PROVIDERS:
            raise ValueError("Unknown statement password provider")
        return f"statement-passwords:{provider}"

    @classmethod
    def status(cls) -> list[dict]:
        with _LOCK:
            result = []
            for provider in _PROVIDERS:
                values = Secrets.get(cls._key(provider)) or {}
                count = len(values.get("passwords", []))
                result.append(
                    {
                        "provider": provider,
                        "configured": count > 0,
                        "count": count,
                        "updated_at": values.get("updated_at") if count else None,
                    }
                )
            return result

    @classmethod
    def remember(cls, provider: str, password: str) -> None:
        key = cls._key(provider)
        if not isinstance(password, str) or not 1 <= len(password.strip()) <= 128:
            raise ValueError("Statement password must contain 1 to 128 characters")
        password = password.strip()
        with _LOCK:
            values = Secrets.get(key) or {}
            previous = values.get("passwords", [])
            passwords = [password, *(old for old in previous if old != password)][:5]
            Secrets.set(
                key,
                {"passwords": passwords, "updated_at": dt.datetime.now(dt.UTC).isoformat()},
            )

    @classmethod
    def candidates(cls, provider: str) -> list[str]:
        key = cls._key(provider)
        with _LOCK:
            values = Secrets.get(key) or {}
            return list(values.get("passwords", []))

    @classmethod
    def clear(cls, provider: str) -> None:
        key = cls._key(provider)
        with _LOCK:
            if Secrets.get(key) is not None:
                Secrets.delete(key)
