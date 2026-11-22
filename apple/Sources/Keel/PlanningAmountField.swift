import KeelCore
import SwiftUI

/// Keeps invalid draft text visible and blocks Apply instead of silently retaining an old amount.
struct PlanningAmountField: View {
    let value: Double?
    let fieldID: String
    @Binding var invalidFields: Set<String>
    var allowNegative = false
    var width: CGFloat = 185
    var onDraftChange: () -> Void = {}
    let onChange: (Double) -> Void
    @State private var text: String

    init(value: Double?, fieldID: String, invalidFields: Binding<Set<String>>, allowNegative: Bool = false, width: CGFloat = 185, onDraftChange: @escaping () -> Void = {}, onChange: @escaping (Double) -> Void) {
        self.value = value
        self.fieldID = fieldID
        _invalidFields = invalidFields
        self.allowNegative = allowNegative
        self.width = width
        self.onChange = onChange
        self.onDraftChange = onDraftChange
        _text = State(initialValue: value.map { String(format: "%.2f", $0) } ?? "")
    }

    private var parsed: Double? { PlanningAmount.parse(text, allowNegative: allowNegative) }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            KeelField(placeholder: "0", text: $text, width: width, mono: true)
            if parsed == nil {
                Text(L("请输入有效金额；没有则填 0。")).font(.ui(10.5)).foregroundStyle(Theme.outflow).fixedSize(horizontal: false, vertical: true)
            }
        }
        .onChange(of: text) { _, _ in onDraftChange(); validate() }
        .onChange(of: value) { _, newValue in
            if let parsed, parsed != newValue { text = newValue.map { String(format: "%.2f", $0) } ?? "" }
        }
        .onAppear { validate() }
        .onDisappear { invalidFields.remove(fieldID) }
    }

    private func validate() {
        if let parsed {
            invalidFields.remove(fieldID)
            if parsed != value { onChange(parsed) }
        } else {
            invalidFields.insert(fieldID)
        }
    }
}
