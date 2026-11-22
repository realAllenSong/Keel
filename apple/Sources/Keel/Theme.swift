import AppKit
import KeelCore
import SwiftUI

/// Keel's look (DESIGN.md, 宣纸编辑部): rice paper, sumi ink and one vermilion seal colour in
/// light; the same ink-and-paper world at night in dark. Headlines in a serif (Charter with Songti
/// for Chinese), words in Geist, every amount in Geist Mono so columns of figures line up.
enum Theme {
    static let paper = dynamic(0xF4EFE8, 0x15130F)  // the page
    static let card = dynamic(0xFBF8F3, 0x1C1915)
    static let ink = dynamic(0x1A1614, 0xEFE9E1)
    static let ink2 = dynamic(0x312926, 0xCFC6BC)
    static let muted = dynamic(0x615955, 0xA0968C)
    static let fill = dynamic(0xE9E2DA, 0x26221D)  // tracks, quiet backgrounds
    static let hover = dynamic(0xEFE9E1, 0x211D19)
    static let raised = dynamic(0xFBF8F3, 0x2A2621)  // the chosen option of a segmented control
    static let border = dynamic(0xDAD2C9, 0x2E2A24)
    static let rule = dynamic(0xC7BFBA, 0x3A352E)
    static let signal = dynamic(0xC2412D, 0xE0745D)  // vermilion: the seal, used where you act
    static let signalInk = dynamic(0xB23A27, 0xE88A75)
    static let contour = dynamic(0xE9E2DA, 0x241F1A)
    static let inflow = dynamic(0x2E6F57, 0x9CCFB4)
    static let outflow = dynamic(0x3A302B, 0xD8CFC5)  // spending is ink, not alarm; vermilion stays for actions
    static let primary = dynamic(0x1A1614, 0xEFE9E1)
    static let onPrimary = dynamic(0xF6F2EC, 0x15130F)

    // Exhibits: ink for the series, a quiet grey for the comparison, the band behind them.
    static let line = dynamic(0x1A1614, 0xEFE9E1)
    static let line2 = dynamic(0xB4A8A0, 0x6E655C)
    static let band = dynamic(0xE9E2DA, 0x26221D)

    // Categorical charts (明细, 规划): validated palette, first slot is the series colour.
    // Traditional pigments: 墨 ink, 靛青 indigo, 赭石 ochre, 石绿 malachite, 胭脂 rouge,
    // 藤黄 gamboge, 黛紫 dusky violet, 青灰 blue-grey. Muted so they sit on paper, lifted at night.
    static let series = [
        line, dynamic(0x2F4F6F, 0x8FA9C4), dynamic(0x9A5B34, 0xD29A72), dynamic(0x4E7A5E, 0x8DB79A),
        dynamic(0x9D2933, 0xD9737C), dynamic(0xB8892E, 0xE2B862), dynamic(0x5E4B6E, 0xA895C2), dynamic(0x6B7F86, 0xA3B5BB),
    ]
    static let income = line2
    static let expense = line
    static let chartGrid = dynamic(0xDAD2C9, 0x2E2A24)
    static let chartAxis = dynamic(0x1A1614, 0xCFC6BC)
    static let chartMuted = dynamic(0x615955, 0xA0968C)

    /// Content column, as the web's `max-w-6xl px-6`.
    static let column: CGFloat = 1104
    static let gutter: CGFloat = 24

    static func dynamic(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(
                srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255, alpha: 1
            )
        })
    }
}

/// Geist for words, Geist Mono for figures, as in the design. The faces ship inside Keel.app
/// (ATSApplicationFontsPath); run from source without them, the system faces stand in.
enum Typeface {
    static let bundled = NSFont(name: "Geist-Regular", size: 12) != nil

    /// A serif that sets Chinese in Songti: Charter first, with Songti in its cascade list.
    static func serif(_ size: CGFloat, bold: Bool) -> NSFont {
        let latin = bold ? "Charter-Bold" : "Charter-Roman"
        let chinese = bold ? "STSongti-SC-Bold" : "STSongti-SC-Regular"
        let cascade = [NSFontDescriptor(name: chinese, size: size)]
        let descriptor = NSFontDescriptor(name: latin, size: size).addingAttributes([.cascadeList: cascade])
        return NSFont(descriptor: descriptor, size: size)
            ?? NSFont(name: chinese, size: size)
            ?? .systemFont(ofSize: size, weight: bold ? .bold : .regular)
    }

