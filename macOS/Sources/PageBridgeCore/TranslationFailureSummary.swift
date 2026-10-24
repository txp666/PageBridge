import Foundation

/// A bounded, text-free snapshot rebuilt from a rejected worker report. Unknown
/// keys and arbitrary error strings never enter the retained diagnostic file.
public struct TranslationFailureSummary: Codable, Equatable, Sendable {
    public struct Paragraph: Codable, Equatable, Sendable {
        public let sourceParagraphSHA256: String
        public let layoutLabel: String
        public let reasons: [String]
    }
    public let protocolVersion: Int
    public let guardVersion: Int
    public let guardInstalled: Bool
    public let layoutGuardInstalled: Bool
    public let preparedParagraphs: Int
    public let validatedParagraphs: Int
    public let unresolvedCount: Int
    public let unresolvedReasons: [String: Int]
    public let unresolvedParagraphs: [Paragraph]

    private struct RawReport: Decodable {
        struct LayoutGuard: Decodable { let guardInstalled: Bool }
        struct Paragraph: Decodable {
            let sourceParagraphSHA256: String?
            let layoutLabel: String?
            let reasons: [String]
        }
        let protocolVersion: Int
        let guardVersion: Int
        let guardInstalled: Bool
        let layoutGuard: LayoutGuard
        let preparedParagraphs: Int
        let validatedParagraphs: Int
        let unresolvedCount: Int
        let unresolvedReasons: [String: Int]
        let unresolvedParagraphs: [Paragraph]
    }

    public static let maximumReportBytes = 256 * 1024

    public static func decodeWorkerReport(_ data: Data) throws -> Self {
        guard !data.isEmpty, data.count <= maximumReportBytes else { throw invalidReport }
        let raw = try JSONDecoder().decode(RawReport.self, from: data)
        guard raw.protocolVersion == 1, (1...1_000).contains(raw.guardVersion),
              (0...1_000_000).contains(raw.preparedParagraphs),
              (0...raw.preparedParagraphs).contains(raw.validatedParagraphs),
              (0...raw.preparedParagraphs).contains(raw.unresolvedCount),
              raw.preparedParagraphs == raw.validatedParagraphs + raw.unresolvedCount,
              raw.unresolvedCount > 0 || !raw.guardInstalled || !raw.layoutGuard.guardInstalled,
              raw.unresolvedReasons.count <= 256,
              raw.unresolvedReasons.values.allSatisfy({ $0 > 0 && $0 <= raw.unresolvedCount }),
              raw.unresolvedParagraphs.count == raw.unresolvedCount else { throw invalidReport }
        var reasons: [String: Int] = [:]
        for (reason, count) in raw.unresolvedReasons {
            reasons[safeReason(reason), default: 0] += count
        }
        let paragraphs = try raw.unresolvedParagraphs.map { entry -> Paragraph in
            guard let sha = entry.sourceParagraphSHA256, sha.count == 64,
                  sha.allSatisfy({ "0123456789abcdef".contains($0) }), entry.reasons.count <= 256 else { throw invalidReport }
            let label = (entry.layoutLabel ?? "other").lowercased().replacingOccurrences(of: " ", with: "_")
            return Paragraph(sourceParagraphSHA256: sha,
                             layoutLabel: knownLabels.contains(label) ? label : "other",
                             reasons: Array(Set(entry.reasons.map(safeReason))).sorted())
        }
        return Self(protocolVersion: 1, guardVersion: raw.guardVersion, guardInstalled: raw.guardInstalled,
                    layoutGuardInstalled: raw.layoutGuard.guardInstalled,
                    preparedParagraphs: raw.preparedParagraphs, validatedParagraphs: raw.validatedParagraphs,
                    unresolvedCount: raw.unresolvedCount, unresolvedReasons: reasons, unresolvedParagraphs: paragraphs)
    }

    public func message(hasPreviousResult: Bool) -> String {
        let result = hasPreviousResult ? "上次成功结果仍可使用。" : "原文仍可阅读，尚未生成可用译文。"
        guard guardInstalled && layoutGuardInstalled else {
            return "翻译校验组件未完整启用，本次未提交译文。\(result)请重新启动应用后重试。"
        }
        let detail: String
        if unresolvedReasons["translation-request-failed"] != nil {
            detail = "部分翻译请求失败。"
        } else if unresolvedReasons.keys.contains(where: { $0.hasPrefix("untranslated-") }) {
            detail = "部分文字仍未完成翻译。"
        } else if unresolvedReasons.keys.contains(where: { $0.contains("formula") || $0.contains("variable") || $0.contains("unit-exponent") }) {
            detail = "公式或数字单位的对应关系需核对。"
        } else if unresolvedReasons.keys.contains(where: { $0.contains("style") || $0.contains("markup") }) {
            detail = "样式或占位标记未完整保留。"
        } else {
            detail = "部分内容与原文的对应关系需核对。"
        }
        return "\(unresolvedCount) 段未通过完整性校验（已通过 \(validatedParagraphs)/\(preparedParagraphs)）。\(detail)本次未提交译文；\(result)"
    }

