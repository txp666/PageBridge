import XCTest
@testable import PageBridgeCore

final class TranslationFailureSummaryTests: XCTestCase {
    private let paragraphSHA = String(repeating: "a", count: 64)
    private func report() -> [String: Any] {
        ["protocolVersion": 1, "guardVersion": 7, "guardInstalled": true,
         "layoutGuard": ["guardInstalled": true], "preparedParagraphs": 20,
         "validatedParagraphs": 18, "unresolvedCount": 2,
         "unresolvedReasons": ["formula-count-or-order": 1, "untranslated-prose-paragraph": 1],
         "unresolvedParagraphs": [
            ["sourceParagraphSHA256": paragraphSHA, "layoutLabel": "text", "reasons": ["formula-count-or-order"]],
            ["sourceParagraphSHA256": paragraphSHA, "layoutLabel": "plain_text", "reasons": ["untranslated-prose-paragraph"]]
         ]]
    }
    private func decode(_ object: [String: Any]) throws -> TranslationFailureSummary {
        try TranslationFailureSummary.decodeWorkerReport(JSONSerialization.data(withJSONObject: object))
    }
    func testRebuildsWhitelistWithoutRawTextOrUnrecognizedReasonStrings() throws {
        var object = report()
        object["prompt"] = "PRIVATE-PROMPT"; object["apiKey"] = "PRIVATE-KEY"
        object["counters"] = ["anything": "PRIVATE-COUNTER"]
        object["unresolvedReasons"] = ["formula-count-or-order": 1, "PRIVATE-REASON": 1]
        object["unresolvedParagraphs"] = [
            ["sourceParagraphSHA256": paragraphSHA, "layoutLabel": "text", "reasons": ["formula-count-or-order"], "source": "PRIVATE-TEXT"],
            ["sourceParagraphSHA256": paragraphSHA, "layoutLabel": "PRIVATE-LABEL", "reasons": ["PRIVATE-REASON"], "response": "PRIVATE-RESPONSE"]
        ]
        let summary = try decode(object)
        let stored = String(decoding: try JSONEncoder().encode(summary), as: UTF8.self)
        XCTAssertFalse(stored.contains("PRIVATE")); XCTAssertFalse(stored.contains("apiKey")); XCTAssertFalse(stored.contains("counters"))
        XCTAssertEqual(summary.unresolvedReasons, ["formula-count-or-order": 1, "other": 1])
        XCTAssertEqual(summary.unresolvedParagraphs.last?.layoutLabel, "other")
        XCTAssertEqual(summary.unresolvedParagraphs.last?.reasons, ["other"])
    }
    func testRejectsInconsistentCountsWrongTypesVersionAndOversizedReport() throws {
        for (key, value) in [("preparedParagraphs", 19 as Any), ("validatedParagraphs", -1 as Any),
                             ("unresolvedCount", true as Any), ("protocolVersion", 2 as Any),
                             ("guardVersion", "PRIVATE" as Any)] {
            var object = report(); object[key] = value
            XCTAssertThrowsError(try decode(object), key)
        }
        var wrongRows = report(); wrongRows["unresolvedParagraphs"] = []
        XCTAssertThrowsError(try decode(wrongRows))
        var badHash = report(); badHash["unresolvedParagraphs"] = [
            ["sourceParagraphSHA256": "PRIVATE-HASH", "reasons": ["formula-count-or-order"]],
            ["sourceParagraphSHA256": paragraphSHA, "reasons": []]
        ]
        XCTAssertThrowsError(try decode(badHash))
        XCTAssertThrowsError(try TranslationFailureSummary.decodeWorkerReport(Data(repeating: 32, count: TranslationFailureSummary.maximumReportBytes + 1)))
    }
    func testFirstFailureAndPreviousResultMessagesDoNotOverclaim() throws {
        let summary = try decode(report())
        let first = summary.message(hasPreviousResult: false), retry = summary.message(hasPreviousResult: true)
        XCTAssertTrue(first.contains("2 段")); XCTAssertTrue(first.contains("18/20"))
        XCTAssertTrue(first.contains("本次未提交译文")); XCTAssertTrue(first.contains("尚未生成可用译文"))
        XCTAssertFalse(first.contains("上次")); XCTAssertTrue(retry.contains("上次成功结果仍可使用"))
        XCTAssertFalse(first.contains("语义正确")); XCTAssertFalse(retry.contains("全部正确"))
        let old = "部分段落未通过译文完整性校验，已保留上次结果。请重试或更换模型。"
        XCTAssertFalse(TranslationFailureSummary.fallbackMessage(old, hasPreviousResult: false).contains("上次"))
        XCTAssertTrue(TranslationFailureSummary.fallbackMessage(old, hasPreviousResult: true).contains("上次成功结果"))
        XCTAssertEqual(TranslationFailureSummary.fallbackMessage("连接超时", hasPreviousResult: false), "连接超时")
    }
    func testMissingGuardIsReportedAsInfrastructureFailureNotBadTranslation() throws {
        var object = report(); object["guardInstalled"] = false
        let message = try decode(object).message(hasPreviousResult: false)
        XCTAssertTrue(message.contains("校验组件未完整启用"))
        XCTAssertFalse(message.contains("2 段未通过"))
    }
}
