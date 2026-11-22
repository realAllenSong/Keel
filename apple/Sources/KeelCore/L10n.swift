import Foundation

/// UI language. Source strings are the Chinese text, as in the rest of Keel; English comes from
/// `L10n.en` (see L10nEN.swift). `scripts/check_l10n.py` fails CI on a missing entry.
public enum Lang: String, Sendable {
    case zh, en
}

public enum L10n {
    /// "system" | "zh" | "en", stored in UserDefaults (and mirrored to the engine's settings).
    public static let preferenceKey = "keel.lang"

    public static var preference: String {
        get { UserDefaults.standard.string(forKey: preferenceKey) ?? "system" }
        set { UserDefaults.standard.set(newValue, forKey: preferenceKey) }
    }

    public static func resolve(_ preference: String) -> Lang {
        switch preference {
        case "zh": return .zh
        case "en": return .en
        default:
            let first = Locale.preferredLanguages.first?.lowercased() ?? "en"
            return first.hasPrefix("zh") ? .zh : .en
        }
    }

    public static var current: Lang { resolve(preference) }
}

/// Translate a Chinese source string, filling `{name}` placeholders.
public func L(_ zh: String, _ values: [String: CustomStringConvertible] = [:]) -> String {
    L(zh, lang: L10n.current, values)
}

public func L(_ zh: String, lang: Lang, _ values: [String: CustomStringConvertible] = [:]) -> String {
    var s = lang == .zh ? zh : (L10n.en[zh] ?? zh)
    for (k, v) in values {
        s = s.replacingOccurrences(of: "{\(k)}", with: v.description)
    }
    return s
}