    /// Older workers may only return a generic integrity error. Do not fabricate
    /// counts or claim a previous successful result when none exists.
    public static func fallbackMessage(_ error: String, hasPreviousResult: Bool) -> String {
        guard error.contains("部分段落未通过译文完整性校验") else { return error }
        let result = hasPreviousResult ? "上次成功结果仍可使用。" : "原文仍可阅读，尚未生成可用译文。"
        return "译文未通过完整性校验，本次未提交译文。\(result)请重试或调整翻译服务。"
    }

    private static var invalidReport: BridgeError { .message("失败诊断格式无效，未保存该诊断。") }
    private static func safeReason(_ reason: String) -> String { knownReasons.contains(reason) ? reason : "other" }
    private static let knownLabels: Set<String> = ["text", "plain_text", "paragraph_hybrid", "title", "section_header", "caption", "figure_caption", "table_caption", "reference", "list_item", "fallback_line", "footnote", "page_header", "page_footer", "other"]
    // Exact machine-generated categories in the pinned guard. New or malformed
    // categories become "other" rather than retaining an arbitrary source string.
    private static let knownReasons: Set<String> = [
        "assigned-variable-pairing", "atomic-variable-pairing", "attention-event-omitted",
        "attention-role-mistranslated", "author-year-citation-changed", "bibliographic-access-date-changed",
        "bibliographic-source-as-power-supply", "calendar-month-or-day-changed", "caption-number-changed",
        "continuation-predicate-mistranslated", "coordinated-quantities-miscounted", "count-ratio-numerator-misgrouped",
        "cross-page-regulator-subject-changed", "daily-total-distributed-to-each-time", "data-processing-inequality-qualifier-added",
        "electrical-input-output-definition-lost", "electrical-input-output-expansion-untranslated", "empty-output",
        "encoded-source-token-changed", "formula-composition-mismatch", "formula-count-or-order",
        "formula-descriptor-pairing", "literal-sentinel-collision", "literal-source-token-changed",
        "mathematical-prime-variable-changed", "mean-operand-treated-as-varied-parameter", "meteorological-wind-energy-added",
        "model-added-prose-markup", "modulation-amplitude-assigned-to-mean", "nested-style",
        "non-equilibrium-contradictory-duplicate", "non-equilibrium-negation-lost", "numbered-rule-heading-changed",
        "objective-classifier-mistranslated-as-goal", "paragraph-preparation-failed", "parenthesis-boundary-changed",
        "passive-temporal-direction-changed", "physical-quantity-symbol-label-changed", "prediction-output-role-changed",
        "prose-numeric-literal-changed", "protected-classification-changed", "protected-literal-token-changed",
        "rejoined-source-paragraph-skipped", "research-triad-relation-narrowed", "scientific-identifier-changed",
        "scientific-role-base-pairing", "short-connector-preparation-failed", "source-accent-word-changed",
        "source-defined-acronym-sense-changed", "source-defined-circulation-role-changed", "source-list-prefix-displaced",
        "source-script-proof-changed", "source-small-script-geometry", "source-split-url-changed", "source-style-structure",
        "source-token-collision", "source-unit-exponent-geometry", "standard-reference-owner-changed",
        "style-count-or-order", "subjective-classification-reversed", "temperature-class-assignment-as-similarity",
        "translation-prompt-echo", "translation-request-failed", "unbalanced-style", "unexpected-formula",
        "unexpected-style", "unit-exponent-pairing", "unit-exponent-repeated-in-prose", "unknown-markup",
        "unresolved-paragraph", "untranslated-classification-connective", "untranslated-codified-modifier",
        "untranslated-contextual-idiom", "untranslated-prose-fragment", "untranslated-prose-paragraph",
        "varied-eps-parameter-omitted", "weather-class-count-scope-changed", "weather-class-wind-became-speed",
        "weather-role-previous-day-window", "whole-recovery-duplicate-key", "whole-recovery-invalid-json",
        "whole-recovery-response-shape", "wind-classification-became-speed"
    ]
}
