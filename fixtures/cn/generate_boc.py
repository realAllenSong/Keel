"""Synthetic 中国银行「交易流水明细清单」PDF in the layout the app's 交易流水打印 emails.

    cd backend && uv run --with reportlab python ../fixtures/cn/generate_boc.py

Layout notes (from the open-source china_bean_importers BOC parser, MIT; unverified against a
real export until one is checked):
- Title 中国银行交易流水明细清单, then 客户姓名, the 19-digit debit card number and
  交易区间：YYYY-MM-DD 至 YYYY-MM-DD above a fully ruled table.
- Twelve columns: 记账日期 记账时间 币别 金额 余额 交易名称 渠道 网点名称 附言 对方账户名
  对方卡号/账号 对方开户行. 金额 is signed (spending negative); empty cells read "------".
- The PDF is password-protected; the password is shown in the app's 申请记录.
The fake card number below deliberately fails the Luhn check, so it is not a real card.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).parent
PASSWORD = "135790"
CARD = "6217850000000001230"  # fails Luhn on purpose: not a real card
NONE = "------"

HEADER = [
    "记账日期",
    "记账时间",
    "币别",
    "金额",
    "余额",
    "交易名称",
    "渠道",
    "网点名称",
    "附言",
    "对方账户名",
    "对方卡号/账号",
    "对方开户行",
]

# date, time, amount, txn name, channel, note, payee, payee account, payee bank
LINES = [
    ("2026-09-01", "09:12:03", "8600.00", "代发工资", "其他", "工资", "示例科技有限公司", "------", "------"),
    ("2026-09-02", "12:40:51", "-36.50", "网上快捷支付", "网上支付", "美团订单", "美团支付", NONE, NONE),
    ("2026-09-03", "18:05:22", "-128.00", "网上快捷支付", "网上支付", "盒马鲜生", "支付宝（中国）网络技术有限公司", NONE, NONE),
    ("2026-09-05", "08:30:00", "-4200.00", "转账汇款", "手机银行", "房租 九月", "林小雨", "6222020000000004567", "中国工商银行股份有限公司上海市分行营业部"),
    ("2026-09-08", "21:14:09", "-59.90", "网上快捷支付", "网上支付", "爱奇艺会员", "财付通支付科技有限公司", NONE, NONE),
    ("2026-09-12", "10:02:44", "-2000.00", "转账汇款", "手机银行", "转入证券账户", "示例用户", "1234567890", "长江证券股份有限公司"),
    ("2026-09-15", "14:55:31", "-86.20", "银联消费", "POS", NONE, "全家便利店", NONE, NONE),
    ("2026-09-21", "02:00:00", "3.52", "结息", "其他", "利息", NONE, NONE, NONE),
    ("2026-09-25", "19:47:16", "-312.00", "网上快捷支付", "网上支付", "京东商城 家用电器", "网银在线（北京）科技有限公司", NONE, NONE),
    ("2026-09-28", "11:20:05", "45.00", "网上快捷支付退款", "网上支付", "美团订单退款", "美团支付", NONE, NONE),
]
OPENING = "5230.15"


def _rows() -> list[list[str]]:
    from decimal import Decimal

    balance = Decimal(OPENING)
    out = []
    for day, time, amount, name, channel, note, payee, acct, bank in LINES:
        balance += Decimal(amount)
        out.append(
            [day, time, "人民币", amount, f"{balance:.2f}", name, channel, NONE, note, payee, acct, bank]
        )
    return out


def main() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    cell = ParagraphStyle("cell", fontName="STSong-Light", fontSize=6.5, leading=8)
    title = ParagraphStyle("title", fontName="STSong-Light", fontSize=14, leading=18, alignment=1)
    meta = ParagraphStyle("meta", fontName="STSong-Light", fontSize=8, leading=11)
    data = [[Paragraph(h, cell) for h in HEADER]]
    data += [[Paragraph(v, cell) for v in r] for r in _rows()]
    widths = [52, 40, 30, 46, 50, 62, 42, 50, 78, 88, 82, 112]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
            ]
        )
    )
    plain = OUT / "boc_debit_plain.pdf"
    doc = SimpleDocTemplate(str(plain), pagesize=landscape(A4), leftMargin=24, rightMargin=24)
    doc.build(
        [
            Paragraph("中国银行交易流水明细清单", title),
            Spacer(1, 6),
            Paragraph("客户姓名：示例用户", meta),
            Paragraph(f"借记卡号：{CARD}", meta),
            Paragraph("交易区间：2026-09-01 至 2026-09-30", meta),
            Spacer(1, 8),
            table,
        ]
    )
    writer = PdfWriter(clone_from=PdfReader(plain))
    writer.encrypt(user_password=PASSWORD, algorithm="AES-128")
    with (OUT / "boc_debit_202609.pdf").open("wb") as f:
        writer.write(f)
    plain.unlink()


if __name__ == "__main__":
    main()
