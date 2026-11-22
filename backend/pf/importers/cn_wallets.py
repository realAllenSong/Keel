"""Alipay (支付宝) and WeChat Pay (微信支付) bill exports.

Both start with a preamble (account, period, totals, notes) before the header row, and carry the
direction in a separate 收/支 column instead of a signed amount:
  收入 = money in, 支出 = money out, 不计收支 (Alipay) / "/" (WeChat) = moving your own money
  (余额宝 in/out, top-ups, withdrawals, card repayments). Those transfers are skipped.
Closed or failed payments never moved money and are skipped too. WeChat refunds are netted into
the original payment (its 当前状态 records the refund). Alipay marks refunds and 余额宝 earnings
不计收支. Book confirmed earnings as interest and refunds linked to a successful purchase as
negative expenses; a closed original and its refund both drop out.

Each login gets its own CNY account (Assets:Alipay:<login>), named from the header's account line
(phone numbers by their last four digits), so a transfer between two logins of yours pairs like
any other transfer; the header's name is remembered as you (see pf.ledger.ownflows).
The wallet accounts are marked `flow_only`: exports have no
running balance and most payments are funded by linked cards or 花呗, so the account's balance
is not your money. Reports count its income and spending; net worth leaves it out.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pf.importers.base import ImportError_, ParsedStatement, RawTxn

HEADER_SCAN_ROWS = 60


def _clean(v: object) -> str:
    return str(v).replace("\t", "").replace("　", " ").strip() if v is not None else ""


def read_table(path: Path) -> list[list[str]]:
    """All rows of a CSV (UTF-8 or GBK) or Excel sheet, cells stripped of the exporters' tabs."""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            return [
                [_clean(c) for c in row] for row in wb.worksheets[0].iter_rows(values_only=True)
            ]
        finally:
            wb.close()
    if suffix == ".xls":
        import xlrd

        sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        return [[_clean(c) for c in sheet.row_values(i)] for i in range(sheet.nrows)]
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_(f"{path.name}: 无法识别文件编码")
    return [[_clean(c) for c in row] for row in csv.reader(io.StringIO(content))]


def _find_header(rows: list[list[str]], required: tuple[str, ...]) -> int | None:
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if all(any(cell == r for cell in row) for r in required):
            return i
    return None


def _date(s: str) -> dt.date:
    s = s.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ImportError_(f"无法识别的日期：{s!r}")


def _amount(s: str) -> Decimal:
    try:
        return Decimal(re.sub(r"[¥￥,\s元]", "", s))
    except InvalidOperation as exc:
        raise ImportError_(f"无法识别的金额：{s!r}") from exc


_LOGIN = re.compile(
    r"(?:支付宝账户|支付宝账号|账户名|账号|账户|微信号|微信昵称|昵称)[:：]\s*\[?([^\]\s,，]+)"
)
_HOLDER = re.compile(r"(?:姓名|户名|用户名)[:：]\s*\[?([^\]\s,，]+)")


def _login_tag(login: str) -> str:
    """'138****1234' -> '1234'; 'li.ming@example.com' -> 'li.ming'; anything else, cleaned."""
    digits = re.findall(r"\d", login)
    if len(digits) >= 4 and re.fullmatch(r"[\d*+\-\s]+", login):
        return "".join(digits[-4:])
    if "@" in login:
        login = login.split("@", 1)[0]
    return re.sub(r"[:\s]", "", login)[:24] or "Wallet"


def _identity(preamble: list[list[str]]) -> tuple[str | None, str | None]:
    """(holder name, login tag) from the lines above the header, when the export states them."""
    blob = "\n".join(" ".join(c for c in row if c) for row in preamble)
    holder = m.group(1) if (m := _HOLDER.search(blob)) else None
    login = m.group(1) if (m := _LOGIN.search(blob)) else None
    return holder, (_login_tag(login) if login else None)


class _Wallet:
    name: str
    institution: str
    account: str
    required: tuple[str, ...]
    marker: str  # text in the preamble or file name

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() in (".xlsx", ".xls"):
            try:
                rows = read_table(path)
            except Exception:
                return False
            return _find_header(rows, self.required) is not None
        return all(col in head for col in self.required) and (
            self.marker in head or self.marker in path.name
        )

    def _rows(self, path: Path) -> tuple[list[str], list[list[str]]]:
        rows = read_table(path)
        h = _find_header(rows, self.required)
        if h is None:
            raise ImportError_(f"{path.name}: 找不到{self.institution}账单的表头")
        self._holder, self._tag = _identity(rows[:h])
        return rows[h], [r for r in rows[h + 1 :] if any(r)]

    _holder: str | None = None
    _tag: str | None = None

    def _statement(self, rows: list[RawTxn]) -> ParsedStatement:
        account = f"Assets:{self.institution}:{self._tag}" if self._tag else self.account
        return ParsedStatement(
            importer=self.name,
            institution=self.institution,
            currency="CNY",
            rows=rows,
            account_type="Assets",
            asset_class="cash",
            default_account=account,
            flow_only=True,
            holder=self._holder,
        )


