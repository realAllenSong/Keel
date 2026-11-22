"""Synthetic 支付宝 / 微信支付 exports in their real layouts (fake people, fake merchants).

    uv run python ../fixtures/cn/generate.py   # from backend/, rewrites fixtures/cn/*

Layout notes kept with the generator so the parsers' assumptions are visible in one place:
- 支付宝「交易流水证明 · 用于个人对账」: GBK CSV, ~24 preamble lines, cells padded with tabs,
  收/支 ∈ {收入, 支出, 不计收支}, 交易状态 includes 交易关闭 for payments that never happened.
- 微信支付账单明细: UTF-8-BOM CSV (newer exports: .xlsx, same rows), 16 preamble lines,
  金额 like "¥12.50", 收/支 "/" for moving your own money.
- Both arrive as AES-encrypted zips (password sent in-app); member names are GBK without the
  UTF-8 flag, as Windows-made zips are.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pyzipper
from openpyxl import Workbook

OUT = Path(__file__).parent
PASSWORD = b"246810"

ALIPAY_HEADER = [
    "交易时间",
    "交易分类",
    "交易对方",
    "对方账号",
    "商品说明",
    "收/支",
    "金额",
    "收/付款方式",
    "交易状态",
    "交易订单号",
    "商家订单号",
    "备注",
]
ALIPAY_ROWS = [
    [
        "2026-09-28 19:02:11",
        "餐饮美食",
        "示例面馆",
        "/",
        "牛肉面",
        "支出",
        "32.00",
        "花呗",
        "交易成功",
        "AP0001",
        "M0001",
        "",
    ],
    [
        "2026-09-25 08:15:40",
        "交通出行",
        "示例地铁",
        "/",
        "地铁出行",
        "支出",
        "4.00",
        "余额",
        "交易成功",
        "AP0002",
        "M0002",
        "",
    ],
    [
        "2026-09-21 14:30:00",
        "日用百货",
        "示例超市",
        "/",
        "日用品",
        "支出",
        "128.50",
        "示例银行储蓄卡(0000)",
        "交易成功",
        "AP0003",
        "M0003",
        "",
    ],
    [
        "2026-09-20 10:00:00",
        "数码电器",
        "示例数码店",
        "/",
        "耳机",
        "支出",
        "399.00",
        "花呗",
        "交易关闭",
        "AP0004",
        "M0004",
        "未付款",
    ],
    [
        "2026-09-15 09:00:00",
        "投资理财",
        "余额宝",
        "/",
        "余额宝-转入",
        "不计收支",
        "1000.00",
        "示例银行储蓄卡(0000)",
        "交易成功",
        "AP0005",
        "",
        "",
    ],
    [
        "2026-09-10 12:00:00",
        "转账红包",
        "示例好友",
        "/",
        "转账",
        "收入",
        "200.00",
        "余额",
        "交易成功",
        "AP0006",
        "",
        "还钱",
    ],
    [
        "2026-09-03 18:45:12",
        "餐饮美食",
        "示例咖啡",
        "/",
        "拿铁",
        "支出",
        "28.00",
        "余额",
        "交易成功",
        "AP0007",
        "M0007",
        "",
    ],
]


def alipay_csv() -> bytes:
    lines = [
        "------------------------------------------------------------------------------------",
        "导出信息：",
        "姓名：示例用户",
        "支付宝账户：demo@example.com",
        "起始时间：[2026-09-01 00:00:00]    终止时间：[2026-09-30 23:59:59]",
        "导出交易类型：[全部]",
        "导出时间：[2026-10-02 10:00:00]",
        "共7笔记录",
        "收入：1笔 200.00元",
        "支出：5笔 591.50元",
        "不计收支：1笔 1000.00元",
        "",
        "特别提示：",
        "1.本回单内容可表明支付宝受理了相关交易申请，但不代表交易已经成功或相关款项已到账；",
        "2.支付宝保留随时更新本回单内容的权利；",
        "3.如有疑问，请拨打支付宝客服热线；",
        "4.本回单仅供参考。",
        "",
        "",
        "",
        "",
        "",
        "",
        "------------------------支付宝（中国）网络技术有限公司  电子客户回单------------------------",
    ]
    buf = io.StringIO()
    buf.write("\n".join(lines) + "\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([*ALIPAY_HEADER, ""])
    for row in ALIPAY_ROWS:
        w.writerow([f"{c}\t" if i in (9, 10) else c for i, c in enumerate(row)] + [""])
    return buf.getvalue().encode("gb18030")


WECHAT_HEADER = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]
WECHAT_ROWS = [
    [
        "2026-09-27 12:20:00",
        "商户消费",
        "示例便利店",
        "饮料",
        "支出",
        "¥6.50",
        "零钱",
        "支付成功",
        "WX0001",
        "S0001",
        "/",
    ],
    [
        "2026-09-24 20:10:00",
        "扫二维码付款",
        "示例水果店",
        "/",
        "支出",
        "¥45.00",
        "示例银行(0000)",
        "已转账",
        "WX0002",
        "/",
        "/",
    ],
    [
        "2026-09-23 13:00:00",
        "商户消费",
        "示例网店",
        "T恤",
        "支出",
        "¥120.00",
        "零钱",
        "已退款(￥20.00)",
        "WX0007",
        "S0007",
        "/",
    ],
    [
        "2026-09-25 09:00:00",
        "商户消费-退款",
        "示例网店",
        "退款",
        "收入",
        "¥20.00",
        "零钱",
        "已退款",
        "WX0008",
        "S0007",
        "/",
    ],
    [
        "2026-09-22 09:00:00",
        "微信红包",
        "示例好友",
        "/",
        "收入",
        "¥88.00",
        "/",
        "已存入零钱",
        "WX0003",
        "/",
        "/",
    ],
    [
        "2026-09-18 15:00:00",
        "零钱提现",
        "示例银行(0000)",
        "/",
        "/",
        "¥100.00",
        "零钱",
        "提现已到账",
        "WX0004",
        "/",
        "/",
    ],
    [
        "2026-09-12 19:30:00",
        "商户消费",
        "示例餐厅",
        "晚餐",
        "支出",
        "¥156.00",
        "零钱",
        "已全额退款",
        "WX0005",
        "S0005",
        "/",
    ],
    [
        "2026-09-13 10:00:00",
        "商户消费-退款",
        "示例餐厅",
        "退款",
        "收入",
        "¥156.00",
        "零钱",
        "已退款",
        "WX0006",
        "S0005",
        "/",
    ],
]
WECHAT_PREAMBLE = [
    ["微信支付账单明细"],
    ["微信昵称：[示例用户]"],
    ["起始时间：[2026-09-01 00:00:00] 终止时间：[2026-09-30 23:59:59]"],
    ["导出类型：[全部]"],
    ["导出时间：[2026-10-02 10:00:00]"],
    [],
    ["共8笔记录"],
    ["收入：3笔 264.00元"],
    ["支出：4笔 327.50元"],
    ["中性交易：1笔 100.00元"],
    ["注："],
    ["1. 充值/提现/理财通购买/零钱通存取/信用卡还款等交易，将计入中性交易"],
    ["2. 本明细仅展示当前账单中的交易，不包括已删除的记录"],
    ["3. 本明细仅供个人对账使用"],
    [],
    ["----------------------微信支付账单明细列表--------------------"],
]


def wechat_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerows([*WECHAT_PREAMBLE, WECHAT_HEADER, *WECHAT_ROWS])
    return buf.getvalue().encode("utf-8-sig")


def wechat_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in [*WECHAT_PREAMBLE, WECHAT_HEADER, *WECHAT_ROWS]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _GbkName(pyzipper.zipfile_aes.AESZipInfo):
    """Store the member name as raw GBK bytes without the UTF-8 flag."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp437"), self.flag_bits


def locked_zip(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(PASSWORD)
        info = _GbkName(member.encode("gb18030").decode("cp437"), date_time=(2026, 10, 2, 10, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, data)
    return buf.getvalue()


def main() -> None:
    files = {
        "alipay_record_202609.csv": alipay_csv(),
        "wechat_bill_202609.csv": wechat_csv(),
        "wechat_bill_202609.xlsx": wechat_xlsx(),
        "alipay_record_202609.zip": locked_zip(
            "支付宝交易明细(20260901-20260930).csv", alipay_csv()
        ),
        "wechat_bill_202609.zip": locked_zip("微信支付账单(20260901-20260930).xlsx", wechat_xlsx()),
    }
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    print(f"wrote {len(files)} files to {OUT} (zip password: {PASSWORD.decode()})")


if __name__ == "__main__":
    main()
