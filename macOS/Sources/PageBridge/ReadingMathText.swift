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
        // TextKit 1 matches the paragraph measurement and avoids adding
        // TextKit 2 rendering subviews during the popover's constraint pass.
        let view = NSTextView(usingTextLayoutManager: false)
        view.isEditable = false; view.isSelectable = true; view.drawsBackground = false
        view.isRichText = true; view.isHorizontallyResizable = false; view.isVerticallyResizable = false
        view.textContainerInset = .zero; view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.containerSize = CGSize(width: 450, height: CGFloat.greatestFiniteMagnitude)
        view.textContainer?.widthTracksTextView = true
        return view
    }

    func updateNSView(_ view: NSTextView, context: Context) {
        let width = view.bounds.width > 0 ? view.bounds.width : 450
        let text = NSMutableAttributedString(attributedString: fittedText(width: width).attributedText)
        text.addAttribute(.foregroundColor, value: NSColor.labelColor, range: NSRange(location: 0, length: text.length))
        if view.textStorage?.isEqual(to: text) != true { view.textStorage?.setAttributedString(text) }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: NSTextView, context: Context) -> CGSize? {
        let width = max(1, proposal.width ?? 450)
        return CGSize(width: width, height: ceil(fittedText(width: width).measuredSize.height) + 2)
    }

    private func fittedText(width: CGFloat) -> StructuredReadingTextLayout {
        let crops = image.map { StructuredReadingMathImages.crops(for: block, image: $0, pageSize: pageSize) } ?? [:]
        let template = useSourceText ? block.sourceTemplate : block.translatedTemplate
        return StructuredReadingTextLayout.fit(text: useSourceText ? block.sourceText : (block.translatedText ?? block.sourceText),
            in: CGSize(width: width, height: .greatestFiniteMagnitude), preferredFontSize: displayFontSize, heading: false,
            template: crops.count == block.inlineMath?.count ? template : nil,
            inlineImages: crops, referenceFontSize: preferredFontSize, firstLineIndent: useSourceText ? 0 : (block.firstLineIndent ?? 0),
            sourceBold: block.sourceBold)
    }
}
