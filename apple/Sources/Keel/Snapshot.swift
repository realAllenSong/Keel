import AppKit
import KeelCore
import SwiftUI

/// Design review in CI: with KEEL_SNAPSHOT_DIR set, Keel walks through its screens in both
/// appearances (and English for the home screen), renders the window to PNG files and quits.
@MainActor
enum Snapshot {
    static var directory: URL? {
        ProcessInfo.processInfo.environment["KEEL_SNAPSHOT_DIR"].map { URL(fileURLWithPath: $0) }
    }

    /// KEEL_SNAPSHOT_OFFSCREEN=1: render windows far off-screen, so a review never covers your work.
    static var offscreen: Bool { ProcessInfo.processInfo.environment["KEEL_SNAPSHOT_OFFSCREEN"] != nil }
    /// KEEL_SNAPSHOT_ONLY=plan,assets: just these shots, in Chinese, light and dark.
    static var only: Set<String>? {
        ProcessInfo.processInfo.environment["KEEL_SNAPSHOT_ONLY"].map { Set($0.split(separator: ",").map(String.init)) }
    }

    struct Shot {
        let name: String
        let place: Place
        var tab: DetailTab?
        var tool: PlanTool?
        var sheet: String?  // "guide" | "pending": capture the sheet instead of the window
        var annual = false  // 年: the year view of the summary place
        var wait: Double = 3
    }

    static let shots: [Shot] = [
        Shot(name: "now", place: .now),
        Shot(name: "report", place: .report),
        Shot(name: "year", place: .now, annual: true),
        Shot(name: "spending", place: .details, tab: .spending),
        Shot(name: "calendar", place: .details, tab: .calendar),
        Shot(name: "assets", place: .details, tab: .assets),
        Shot(name: "holdings", place: .details, tab: .holdings, wait: 14),
        Shot(name: "bills", place: .details, tab: .bills),
        Shot(name: "transactions", place: .details, tab: .transactions),
        Shot(name: "plan", place: .plan, tool: .workspace, wait: 25),
        Shot(name: "backtest", place: .plan, tool: .backtest, wait: 20),
        Shot(name: "housing", place: .plan, tool: .housing, wait: 4),
        Shot(name: "settings", place: .settings),
        Shot(name: "guide", place: .now, sheet: "guide", wait: 3),
        Shot(name: "pending", place: .now, sheet: "pending", wait: 3),
    ]

    static func run(_ model: AppModel, into dir: URL) async {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        while model.engine.baseURL == nil {
            if case .failed = model.engine.state { NSApp.terminate(nil) }
            try? await Task.sleep(for: .milliseconds(300))
        }
        try? await Task.sleep(for: .seconds(2))
        if ProcessInfo.processInfo.environment["KEEL_SNAPSHOT_FILMSTRIP"] != nil {
            await currencyFilmstrip(model, into: dir)
            NSApp.terminate(nil)
            return
        }
        for (lang, themes) in [("zh", ["light", "dark"]), ("en", ["light"])] where only == nil || lang == "zh" {
            // A filtered run leaves your saved language and theme alone: appearance only, in memory.
            if only == nil { model.setLanguage(lang) }
            for theme in themes {
                if only == nil { model.setTheme(theme) } else { NSApp.appearance = NSAppearance(named: theme == "dark" ? .darkAqua : .aqua) }
                for shot in shots where (lang == "zh" || ["now", "report", "plan"].contains(shot.name)) && (only?.contains(shot.name) ?? true) {
                    if let tool = shot.tool { model.planTool = tool }
                    model.showAnnual = shot.annual
                    model.open(shot.place, tab: shot.tab)
                    guard let window = mainWindow else { continue }
                    window.setFrame(NSRect(x: offscreen ? -30000 : 0, y: offscreen ? -30000 : 0, width: 1440, height: 900), display: true)
                    if shot.sheet == "guide" { model.showGuide = true }
                    if shot.sheet == "pending" { model.showPendingFiles = true }
                    try? await Task.sleep(for: .seconds(shot.wait))
                    let url = dir.appendingPathComponent("\(shot.name)-\(theme)-\(lang).png")
                    if shot.sheet != nil, let sheet = window.attachedSheet {
                        if let view = sheet.contentView?.superview ?? sheet.contentView, let img = image(of: view) { write(img, to: url) }
                        model.showGuide = false
                        model.showPendingFiles = false
                        try? await Task.sleep(for: .seconds(1))
                    } else {
                        save(window, to: url)
                    }
                }
            }
        }
        NSApp.terminate(nil)
    }

