import AppKit
import SwiftUI
import PageBridgeCore

/// The full paragraph uses the same source-equation attachments as the page canvas.
struct ReadingMathText: NSViewRepresentable {
    let block: StructuredReadingBlock
    let image: NSImage?
    let pageSize: CGSize
    let preferredFontSize: CGFloat
    var displayFontSize: CGFloat = 15
    var useSourceText: Bool = false

    func makeNSView(context: Context) -> NSTextView {
        let view = NSTextView(frame: .zero)
        view.isEditable = false; view.isSelectable = true; view.drawsBackground = false
        view.isRichText = true; view.isHorizontallyResizable = false; view.isVerticallyResizable = true
        view.textContainerInset = .zero; view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.widthTracksTextView = true
        return view
    }

    func updateNSView(_ view: NSTextView, context: Context) {
        apply(to: view, width: max(1, view.bounds.width > 0 ? view.bounds.width : 450))
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSTextView, context: Context) -> CGSize? {
        let width = max(1, proposal.width ?? 450)
        return CGSize(width: width, height: apply(to: nsView, width: width))
    }

    @discardableResult private func apply(to view: NSTextView, width: CGFloat) -> CGFloat {
        let crops = image.map { StructuredReadingMathImages.crops(for: block, image: $0, pageSize: pageSize) } ?? [:]
        let template = useSourceText ? block.sourceTemplate : block.translatedTemplate
        let layout = StructuredReadingTextLayout.fit(text: useSourceText ? block.sourceText : (block.translatedText ?? block.sourceText),
            in: CGSize(width: width, height: .greatestFiniteMagnitude), preferredFontSize: displayFontSize, heading: false,
            template: crops.count == block.inlineMath?.count ? template : nil,
            inlineImages: crops, referenceFontSize: preferredFontSize)
        let text = NSMutableAttributedString(attributedString: layout.attributedText)
        text.addAttribute(.foregroundColor, value: NSColor.labelColor, range: NSRange(location: 0, length: text.length))
        view.textContainer?.containerSize = CGSize(width: width, height: .greatestFiniteMagnitude)
        if view.textStorage?.isEqual(to: text) != true { view.textStorage?.setAttributedString(text) }
        return ceil(layout.measuredSize.height) + 2
    }
}
