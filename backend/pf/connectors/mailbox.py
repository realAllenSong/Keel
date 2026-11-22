"""Collect recent bank statement attachments and official WeChat Pay downloads via read-only IMAP."""

from __future__ import annotations

import contextlib
import datetime as dt
import email
import http.client
import imaplib
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from pf.connectors.base import ConnectorError, FetchResult, Field
from pf.i18n import tr
from pf.importers.archive import is_zip, provider_for_member, statement_zip
from pf.settings import get_settings

LOOKBACK_DAYS = 45
MAX_MESSAGES = 300
BILL_NAME = re.compile(
    r"支付宝|微信|alipay|wechat|交易明细|交易流水|账单|对账单|statement|bill", re.I
)
EXTS = (".zip", ".csv", ".xlsx", ".xls", ".pdf")
WECHAT_BILL_HOST = "tenpay.wechatpay.cn"
WECHAT_BILL_PATH = "/userroll/userbilldownload/downloadfilefromemail"
MAX_BILL_BYTES = 50 * 1024 * 1024
SEEN_VERSION = 2  # Recheck the recent inbox once for links and PDF archives previously skipped.
KNOWN_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "qq.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
}


def _decode(s: str | None) -> str:
    return str(make_header(decode_header(s))) if s else ""


def host_for(address: str) -> str | None:
    return KNOWN_HOSTS.get(address.rsplit("@", 1)[-1].lower())


class _WeChatBillLink(HTMLParser):
    url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href") if tag == "a" else None
        if not href or self.url:
            return
        try:
            url = urlsplit(href)
        except ValueError:
            return
        if (
            url.scheme == "https"
            and url.netloc == WECHAT_BILL_HOST
            and url.path == WECHAT_BILL_PATH
            and url.query
        ):
            self.url = href


def _wechat_bill_link(msg: Message, subject: str) -> str | None:
    if parseaddr(msg.get("From", ""))[1].lower() != "wechatpay@tencent.com":
        return None
    if not subject.startswith("微信支付") or "账单" not in subject:
        return None
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            body = part.get_payload(decode=True)
            if isinstance(body, bytes):
                parser = _WeChatBillLink()
                parser.feed(body.decode(part.get_content_charset() or "utf-8", errors="replace"))
                if parser.url:
                    return parser.url
    return None


def _download_wechat(url: str) -> bytes | None:
    """Contact the verified host directly, without redirects or logging the signed link."""
    parsed = urlsplit(url)
    with contextlib.closing(http.client.HTTPSConnection(WECHAT_BILL_HOST, timeout=25)) as conn:
        conn.request("GET", f"{parsed.path}?{parsed.query}")
        with conn.getresponse() as resp:
            if resp.status != 200:
                return None
            data = resp.read(MAX_BILL_BYTES + 1)
            return data if len(data) <= MAX_BILL_BYTES and data.startswith(b"PK") else None


class Mailbox:
    source = "mailbox"
    label = "Mailbox (IMAP)"
    fields = (
        Field("address", "Email address", secret=False),
        Field("password", "App password"),
        Field("host", "IMAP server (optional)", secret=False),
    )

    def __init__(self, imap: type[imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL) -> None:
        self.imap = imap

    def fetch(
        self, client: httpx.Client, secrets: dict[str, Any], state: dict[str, Any]
    ) -> FetchResult:
        address = secrets["address"].strip()
        host = (secrets.get("host") or "").strip() or host_for(address)
        if not host:
            raise ConnectorError("Unknown mail provider: fill in the IMAP server")
        seen: list[str] = list(state.get("seen", []))
        out_dir = get_settings().data_dir / "mailbox"
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        warnings: list[str] = []
        try:
            box = self.imap(host)
            box.login(address, secrets["password"])
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ConnectorError(f"Mailbox sign-in failed: {exc}", reauth=True) from exc
        try:
            box.select("INBOX", readonly=True)
            since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
            _, data = box.uid("SEARCH", f"(SINCE {since})")
            uids = [u.decode() for u in (data[0] or b"").split()][-MAX_MESSAGES:]
            recovery: set[str] = set()
            if state.get("seen_version") != SEEN_VERSION and seen:
                _, old = box.uid(
                    "SEARCH",
                    f'(SINCE {since} OR FROM "wechatpay@tencent.com" FROM "message.cmbchina.com")',
                )
                recovery = {u.decode() for u in (old[0] or b"").split()[-MAX_MESSAGES:]}
            for uid in uids:
                key = f"{host}:{uid}"
                if key in seen and uid not in recovery:
                    continue
                _, parts = box.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                attachments = self._attachments(msg, out_dir, uid)
                link = (
                    _wechat_bill_link(msg, _decode(msg.get("Subject"))) if not attachments else None
                )
                if link:
                    try:
                        data = _download_wechat(link)
                    except (OSError, http.client.HTTPException):
                        warnings.append(tr("微信账单下载失败，下次同步会重试。"))
                        continue
                    if data:
                        path = out_dir / f"{uid}-微信支付账单.zip"
                        path.write_bytes(data)
                        if statement_zip(path) is not None:
                            attachments.append(path)
                        else:
                            path.unlink()
                    if not attachments:
                        warnings.append(
                            tr("微信账单链接未返回可导入的文件，请检查原邮件中的下载链接。")
                        )
                files.extend(attachments)
                if key not in seen:
                    seen.append(key)
        finally:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                box.logout()
        state["seen"] = seen[-5000:]
        state["seen_version"] = SEEN_VERSION
        return FetchResult([], warnings, files)

    def _attachments(self, msg: Message, out_dir: Path, uid: str) -> list[Path]:
        subject = _decode(msg.get("Subject"))
        found = []
        for part in msg.walk():
            name = _decode(part.get_filename())
            if not name or not name.lower().endswith(EXTS):
                continue
            if name.lower().endswith(".pdf") and provider_for_member(name) != "cmb":
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes) or not payload:
                continue
            safe = re.sub(r"[^\w.()（）\-一-鿿]+", "_", Path(name).name)  # i18n-allow
            path = out_dir / f"{uid}-{safe}"
            path.write_bytes(payload)
            looks_like_bill = BILL_NAME.search(name) or BILL_NAME.search(subject)
            if (is_zip(path) and statement_zip(path) is not None) or (
                not is_zip(path) and looks_like_bill
            ):
                found.append(path)
            else:
                path.unlink()
        return found