    /// A frame every 60 ms while the report currency flips USD → CNY → USD on each money screen,
    /// to see whether a page collapses or moves while it reloads (KEEL_SNAPSHOT_FILMSTRIP=1).
    private static func currencyFilmstrip(_ model: AppModel, into dir: URL) async {
        let key = "keel.netWorthCurrency"
        let places: [(String, Place, DetailTab?, Bool)] = [
            ("now", .now, nil, false), ("report", .report, nil, false),
            ("annual", .now, nil, true), ("assets", .details, .assets, false),
        ]
        for (name, place, tab, annual) in places {
            UserDefaults.standard.set("USD", forKey: key)
            model.showAnnual = annual
            model.open(place, tab: tab)
            guard let window = mainWindow, let frame = window.contentView?.superview ?? window.contentView else { continue }
            window.setFrame(NSRect(x: 0, y: 0, width: 1440, height: 900), display: true)
            try? await Task.sleep(for: .seconds(5))
            for (step, target) in ["CNY", "USD"].enumerated() {
                UserDefaults.standard.set(target, forKey: key)
                for i in 0..<40 {
                    if let img = image(of: frame) {
                        write(img, to: dir.appendingPathComponent(String(format: "%@-%d-%02d.png", name, step, i)))
                    }
                    try? await Task.sleep(for: .milliseconds(60))
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private static var mainWindow: NSWindow? {
        NSApp.windows.filter { $0.isVisible && $0.contentView != nil && $0.frame.width > 400 }
            .max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
    }

    private static func save(_ window: NSWindow, to url: URL) {
        guard let frame = window.contentView?.superview ?? window.contentView else { return }
        guard let first = image(of: frame) else { return }
        // Scroll the page and stitch the slices, so the PNG shows the whole screen.
        guard let scroll = largestScrollView(in: frame), let doc = scroll.documentView,
              doc.frame.height > scroll.contentView.bounds.height + 4,
              let full = stitch(first, frame: frame, scroll: scroll, doc: doc)
        else {
            write(first, to: url)
            return
        }
        write(full, to: url)
    }

    private static func stitch(_ first: CGImage, frame: NSView, scroll: NSScrollView, doc: NSView) -> CGImage? {
        let scale = CGFloat(first.width) / frame.bounds.width
        let inset = scroll.contentInsets.top
        let rect = scroll.convert(scroll.bounds, to: frame)
        let visibleTop = (frame.isFlipped ? rect.minY : frame.bounds.height - rect.maxY) + inset
        let visibleH = scroll.contentView.bounds.height - inset
        let docH = doc.frame.height
        let height = visibleTop + docH
        let width = frame.bounds.width
        guard let ctx = CGContext(
            data: nil, width: Int(width * scale), height: Int(height * scale), bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue
        ) else { return nil }
        func paste(_ img: CGImage, top: CGFloat) {  // `top` in points from the canvas top
            ctx.draw(img, in: CGRect(x: 0, y: (height - top) * scale - CGFloat(img.height), width: CGFloat(img.width), height: CGFloat(img.height)))
        }
        paste(first, top: 0)
        var offset: CGFloat = 0
        while offset + visibleH < docH {
            offset = min(offset + visibleH, docH - visibleH)
            let y = doc.isFlipped ? offset : docH - visibleH - offset
            scroll.contentView.scroll(to: NSPoint(x: 0, y: y - (doc.isFlipped ? inset : 0)))
            scroll.reflectScrolledClipView(scroll.contentView)
            frame.layoutSubtreeIfNeeded()
            RunLoop.current.run(until: Date().addingTimeInterval(0.6))
            guard let img = image(of: frame),
                  let slice = img.cropping(to: CGRect(x: 0, y: visibleTop * scale, width: CGFloat(img.width), height: visibleH * scale))
            else { break }
            paste(slice, top: visibleTop + offset)
        }
        scroll.contentView.scroll(to: NSPoint(x: 0, y: doc.isFlipped ? -inset : docH - visibleH))
        return ctx.makeImage()
    }

    private static func largestScrollView(in view: NSView) -> NSScrollView? {
        var best: NSScrollView?
        func walk(_ v: NSView) {
            if let s = v as? NSScrollView, s.documentView != nil, !(s is NSTextView),
               s.frame.width * s.frame.height > (best.map { $0.frame.width * $0.frame.height } ?? 0) {
                best = s
            }
            v.subviews.forEach(walk)
        }
        walk(view)
        return best
    }

    private static func image(of view: NSView) -> CGImage? {
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return nil }
        view.cacheDisplay(in: view.bounds, to: rep)
        return rep.cgImage
    }

    private static func write(_ image: CGImage, to url: URL) {
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
        print("snapshot \(url.lastPathComponent) \(image.width)x\(image.height)")
    }
}