    static func name(_ family: String, _ weight: Font.Weight) -> String {
        switch weight {
        case .semibold, .bold, .heavy, .black: return family + "-SemiBold"
        case .medium: return family + "-Medium"
        default: return family + "-Regular"
        }
    }
}

extension Font {
    /// Headings and hero figures.
    /// Headings and hero figures: the editorial serif (DESIGN.md).
    static func display(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        serif(size, weight == .regular || weight == .light ? .regular : .bold)
    }

    /// Editorial headlines: Charter for Latin and figures, Songti SC (宋体) for Chinese.
    static func serif(_ size: CGFloat, _ weight: Font.Weight = .bold) -> Font {
        Font(Typeface.serif(size, bold: weight != .regular && weight != .light))
    }

    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        Typeface.bundled ? .custom(Typeface.name("GeistMono", weight), size: size)
            : .system(size: size, weight: weight, design: .monospaced)
    }

    static func ui(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        Typeface.bundled ? .custom(Typeface.name("Geist", weight), size: size) : .system(size: size, weight: weight)
    }
}

extension Color {
    static var inflow: Color { Theme.inflow }
    static var outflow: Color { Theme.outflow }
}

// MARK: - layout

/// A screen's scrolling page: paper background, centred column.
struct Page<Content: View>: View {
    var width: CGFloat = Theme.column
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            content
                .frame(maxWidth: width, alignment: .leading)
                .padding(.horizontal, Theme.gutter)
                .padding(.top, 32)
                .padding(.bottom, 48)
                .frame(maxWidth: .infinity)
        }
        .scrollContentBackground(.hidden)
        .background(Theme.paper)
    }
}

/// A card: one plain question as its serif title, a quiet hint under it, an optional link.
struct Card<Content: View>: View {
    var title: String?
    var hint: String?
    var link: String?
    var action: (() -> Void)?
    var padding: CGFloat = 20
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title {
                CardHeader(title: title, hint: hint, link: link, action: action)
            }
            content
        }
        .padding(padding)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .surface()
    }
}

extension View {
    /// An editorial section (DESIGN.md): no box, a full ink rule above it, content on the page.
    func flatSection(top: CGFloat = 20) -> some View {
        padding(.top, top)
            .overlay(alignment: .top) { Rectangle().fill(Theme.ink).frame(height: 1) }
    }

    /// A card's material: a sheet of slightly brighter paper with a hairline edge, no cast shadow.
    func surface(radius: CGFloat = 6) -> some View {
        background {
            RoundedRectangle(cornerRadius: radius, style: .continuous).fill(Theme.card)
        }
        .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(Theme.border, lineWidth: 0.75))
    }
}

struct CardHeader: View {
    let title: String
    var hint: String?
    var link: String?
    var action: (() -> Void)?

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.serif(18)).foregroundStyle(Theme.ink)
                if let hint { Text(hint).font(.ui(11.5)).foregroundStyle(Theme.muted) }
            }
            Spacer(minLength: 12)
            if let link, let action { LinkButton(title: link, action: action) }
        }
    }
}

/// "明细 →": small, muted, ink on hover.
struct LinkButton: View {
    let title: String
    var arrow = true
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Text(arrow ? title + " →" : title)
                .font(.ui(11.5))
                .foregroundStyle(hover ? Theme.ink : Theme.muted)
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .pointingHandCursor()
    }
}

struct Hairline: View {
    var color: Color = Theme.border
    var body: some View { Rectangle().fill(color).frame(height: 1) }
}

struct VHairline: View {
    var color: Color = Theme.border
    var body: some View { Rectangle().fill(color).frame(width: 1) }
}

/// Hero number with its change versus the previous period. `goodWhen` says which way is good, so
/// the colour carries meaning, and the arrow and words carry it too.
struct StatTile: View {
    let label: String
    let value: String
    var delta: Double?
    var deltaText: String?
    var goodWhen: Direction = .up
    var sub: String?

    enum Direction { case up, down }

