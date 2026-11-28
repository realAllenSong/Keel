import KeelCore
import SwiftUI

/// Bank lines that have not posted yet: one quiet row that expands. Shown beside the books and
/// never counted in any total; a posted line replaces it, a released hold simply disappears.
struct InFlightRow: View {
    let title: String
    let lines: [InFlight.Line]
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeOut(duration: 0.15)) { open.toggle() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: open ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(Theme.muted)
                        .frame(width: 12)
                    Text(title).font(.ui(13)).foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .pointingHandCursor()
            if open {
                VStack(spacing: 0) {
                    ForEach(lines) { line in
                        row(line)
                        Hairline(color: Theme.border.opacity(0.6))
                    }
                }
                .padding(.leading, 20)
                .padding(.top, 8)
            }
        }
    }

    private func row(_ l: InFlight.Line) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(Format.day(l.date)).font(.ui(12)).foregroundStyle(Theme.muted)
                .frame(width: 72, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(l.what).font(.ui(13)).foregroundStyle(Theme.ink2).lineLimit(1)
                if l.hold {
                    Text(L("可能是押金或预授权，之后可能退回")).font(.ui(11)).foregroundStyle(Theme.muted)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(Format.account(l.account)).font(.ui(12)).foregroundStyle(Theme.muted).lineLimit(1)
                .frame(width: 160, alignment: .leading)
            Text(Format.money(l.amount, currency: l.currency, digits: 2, sign: true))
                .font(.mono(12.5)).foregroundStyle(l.amount.isNegative ? Theme.ink2 : Theme.inflow)
                .frame(width: 100, alignment: .trailing)
        }
        .padding(.vertical, 8)
    }
}

extension InFlight {
    /// "另有 3 笔支出银行还没入账，共 $84.41" for the month page (nil when nothing is pending).
    func spendTitle(currency: String) -> String? {
        guard spendCount > 0 else { return nil }
        let base = L("另有 {n} 笔支出银行还没入账，共 {a}，不计入本月支出",
                     ["n": spendCount, "a": Format.money(spend, currency: currency, digits: 2)])
        return holds > 0 ? base + L("；其中 {h} 笔可能是押金", ["h": holds]) : base
    }
}