class AlipayImporter(_Wallet):
    """支付宝「交易流水证明 · 用于个人对账」CSV (GBK). Also accepts the older web export columns."""

    name = "alipay"
    institution = "Alipay"
    account = "Assets:Alipay:Wallet"
    required = ("收/支", "交易对方", "交易状态")  # WeChat says 当前状态
    marker = "支付宝"

    def parse(self, path: Path) -> ParsedStatement:
        header, body = self._rows(path)
        col = {name: i for i, name in enumerate(header)}

        def get(row: list[str], *names: str) -> str:
            for n in names:
                if n in col and col[n] < len(row):
                    return row[col[n]]
            return ""

        out: list[RawTxn] = []
        purchases: dict[str, Decimal] = {}
        refunds: dict[str, dict[str, RawTxn]] = {}
        for row in body:
            when = get(row, "交易时间", "交易创建时间", "付款时间")
            if not re.match(r"\d{4}[-/]", when):
                continue  # footer lines
            direction = get(row, "收/支")
            status = get(row, "交易状态")
            if "关闭" in status or "失败" in status:
                continue
            description = get(row, "商品说明", "商品名称") or get(row, "交易对方")
            hint = get(row, "交易分类")
            ref = get(row, "交易订单号", "交易号")
            category = None
            original, separator, _ = ref.partition("_")
            if direction == "不计收支":
                if status == "退款成功" and separator and original:
                    category = "Expenses:Refunds"
                elif (
                    status == "交易成功"
                    and hint == "投资理财"
                    and re.fullmatch(r"余额宝(?:-\d{4}\.\d{2}\.\d{2})?-收益发放", description)
                ):
                    category = "Income:Interest"
                else:
                    continue
            elif direction not in ("收入", "支出"):
                continue

            amount = _amount(get(row, "金额", "金额（元）", "金额(元)"))
            if category and amount <= 0:
                continue
            method = get(row, "收/付款方式")
            note = get(row, "备注")
            txn = RawTxn(
                date=_date(when),
                amount=-amount if direction == "支出" else amount,
                description=description,
                payee=get(row, "交易对方") or None,
                ref=ref or None,
                category_hint=hint or None,
                memo=" · ".join(x for x in (method, note) if x) or None,
                category=category,
            )
            if category == "Expenses:Refunds":
                refunds.setdefault(original, {})[ref] = txn
            else:
                out.append(txn)
            if ref and direction == "支出" and status in ("交易成功", "支付成功"):
                purchases[ref] = amount

        # Refund order IDs extend the original order ID. Keep each refund as its own row so
        # overlapping exports dedup without changing the original purchase's source key.
        for original, by_ref in refunds.items():
            if sum(txn.amount for txn in by_ref.values()) <= purchases.get(original, 0):
                out.extend(by_ref.values())
        return self._statement(out)


class WeChatImporter(_Wallet):
    """微信支付账单明细 (CSV in UTF-8, or the newer .xlsx)."""

    name = "wechat"
    institution = "WeChat"
    account = "Assets:WeChat:Wallet"
    required = ("收/支", "交易类型", "当前状态")
    marker = "微信"

    def parse(self, path: Path) -> ParsedStatement:
        header, body = self._rows(path)
        col = {name: i for i, name in enumerate(header)}

        def get(row: list[str], *names: str) -> str:
            for n in names:
                if n in col and col[n] < len(row):
                    return row[col[n]]
            return ""

        out: list[RawTxn] = []
        for row in body:
            when = get(row, "交易时间")
            if not re.match(r"\d{4}[-/]", when):
                continue
            direction = get(row, "收/支")
            status = get(row, "当前状态")
            kind = get(row, "交易类型")
            if direction not in ("收入", "支出") or "失败" in status or "关闭" in status:
                continue
            # Refunds are folded into the payment they undo (its status says how much came back),
            # so they never show up as income: 已全额退款 drops it, 已退款(¥x) reduces it.
            if "退款" in kind or status == "已全额退款":
                continue
            amount = _amount(get(row, "金额(元)", "金额（元）", "金额"))
            if direction == "支出" and (m := re.search(r"已退款[(（]?[¥￥]?([\d.,]+)", status)):
                amount -= _amount(m.group(1))
                if amount <= 0:
                    continue
            goods = get(row, "商品")
            out.append(
                RawTxn(
                    date=_date(when),
                    amount=amount if direction == "收入" else -amount,
                    description=goods if goods and goods != "/" else kind,
                    payee=get(row, "交易对方") or None,
                    ref=get(row, "交易单号") or None,
                    category_hint=kind or None,
                    memo=" · ".join(
                        x for x in (get(row, "支付方式"), get(row, "备注")) if x and x != "/"
                    )
                    or None,
                )
            )
        return self._statement(out)
