import Foundation

public enum Format {
    static func grouping(_ digits: Int) -> NumberFormatter {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.minimumFractionDigits = digits
        f.maximumFractionDigits = digits
        return f
    }

    /// "$1,234" / "−$56.78" / "+$4,211". `digits` nil = 0 for |v| ≥ 100, else 2.
    public static func money(
        _ value: Decimal, currency: String = "USD", digits: Int? = nil, sign: Bool = false
    ) -> String {
        let magnitude = abs(value)
        let d = digits ?? (magnitude >= 100 ? 0 : 2)
        let body = grouping(d).string(from: NSDecimalNumber(decimal: magnitude)) ?? "\(magnitude)"
        let symbol = ["USD": "$", "CNY": "¥", "HKD": "HK$", "EUR": "€", "JPY": "¥"][currency] ?? "\(currency) "
        let prefix = value < 0 ? "−" : (sign && value > 0 ? "+" : "")
        return prefix + symbol + body
    }

    public static func money(_ m: Money, currency: String = "USD", digits: Int? = nil, sign: Bool = false) -> String {
        money(m.value, currency: currency, digits: digits, sign: sign)
    }

    public static func percent(_ v: Double, digits: Int = 0) -> String {
        String(format: "%.\(digits)f%%", v * 100)
    }

    static let monthsShort = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    static let monthsFull = [
        "January", "February", "March", "April", "May", "June", "July", "August", "September",
        "October", "November", "December",
    ]

    /// "2026-09" -> "2026 年 9 月" / "Sep 2026"; short: "9月" / "Sep"; full (English only): "September".
    public static func month(_ ym: String, lang: Lang = L10n.current, short: Bool = false, full: Bool = false) -> String {
        let parts = ym.split(separator: "-")
        guard parts.count >= 2, let m = Int(parts[1]), (1...12).contains(m) else { return ym }
        let y = String(parts[0])
        switch lang {
        case .zh: return short || full ? "\(m)月" : "\(y) 年 \(m) 月"  // l10n-allow
        case .en: return full ? monthsFull[m - 1] : short ? monthsShort[m - 1] : "\(monthsShort[m - 1]) \(y)"
        }
    }

    /// "2026-10-18" -> "10月18日" / "Oct 18"
    public static func day(_ iso: String, lang: Lang = L10n.current) -> String {
        let parts = iso.split(separator: "-")
        guard parts.count == 3, let m = Int(parts[1]), let d = Int(parts[2]), (1...12).contains(m) else { return iso }
        return lang == .zh ? "\(m)月\(d)日" : "\(monthsShort[m - 1]) \(d)"  // l10n-allow
    }

    // Ledger account segments stay English internally; people read them in their language.
    static let zhSegments: [String: String] = [
        "Uncategorized": "用途待确认", "Expenses": "支出", "Income": "收入", "Assets": "资产", "Liabilities": "欠款",
        "Housing": "住房", "Rent": "房租", "Utilities": "水电燃气", "Phone & Internet": "话费网费", "Home": "家居",
        "Food": "餐饮", "Groceries": "买菜", "Dining": "外出就餐", "Coffee": "咖啡",
        "Transport": "交通", "Fuel": "油费", "Rideshare": "打车", "Public": "公共交通", "Car": "汽车",
        "Travel": "旅行", "Flights": "机票", "Lodging": "住宿",
        "Shopping": "购物", "Online": "网购", "General": "日常购物",
        "Subscriptions": "订阅", "Streaming": "影音会员", "Software": "软件服务",
        "Health": "医疗健康", "Insurance": "保险", "Taxes": "税", "Gifts": "礼物与捐赠", "Personal": "个人",
        "Entertainment": "娱乐", "Education": "教育", "Fees": "手续费", "Bank": "银行", "Cash": "取现",
        "Salary": "工资", "Bonus": "奖金", "Interest": "利息", "Dividends": "股息", "Refunds": "退款", "Other": "其他",
        "Family Support": "家庭资助", "Reimbursements": "朋友还款/报销",
        "Investment": "投资", "Unrealized": "市值变化",
        "Checking": "支票账户", "Savings": "储蓄账户", "Brokerage": "券商账户", "Retirement": "退休账户",
        "Crypto": "加密资产", "Opening-Balances": "期初余额",
        "Alipay": "支付宝", "WeChat": "微信", "Wallet": "钱包",
        "China Merchants Bank": "招商银行", "Bank of Ningbo": "宁波银行",
        "ReportedTotal": "未拆分资产", "YuEBao": "余额宝", "Balance": "余额",
        "Card": "信用卡", "Loan": "贷款", "Exchange": "交易所",
        "Elsewhere": "我的其他账户（未导入）",
    ]
    static let enSegments = ["Opening-Balances": "Opening balances", "Unrealized": "Market value change", "Uncategorized": "Needs categorization", "ReportedTotal": "Assets without account breakdown", "YuEBao": "Yu’e Bao", "Elsewhere": "My other accounts (not in Keel)"]

    public static func segment(_ s: String, lang: Lang = L10n.current) -> String {
        // "Card-0002" -> "信用卡 0002" / "Card 0002": the kind in the reader's language, then the digits.
        if let dash = s.lastIndex(of: "-"), dash != s.startIndex {
            let digits = s[s.index(after: dash)...]
            if (3...5).contains(digits.count), digits.allSatisfy(\.isNumber) {
                return segment(String(s[..<dash]), lang: lang) + " " + digits
            }
        }
        return (lang == .zh ? zhSegments[s] : enSegments[s]) ?? s
    }

    /// "Expenses:Food:Dining" -> "餐饮 › 外出就餐" / "Food › Dining"
    public static func account(_ name: String?, lang: Lang = L10n.current) -> String {
        guard let name, !name.isEmpty else { return "—" }
        let parts = name.split(separator: ":").map(String.init)
        return (parts.count > 1 ? Array(parts.dropFirst()) : parts).map { segment($0, lang: lang) }.joined(separator: " › ")
    }

    /// A statement's shouting merchant text made readable for display (the ledger keeps the
    /// original): reference numbers and ACH codes dropped, ALL CAPS in normal case.
    /// "ACME CORP PAYROLL PPD ID: 9000000001" -> "Acme Corp Payroll"; "WHOLEFDS PRN 10234" -> "Wholefds Prn".
    public static func tidy(_ raw: String) -> String {
        // Lines Keel wrote itself carry ledger names ("Opening balance Assets:Chase:Checking-0001").
        if raw.hasPrefix("Opening balance ") { return L("期初余额") }
        if raw.hasPrefix("Valuation Assets:") || raw.hasPrefix("Valuation Liabilities:") { return L("市值更新") }
        var s = raw
        for pattern in [#"\s+(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b.*$"#, #"\s+(PPD|WEB|CCD|TEL)\s+ID:?.*$"#, #"\s*[#*]\s*\w*\d\w*"#, #"\s+\d{4,}\b"#, #"\s{2,}"#] {
            s = s.replacingOccurrences(of: pattern, with: pattern == #"\s{2,}"# ? " " : "", options: [.regularExpression, .caseInsensitive])
        }
        s = s.trimmingCharacters(in: .whitespaces)
        if s.isEmpty { return raw }
        let letters = s.unicodeScalars.filter { CharacterSet.letters.contains($0) }
        let ascii = letters.allSatisfy(\.isASCII)
        if ascii, !letters.isEmpty, s == s.uppercased() { s = s.capitalized }
        return s
    }
}
