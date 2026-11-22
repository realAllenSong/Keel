"""Statement archives: Alipay and WeChat Pay mail their exports as password-protected zips.

Only zips that contain a supported statement member are accepted; anything else
in ~/Downloads stays untouched. Member names are visible without the password, so a locked zip
can be recognised and parked until the user types the 6-digit code from the app.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Literal

import pyzipper

from pf.i18n import tr
from pf.importers.base import ImportError_

PasswordProvider = Literal["alipay", "wechat", "cmb", "ningbo", "other"]

MEMBER_SUFFIXES = (".csv", ".xlsx", ".xls")
MAX_MEMBER_BYTES = 50 * 1024 * 1024


def _name(info: pyzipper.ZipInfo) -> str:
    """Chinese member names are usually GBK without the UTF-8 flag; zipfile decoded them as cp437."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("gb18030")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _statement_member(zf: pyzipper.ZipFile) -> pyzipper.ZipInfo | None:
    members = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = _name(info)
        if name.lower().endswith(MEMBER_SUFFIXES) or (
            name.lower().endswith(".pdf") and provider_for_member(name) == "cmb"
        ):
            members.append(info)
    return members[0] if len(members) == 1 else None


def is_zip(path: Path) -> bool:
    return path.suffix.lower() == ".zip" and zipfile.is_zipfile(path)


def statement_zip(path: Path) -> tuple[str, bool] | None:
    """(member name, needs password) for a zip holding exactly one statement file, else None."""
    if not is_zip(path):
        return None
    try:
        with pyzipper.AESZipFile(path) as zf:
            info = _statement_member(zf)
            if info is None or info.file_size > MAX_MEMBER_BYTES:
                return None
            return _name(info), bool(info.flag_bits & 0x1)
    except (zipfile.BadZipFile, pyzipper.BadZipFile, OSError):
        return None


def extract_statement(path: Path, dest_dir: Path, password: str | None = None) -> Path:
    """Write the single statement member to dest_dir and return its path."""
    found = statement_zip(path)
    if found is None:
        raise ImportError_(f"{path.name}: " + tr("压缩包里没有可识别的账单文件"))
    name, locked = found
    if locked and not password:
        raise ImportError_(f"{path.name}: " + tr("需要解压密码"))
    with pyzipper.AESZipFile(path) as zf:
        info = _statement_member(zf)
        assert info is not None
        try:
            data = zf.read(info, pwd=password.encode() if password else None)
        except RuntimeError as exc:  # wrong password (ZipCrypto and AES both raise RuntimeError)
            raise ImportError_(tr("解压密码不对，请用 App 里最新收到的那一个")) from exc
        except (zipfile.BadZipFile, pyzipper.BadZipFile) as exc:
            raise ImportError_(tr("解压密码不对，请用 App 里最新收到的那一个")) from exc
    safe = re.sub(r"[^\w.()（）\-一-鿿]+", "_", Path(name).name) or "statement.csv"  # i18n-allow
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / safe
    out.write_bytes(data)
    return out


def provider_for_member(member: str) -> PasswordProvider:
    """Identify the statement issuer from its archive member or original file name."""
    name = member.casefold()
    if "支付宝" in name or "alipay" in name:
        return "alipay"
    if "微信" in name or "wechat" in name:
        return "wechat"
    if "招商银行" in name or "china merchants bank" in name:
        return "cmb"
    if "宁波银行" in name or "bank of ningbo" in name:
        return "ningbo"
    return "other"
