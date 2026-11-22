import KeelCore
import SwiftUI

/// 怎么下载账单: per-institution steps from the engine's institution list, then the three ways to
/// bring the file in.
struct DownloadGuide: View {
    var initialSelection: String? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var sources: [Institution] = []
    @State private var picked: String?

    var body: some View {
        let current = sources.first { $0.id == picked } ?? sources.first
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("怎么下载账单")).font(.display(26)).foregroundStyle(Theme.ink)
                    (Text(L("要的是")) + Text(L("交易明细")).foregroundColor(Theme.ink).fontWeight(.medium)
                        + Text(L("（CSV、Excel 或压缩包）。先导出一个月核对，再补历史；同一来源重复导入会去重。PDF 和图片暂不支持。")))
                        .font(.ui(13)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 20)
                Button(L("完成")) { dismiss() }.buttonStyle(.keelOutlineCompact).keyboardShortcut(.cancelAction)
            }
            .padding(24)
            Hairline()
            HStack(alignment: .top, spacing: 24) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(sources.enumerated()), id: \.element.id) { i, s in
                            if i == 0 || s.supported != sources[i - 1].supported {
                                Text(s.supported ? L("可以直接导入") : L("先下载好，导入器开发中"))
                                    .font(.ui(10.5)).foregroundStyle(Theme.muted).padding(.horizontal, 10).padding(.top, i == 0 ? 0 : 10).padding(.bottom, 4)
                            }
                            Button { picked = s.id } label: {
                                Text(s.name).font(.ui(13, s.id == current?.id ? .medium : .regular))
                                    .foregroundStyle(s.id == current?.id ? Theme.ink : Theme.muted)
                                    .padding(.horizontal, 10).padding(.vertical, 7)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(s.id == current?.id ? Theme.fill : .clear, in: RoundedRectangle(cornerRadius: 6))
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .frame(width: 180)
                if let s = current {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            HStack(spacing: 8) {
                                Text(s.supported ? L("可以直接导入") : L("导入器开发中"))
                                    .font(.ui(11)).foregroundStyle(s.supported ? Theme.inflow : Theme.ink)
                                    .padding(.horizontal, 8).padding(.vertical, 2)
                                    .background((s.supported ? Theme.inflow.opacity(0.15) : Theme.fill), in: Capsule())
                                Text(["bank", "payment"].contains(s.kind) ? L("在银行或支付 App 中导出，或使用网上银行") : L("在电脑上的网页里操作"))
                                    .font(.ui(11.5)).foregroundStyle(Theme.muted)
                            }
                            ForEach(Array(s.steps.enumerated()), id: \.offset) { i, step in
                                HStack(alignment: .firstTextBaseline, spacing: 10) {
                                    Text("\(i + 1)").font(.mono(10.5)).foregroundStyle(Theme.muted)
                                        .frame(width: 20, height: 20).overlay(Circle().strokeBorder(Theme.rule))
                                    Text(step).font(.ui(13)).foregroundStyle(Theme.ink).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            if !s.note.isEmpty {
                                Text(s.note).font(.ui(11.5)).foregroundStyle(Theme.muted).padding(.leading, 12)
                                    .overlay(alignment: .leading) { Rectangle().fill(Theme.signal).frame(width: 2) }
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            if let url = URL(string: s.exportUrl), !s.exportUrl.isEmpty {
                                Button(L("打开 {name} 网站", ["name": s.name]) + " ↗") { NSWorkspace.shared.open(url) }
                                    .buttonStyle(.keelPrimary)
                            }
                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                } else {
                    Text(L("正在读取…")).font(.ui(13)).foregroundStyle(Theme.muted)
                }
            }
            .padding(24)
            .frame(height: 380)
            Hairline()
            VStack(alignment: .leading, spacing: 12) {
                Text(L("下载之后")).font(.ui(13, .medium)).foregroundStyle(Theme.ink)
                HStack(alignment: .top, spacing: 20) {
                    after(L("拖进来"), L("把文件拖到 Keel 窗口的任意位置，或点右上角「拖入账单」。"))
                    after(L("或者什么都不做"), L("留在「下载」文件夹里的账单，Keel 会自己发现。"))
                    after(L("压缩包不用解压"), L("支付宝、微信的加密压缩包直接拖进来，Keel 会问你解压密码。"))
                }
                Text(L("仍需在原 App 中按期导出新账单。银行与钱包里的同笔消费要先核对，避免重复记账。菜单以实际 App 为准，文件只在这台电脑读取。"))
                    .font(.ui(10.5)).foregroundStyle(Theme.muted)
            }
            .padding(24)
        }
        .frame(width: 780)
        .background(Theme.paper)
        .task {
            picked = initialSelection
            guard let api = model.api, let list = try? await api.institutions() else { return }
            sources = list.filter(\.supported) + list.filter { !$0.supported }
        }
    }

    private func after(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.ui(13, .medium)).foregroundStyle(Theme.ink)
            Text(body).font(.ui(11.5)).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