    var body: some View {
        let flat = delta.map { abs($0) < 0.005 } ?? true
        let good: Bool? = flat ? nil : ((delta ?? 0) > 0) == (goodWhen == .up)
        VStack(alignment: .leading, spacing: 8) {
            Text(label).font(.ui(12)).foregroundStyle(Theme.muted)
            Text(value).font(.mono(26)).foregroundStyle(Theme.ink).lineLimit(1).minimumScaleFactor(0.6)
            HStack(spacing: 8) {
                if let deltaText {
                    Text((flat ? "•" : (delta ?? 0) > 0 ? "▲" : "▼") + " " + deltaText)
                        .font(.mono(11))
                        .foregroundStyle(good == true ? Theme.inflow : good == false ? Theme.outflow : Theme.ink)
                }
                if let sub { Text(sub).font(.ui(11.5)).foregroundStyle(Theme.muted) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - controls

/// Text tabs with an ink underline (the web's top and 明细 tabs).
struct UnderlineTabs<ID: Hashable>: View {
    let items: [(ID, String)]
    @Binding var selection: ID
    var size: CGFloat = 15
    var spacing: CGFloat = 24

    var body: some View {
        HStack(spacing: spacing) {
            ForEach(items, id: \.0) { id, label in
                TabLabel(label: label, active: selection == id, size: size) { selection = id }
            }
        }
    }

    private struct TabLabel: View {
        let label: String
        let active: Bool
        let size: CGFloat
        let action: () -> Void
        @State private var hover = false

        var body: some View {
            Button(action: action) {
                Text(label)
                    .font(.ui(size, active ? .medium : .regular))
                    .foregroundStyle(active || hover ? Theme.ink : Theme.muted)
                    .padding(.bottom, 5)
                    .overlay(alignment: .bottom) {
                        Rectangle().fill(active ? Theme.ink : .clear).frame(height: 1)
                    }
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .onHover { hover = $0 }
        }
    }
}

/// A segmented control in Keel's style: a quiet track, the chosen option raised on card paper.
struct Segmented<ID: Hashable>: View {
    let items: [(ID, String)]
    @Binding var selection: ID
    var fill = false  // chosen option in solid ink (filters), instead of raised paper (settings)

    var body: some View {
        HStack(spacing: 2) {
            ForEach(items, id: \.0) { id, label in
                let on = selection == id
                Button { selection = id } label: {
                    Text(label)
                        .font(.ui(fill ? 12 : 13, on ? .medium : .regular))
                        .foregroundStyle(on ? (fill ? Theme.onPrimary : Theme.ink) : Theme.muted)
                        .padding(.horizontal, fill ? 11 : 14)
                        .padding(.vertical, fill ? 4 : 6)
                        .background {
                            if on {
                                RoundedRectangle(cornerRadius: 6, style: .continuous)
                                    .fill(fill ? Theme.primary : Theme.raised)
                                    .shadow(color: .black.opacity(fill ? 0 : 0.08), radius: 1, y: 1)
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(fill ? Theme.card : Theme.fill, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Theme.border))
        .fixedSize()
    }
}

/// Solid ink button (one per view: the thing to do next).
struct PrimaryButtonStyle: ButtonStyle {
    var compact = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.ui(compact ? 12 : 13, .medium))
            .foregroundStyle(Theme.onPrimary)
            .padding(.horizontal, compact ? 10 : 14)
            .padding(.vertical, compact ? 4 : 7)
            .background(Theme.primary.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
            .contentShape(Rectangle())
    }
}

/// Hairline-bordered button (secondary actions).
struct OutlineButtonStyle: ButtonStyle {
    var compact = false
    func makeBody(configuration: Configuration) -> some View {
        OutlineBody(configuration: configuration, compact: compact)
    }

    private struct OutlineBody: View {
        let configuration: Configuration
        let compact: Bool
        @State private var hover = false
        var body: some View {
            configuration.label
                .font(.ui(compact ? 12 : 13))
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, compact ? 8 : 12)
                .padding(.vertical, compact ? 4 : 6)
                .background(
                    (hover || configuration.isPressed ? Theme.fill : Theme.card.opacity(0.0001)),
                    in: RoundedRectangle(cornerRadius: 6, style: .continuous)
                )
                .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(Theme.border))
                .contentShape(Rectangle())
                .onHover { hover = $0 }
        }
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle {
    static var keelPrimary: PrimaryButtonStyle { PrimaryButtonStyle() }
    static var keelPrimaryCompact: PrimaryButtonStyle { PrimaryButtonStyle(compact: true) }
}

extension ButtonStyle where Self == OutlineButtonStyle {
    static var keelOutline: OutlineButtonStyle { OutlineButtonStyle() }
    static var keelOutlineCompact: OutlineButtonStyle { OutlineButtonStyle(compact: true) }
}

/// A text field drawn like the web's inputs: card paper, hairline border, no focus glow jump.
struct KeelField: View {
    let placeholder: String
    @Binding var text: String
    var width: CGFloat? = 240
    var mono = false
    var icon: String?
    var secure = false  // keys and tokens: typed text is masked

    var body: some View {
        HStack(spacing: 6) {
            if let icon { Image(systemName: icon).font(.ui(12)).foregroundStyle(Theme.muted) }
            Group {
                if secure {
                    SecureField("", text: $text, prompt: Text(placeholder).foregroundStyle(Theme.muted.opacity(0.8)))
                } else {
                    TextField("", text: $text, prompt: Text(placeholder).foregroundStyle(Theme.muted.opacity(0.8)))
                }
            }
            .textFieldStyle(.plain)
            .font(mono ? .mono(13) : .ui(13))
            .foregroundStyle(Theme.ink)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .frame(width: width)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(Theme.border))
    }
}

/// Legend item: small square swatch + label (chart legends sit above the plot, like the web).
struct LegendItem: View {
    let color: Color
    let label: String
    var dashed = false

    var body: some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 1.5).fill(color).frame(width: 9, height: 9)
            Text(label).font(.ui(11)).foregroundStyle(Theme.muted)
        }
    }
}

struct Dot: View {
    let color: Color
    var size: CGFloat = 8
    var body: some View { Circle().fill(color).frame(width: size, height: size) }
}

/// Keel's mark: one ink brushstroke rising toward a vermilion sun (the app icon's artwork). The
/// stroke is a mask tinted with the ink colour, so it turns light at night; the sun is drawn.
struct KeelMark: View {
    var size: CGFloat = 26

    static let stroke: NSImage? = {
        let bundled = Bundle.main.url(forResource: "KeelStroke", withExtension: "png")
        // Run from source (swift run, snapshots): the asset beside the package.
        let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../Assets/KeelStroke.png").standardizedFileURL
        return (bundled ?? source).flatMap { NSImage(contentsOf: $0) }
    }()

    var body: some View {
        ZStack(alignment: .topLeading) {
            if let stroke = Self.stroke {
                Theme.ink.mask(Image(nsImage: stroke).resizable().interpolation(.high))
            }
            // Measured from the artwork: centre (0.875, 0.146), radius 0.083 of the square.
            Circle().fill(Theme.signal)
                .frame(width: size * 0.166, height: size * 0.166)
                .offset(x: size * (0.875 - 0.083), y: size * (0.146 - 0.083))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

extension View {
    func pointingHandCursor() -> some View {
        onHover { inside in
            if inside { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }
}

// MARK: - money

/// "$4,463": amounts in the ledger's mono face.
func m0(_ m: Money) -> String { Format.money(m.value, digits: 0) }
func m0(_ d: Double) -> String { Format.money(Decimal(d), digits: 0) }
func m0(_ m: Money, currency: String) -> String { Format.money(m, currency: currency, digits: 0) }
func m0(_ d: Double, currency: String) -> String { Format.money(Decimal(d), currency: currency, digits: 0) }

/// "$62k" / "$1.2M" for chart axes.
func compactUSD(_ v: Double) -> String { compactMoney(v, currency: "USD") }

/// "$4.2k", "¥30k": a chart label in the chart's own currency.
func compactMoney(_ v: Double, currency: String) -> String {
    let a = abs(v)
    let sign = v < 0 ? "−" : ""
    let symbol = currency == "USD" ? "$" : currency == "CNY" ? "¥" : currency + " "
    if a >= 1_000_000 { return sign + symbol + String(format: "%.1fM", a / 1_000_000) }
    if a >= 1000 { return sign + symbol + String(format: a >= 10000 ? "%.0fk" : "%.1fk", a / 1000) }
    return sign + symbol + String(format: "%.0f", a)
}
