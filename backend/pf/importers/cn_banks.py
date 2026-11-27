"""Chinese bank statements: 招商银行 and 中国银行 PDFs, and 宁波银行 Excel / CSV.

中国银行 emails its 交易流水明细清单 as a password-protected PDF (see BankOfChinaImporter);
pf.importers.archive parks it until the code is known and hands over a plain copy.

宁波银行 (Bank of Ningbo) 交易明细, exported from 网银 / 手机银行 as Excel or CSV.

The export opens with a few preamble lines (户名, 账号, 币种, 起止日期) before the header row.
Banks in China lay out the amount in one of two ways, and both are accepted:
  separate columns  收入金额 / 支出金额 (also 存入/支取, 贷方/借方发生额)
  one column        交易金额 (signed, or unsigned with a 借贷标志 / 收支 column: 借/支 = out)
A running 余额 column becomes the closing balance assertion when the rows chain consistently.
The layout is unverified until checked against a real export; fixtures/cn mirrors it. PDF
statements are not supported.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pf.importers.base import ImportError_, ParsedStatement, RawTxn
from pf.importers.cn_wallets import read_table
from pf.importers.generic_csv import _closing_assertion
from pf.money import MoneyError, parse_decimal

DATE = ("交易日期", "记账日期", "交易时间", "日期")
IN = ("收入金额", "存入金额", "贷方发生额", "贷方金额", "收入")
OUT = ("支出金额", "支取金额", "借方发生额", "借方金额", "支出")
SIGNED = ("交易金额", "发生额", "金额")
FLAG = ("借贷标志", "借贷方向", "收支", "收/支")
BALANCE = ("账户余额", "余额", "联机余额", "可用余额")
SUMMARY = ("摘要", "交易摘要", "交易类型", "业务摘要")
NOTE = ("附言", "备注", "用途", "交易附言")
PAYEE = ("对方户名", "对方名称", "对方账户名称", "交易对方")
REF = ("交易流水号", "流水号", "凭证号")
MARKERS = ("宁波银行", "Bank of Ningbo", "NBCB", "nbcb")
CMB_TITLE = "Transaction Statement of China Merchants Bank"
CMB_INSTITUTION = "China Merchants Bank"
CMB_ROW = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+([A-Z]{3})\s+([+\d,.-]+)\s+([+\d,.-]+)\s+(\S+)(?:\s+(.*))?$"
)
CMB_PERIOD = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*--\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)


def _cmb_statement(pages: list[str], filename: str) -> ParsedStatement:
    account = re.search(r"账号\s*[:：]\s*([\d*]+)", pages[0])
    if CMB_TITLE not in pages[0] or not account or not re.search(r"\d{4}$", account[1]):
        raise ImportError_(f"{filename}: 招行流水缺少可识别的账号")
    tail = account[1][-4:]
    rows: list[RawTxn] = []
    currencies: set[str] = set()
    for page in pages:
        for line in page.splitlines():
            line = line.strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}\s+[A-Z]{3}\b", line):
                continue
            match = CMB_ROW.fullmatch(line)
            if not match:
                raise ImportError_(f"{filename}: 招行流水有无法完整读取的交易行")
            day, currency, amount, balance, summary, counterparty = match.groups()
            currencies.add(currency)
            # Counterparty account numbers are not transaction IDs. The printed running
            # balance distinguishes identical purchases posted on the same day.
            payee = re.sub(r"\s+\d{4,}$", "", counterparty or "").strip() or None
            ending = parse_decimal(balance)
            rows.append(
                RawTxn(
                    date=dt.date.fromisoformat(day),
                    amount=parse_decimal(amount),
                    description=" ".join(part for part in (summary, payee) if part),
                    payee=payee,
                    ref=str(ending.normalize()),
                    balance_after=ending,
                    category="Income:Interest" if summary == "账户结息" else None,
                )
            )
    if len(currencies) != 1 or not (assertions := _closing_assertion(rows)):
        raise ImportError_(f"{filename}: 招行流水需要单一币种且余额逐笔对得上")
    closing_day, closing_balance = assertions[0]
    period = CMB_PERIOD.search(pages[0])
    reported_on = dt.date.fromisoformat(period[1]) if period else closing_day
    if reported_on < closing_day:
        raise ImportError_(f"{filename}: 招行流水的截止日期早于最后一笔交易")
    return ParsedStatement(
        importer="cmb",
        institution=CMB_INSTITUTION,
        currency=currencies.pop(),
        rows=rows,
        assertions=assertions,
        valuations=[(reported_on, closing_balance)],
        account_hint=tail,
        default_account=f"Assets:{CMB_INSTITUTION}:Checking-{tail}",
    )


class ChinaMerchantsBankImporter:
    name = "cmb"

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        try:
            pdf = PdfReader(path)
            return (
                not pdf.is_encrypted
                and len(pdf.pages) > 0
                and CMB_TITLE in pdf.pages[0].extract_text()
            )
        except (PdfReadError, OSError, ValueError):
            return False

    def parse(self, path: Path) -> ParsedStatement:
        try:
            pdf = PdfReader(path)
            pages = [page.extract_text() for page in pdf.pages]
        except (PdfReadError, OSError, ValueError) as exc:
            raise ImportError_(f"{path.name}: 无法读取招行流水 PDF") from exc
        if not pages:
            raise ImportError_(f"{path.name}: 招行流水 PDF 没有页面")
        return _cmb_statement(pages, path.name)


BOC_TITLE = "中国银行交易流水明细清单"
BOC_INSTITUTION = "Bank of China"
BOC_EMPTY = "------"
BOC_CURRENCIES = {"人民币": "CNY", "人民币元": "CNY", "CNY": "CNY", "美元": "USD", "USD": "USD"}


def _cell(value: object) -> str:
    """A table cell as one line: the PDF wraps long names inside the cell."""
    text_ = re.sub(r"\s+", "", str(value or ""))
    return "" if text_ == BOC_EMPTY or set(text_) <= {"-"} else text_


def _boc_statement(path: Path) -> ParsedStatement:
    """中国银行「交易流水明细清单」: the debit-card statement the app's 交易流水打印 emails.

    Columns are found by name, so a reordered or narrower export still reads: 记账日期, 金额
    (signed, spending negative) and 余额 are required; 币别, 交易名称, 附言, 对方账户名 and 记账时间
    are used when present. The running balance must chain or nothing is asserted."""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
            tables = [t for page in pdf.pages for t in page.extract_tables()]
    except Exception as exc:  # pdfminer raises many types on damaged files
        raise ImportError_(f"{path.name}: 无法读取中国银行流水 PDF") from exc
    if BOC_TITLE not in first:
        raise ImportError_(f"{path.name}: 不是中国银行交易流水明细清单")
    cols: dict[str, int] = {}
    rows: list[RawTxn] = []
    currencies: set[str] = set()
    for table in tables:
        for raw in table:
            cells = [_cell(c) for c in raw]
            if "记账日期" in cells:  # the header repeats on every page
                cols = {name: i for i, name in enumerate(cells) if name}
                continue
            if not cols or "金额" not in cols or "余额" not in cols:
                continue

            def get(name: str, cells: list[str] = cells, cols: dict[str, int] = cols) -> str:
                i = cols.get(name)
                return cells[i] if i is not None and i < len(cells) else ""

            day = _date(get("记账日期"))
            amount, balance = _num(get("金额")), _num(get("余额"))
            if day is None or amount is None or amount == 0:
                continue
            label = get("币别") or "人民币"
            currencies.add(BOC_CURRENCIES.get(label, label))
            kind, note, payee = get("交易名称"), get("附言"), get("对方账户名")
            rows.append(
                RawTxn(
                    date=day,
                    amount=amount,
                    description=" ".join(x for x in (kind, payee, note) if x) or "中国银行",
                    payee=payee or None,
                    # No transaction id on the statement: the time and running balance tell two
                    # identical purchases on the same day apart.
                    ref=" ".join(x for x in (get("记账时间"), str(balance or "")) if x) or None,
                    balance_after=balance,
                    memo=note or None,
                    category="Income:Interest" if kind in ("结息", "利息") else None,
                )
            )
    if not cols:
        raise ImportError_(f"{path.name}: 找不到中国银行流水的表头（记账日期、金额、余额）")
    if not rows:
        raise ImportError_(f"{path.name}: 中国银行流水里没有交易")
    if len(currencies) != 1 or not currencies <= {"CNY", "USD"}:
        raise ImportError_(
            f"{path.name}: 中国银行流水需要单一币种（人民币或美元），请按币种分别导出"
        )
    card = re.search(r"(?:卡号|账号)\s*[:：]\s*([\d* ]{8,})", first)
    digits = re.sub(r"\D", "", card[1]) if card else ""
    tail = digits[-4:] if len(digits) >= 4 else None
    holder = re.search(r"客户姓名\s*[:：]\s*(\S+)", first)
    period = re.search(r"交易区间\s*[:：]\s*\S+\s*至\s*(\d{4}-\d{2}-\d{2})", first)
    assertions = _closing_assertion(rows)
    valuations = assertions
    if assertions and period:
        reported_on = dt.date.fromisoformat(period[1])
        if reported_on >= assertions[0][0]:
            valuations = [(reported_on, assertions[0][1])]
    return ParsedStatement(
        importer="boc",
        institution=BOC_INSTITUTION,
        currency=currencies.pop(),
        rows=rows,
        assertions=assertions,
        valuations=valuations,
        account_hint=tail,
        holder=holder[1] if holder else None,
        default_account=f"Assets:{BOC_INSTITUTION}:Checking-{tail}" if tail else None,
    )


class BankOfChinaImporter:
    name = "boc"

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() != ".pdf":
            return False
        try:
            pdf = PdfReader(path)
            return (
                not pdf.is_encrypted
                and len(pdf.pages) > 0
                and BOC_TITLE in (pdf.pages[0].extract_text() or "")
            )
        except (PdfReadError, OSError, ValueError):
            return False

    def parse(self, path: Path) -> ParsedStatement:
        return _boc_statement(path)


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    for n in names:
        if n in header:
            return header.index(n)
    return None


def _header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:40]):
        has_amount = (_find(row, IN) is not None and _find(row, OUT) is not None) or _find(
            row, SIGNED
        ) is not None
        if _find(row, DATE) is not None and has_amount and _find(row, BALANCE) is not None:
            return i
    return None


def _date(s: str) -> dt.date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s[: len(dt.date(2000, 1, 1).strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def _num(s: str) -> Decimal | None:
    s = s.strip().replace("元", "")
    if s in ("", "-", "--", "/"):
        return None
    try:
        return parse_decimal(s)
    except MoneyError:
        return None


class NingboBankImporter:
    name = "ningbo"
    institution = "Bank of Ningbo"

    def sniff(self, path: Path, head: str) -> bool:
        if path.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            return False
        if path.suffix.lower() == ".csv" and not any(m in head or m in path.name for m in MARKERS):
            return False  # cheap exit before reading the whole file
        try:
            rows = read_table(path)
        except Exception:
            return False
        text = "\n".join(",".join(r) for r in rows[:40])
        if not any(m in text or m in path.name for m in MARKERS):
            return False
        return _header(rows) is not None

    def parse(self, path: Path) -> ParsedStatement:
        rows = read_table(path)
        h = _header(rows)
        if h is None:
            raise ImportError_(f"{path.name}: 找不到宁波银行交易明细的表头")
        header = rows[h]
        c = {
            "date": _find(header, DATE),
            "in": _find(header, IN),
            "out": _find(header, OUT),
            "signed": _find(header, SIGNED),
            "flag": _find(header, FLAG),
            "balance": _find(header, BALANCE),
            "summary": _find(header, SUMMARY),
            "note": _find(header, NOTE),
            "payee": _find(header, PAYEE),
            "ref": _find(header, REF),
        }

        def get(row: list[str], key: str) -> str:
            i = c[key]
            return row[i] if i is not None and i < len(row) else ""

        out: list[RawTxn] = []
        for row in rows[h + 1 :]:
            date = _date(get(row, "date"))
            if date is None:
                continue  # 合计 / footer lines
            if c["in"] is not None and c["out"] is not None:
                amount = (_num(get(row, "in")) or Decimal(0)) - abs(
                    _num(get(row, "out")) or Decimal(0)
                )
            else:
                raw = _num(get(row, "signed"))
                if raw is None:
                    continue
                flag = get(row, "flag")
                amount = (
                    -abs(raw)
                    if flag.startswith(("借", "支", "出"))
                    else (abs(raw) if flag.startswith(("贷", "收", "入")) else raw)
                )
            if amount == 0:
                continue
            summary, note = get(row, "summary"), get(row, "note")
            payee = get(row, "payee")
            out.append(
                RawTxn(
                    date=date,
                    amount=amount,
                    description=" ".join(x for x in (summary, payee, note) if x) or "宁波银行",
                    payee=payee or None,
                    ref=get(row, "ref") or None,
                    balance_after=_num(get(row, "balance")),
                    memo=note or None,
                )
            )
        if not out:
            raise ImportError_(f"{path.name}: 没有可解析的交易行")
        preamble = " ".join(" ".join(r) for r in rows[:h])
        m = re.search(r"(?:账号|卡号)[:：]?\s*[\d* ]*(\d{4})(?!\d)", preamble)
        currency = "USD" if re.search(r"币种[:：]\s*(美元|USD)", preamble) else "CNY"
        assertions = _closing_assertion(out)
        return ParsedStatement(
            importer=self.name,
            institution=self.institution,
            currency=currency,
            rows=out,
            account_type="Assets",
            asset_class="cash",
            assertions=assertions,
            valuations=assertions,
            account_hint=m.group(1) if m else None,
        )
