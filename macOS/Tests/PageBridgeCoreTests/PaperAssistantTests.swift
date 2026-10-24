import XCTest
import Foundation
@testable import PageBridgeCore

final class PaperAssistantTests: XCTestCase {
    private func record(id: UUID = UUID(), hash: String = "source-a") -> DocumentRecord {
        .init(id: id, name: "Paper", pageCount: 20, inputPath: "/not-used/input.pdf", inputSHA256: hash)
    }
    private var evidence: [PaperAssistantEvidence] { [.init(page: 3, text: "Only the supplied methods paragraph.")] }
    private var context: PaperAssistantContext { .init(mode: .currentPage, totalPages: 20, scannedPages: [3], evidence: evidence) }
    private func payload(_ request: URLRequest) throws -> [String: Any] { try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(request.httpBody)) as? [String: Any]) }

    func testDocumentModeRetainsLegacySerializedValue() throws {
        let mode = try JSONDecoder().decode(PaperAssistantMode.self, from: Data(#""paper""#.utf8))
        XCTAssertEqual(mode, .paper)
        XCTAssertEqual(mode.title, "文档问答")
        XCTAssertEqual(String(data: try JSONEncoder().encode(mode), encoding: .utf8), #""paper""#)
    }
    func testManualQuestionIncludesOverviewRequirementsAndPrecautionsWithinBudget() {
        var pages = Dictionary(uniqueKeysWithValues: (1...12).map { ($0, "General reference page \($0).") })
        pages[2] = "1. Overview\nThis manual explains the installation and maintenance of a controller."
        pages[8] = String(repeating: "Equipment specifications. ", count: 150) + "\n2.1 Requirements\nDisconnect the power supply before installation."
        pages[11] = "3. 注意事项\n维护前请确认设备已经断电。"
        let c = PaperAssistantContextBuilder.buildContext(mode: .paper, totalPages: 12, pages: pages, currentPage: 6, selection: "", question: "概述文档的要求与注意事项")
        XCTAssertTrue(Set(c.evidence.map(\.page)).isSuperset(of: [2, 6, 8, 11]))
        XCTAssertTrue(c.evidence.first { $0.page == 8 }?.text.contains("Disconnect the power supply") == true)
        XCTAssertLessThanOrEqual(c.evidence.count, PaperAssistantLimits.evidencePages)
        XCTAssertLessThanOrEqual(c.characterCount, PaperAssistantLimits.contextCharacters)
        XCTAssertTrue(c.evidence.allSatisfy { $0.text.count <= PaperAssistantLimits.pageCharacters })
    }
    func testSpecificManualQuestionRanksRelevantStepsAheadOfOtherGuidance() {
        var pages = Dictionary(uniqueKeysWithValues: (1...16).map { ($0, "Requirements\nGeneral operating conditions for section \($0).") })
        pages[1] = "Controller manual"
        pages[2] = "Purpose\nInstallation, operation and maintenance."
        pages[14] = String(repeating: "Service reference. ", count: 250) + "\n3.2 Steps\nDisconnect the supply and verify capacitor discharge before replacing the fuse."
        let c = PaperAssistantContextBuilder.buildContext(mode: .paper, totalPages: 16, pages: pages, currentPage: 4, selection: "", question: "Explain capacitor discharge")
        XCTAssertTrue(c.evidence.contains { $0.page == 2 })
        XCTAssertTrue(c.evidence.first { $0.page == 14 }?.text.contains("verify capacitor discharge") == true)
        XCTAssertTrue(c.evidence.first { $0.page == 14 }?.truncated == true)
        XCTAssertLessThanOrEqual(c.evidence.count, PaperAssistantLimits.evidencePages)
        XCTAssertLessThanOrEqual(c.characterCount, PaperAssistantLimits.contextCharacters)
    }
    func testChinesePaperQuestionIncludesCurrentAbstractConclusionWithinBudget() {
        var pages: [Int: String] = [:]
        for page in 1...100 { pages[page] = String(repeating: "ordinary paragraph ", count: 1_000) }
        pages[2] = "Abstract\n" + String(repeating: "method evidence ", count: 400)
        pages[84] = "8. Conclusions\n" + String(repeating: "conclusion evidence ", count: 400)
        let c = PaperAssistantContextBuilder.buildContext(mode: .paper, totalPages: 100, pages: pages, currentPage: 36, selection: "", question: "论文有哪些贡献与局限？")
        XCTAssertTrue(Set(c.evidence.map(\.page)).isSuperset(of: [2,36,84]))
        XCTAssertLessThanOrEqual(c.evidence.count, 6)
        XCTAssertLessThanOrEqual(c.characterCount, 12_000)
        XCTAssertTrue(c.evidence.allSatisfy { $0.text.count <= 2_400 && $0.truncated })
        XCTAssertTrue(c.scopeDescription.contains("模型未通读全文"))
    }
    func testQuestionRanksRelevantPageWithoutDroppingStructuralEvidence() {
        let c = PaperAssistantContextBuilder.buildContext(mode: .paper, totalPages: 10,
            pages: [1:"Abstract\nA study.",2:"unrelated",3:"unrelated",4:"causal intervention counterfactual",9:"Conclusions\nLimits.",10:"References"], currentPage: 2, selection: "", question: "Explain causal intervention counterfactual")
        XCTAssertTrue(c.evidence.contains { $0.page == 4 })
        XCTAssertTrue(c.evidence.contains { $0.page == 9 })
    }
    func testSelectionAndCurrentPageNeverImportOtherPages() {
        let c = PaperAssistantContextBuilder.buildContext(mode: .selection, totalPages: 20, pages: [1:"must not send"], currentPage: 3,
            selection: String(repeating: "选", count: 8_000), question: "explain")
        XCTAssertEqual(c.evidence.count, 1); XCTAssertEqual(c.evidence[0].page,3)
        XCTAssertEqual(c.evidence[0].origin,.selection); XCTAssertEqual(c.characterCount,6_000)
        XCTAssertTrue(c.evidence[0].truncated)
        let p = PaperAssistantContextBuilder.buildContext(mode: .currentPage, totalPages: 20, pages: [1:"Abstract other",3:"selected page",19:"Conclusions other"], currentPage: 3, selection:"not included",question:"test")
        XCTAssertEqual(p.evidence.map(\.page),[3]); XCTAssertEqual(p.characterCount,13)
    }
    func testFreeQuestionDoesNotEvenReadMissingPDFOrSelection() throws {
        let c = try PaperAssistantContextBuilder.prepare(document: record(), mode: .freeQuestion, currentPage: 3, selection: "secret excerpt", question: "general question")
        XCTAssertTrue(c.evidence.isEmpty); XCTAssertTrue(c.scannedPages.isEmpty)
        let request = try PaperAssistantService.request(service: .defaultOllama, apiKey: "ignored-key", question: "general question", context: c, history: [])
        XCTAssertFalse(String(data: request.httpBody!, encoding:.utf8)!.contains("secret excerpt"))
    }
    func testRequestUsesNativeOllamaEndpointAndNeverSendsKey() throws {
        let r = try PaperAssistantService.request(service: .defaultOllama, apiKey: "must-not-send", question: "解释方法", context: context, history: [])
        XCTAssertEqual(r.url?.absoluteString,"http://127.0.0.1:11434/api/chat")
        XCTAssertNil(r.value(forHTTPHeaderField:"Authorization"))
        let body = try payload(r)
        XCTAssertEqual(body["think"] as? Bool,false); XCTAssertEqual(body["stream"] as? Bool,false)
        XCTAssertEqual((body["options"] as? [String:Any])?["num_predict"] as? Int,3_072)
        XCTAssertFalse(String(data:r.httpBody!,encoding:.utf8)!.contains("must-not-send"))
    }
    func testRemoteRequestUsesValidatedDestinationAndHeaderOnlyCredential() throws {
        let service = ServiceConfiguration(name:"test",kind:.openAI,baseURL:"https://example.com/tenant/v1",model:"test-model",provider:.custom)
        let r = try PaperAssistantService.request(service:service,apiKey:" fixture-secret ",question:"explain",context:context,history:[])
        XCTAssertEqual(r.url?.absoluteString,"https://example.com/tenant/v1/chat/completions")
        XCTAssertEqual(r.value(forHTTPHeaderField:"Authorization"),"Bearer fixture-secret")
        XCTAssertEqual(r.value(forHTTPHeaderField:"Content-Type"),"application/json")
        XCTAssertFalse(String(data:r.httpBody!,encoding:.utf8)!.contains("fixture-secret"))
        XCTAssertThrowsError(try PaperAssistantService.request(service:service,apiKey:"bad\nkey",question:"explain",context:context,history:[]))
        var insecure=service;insecure.baseURL="http://example.com/v1"
        XCTAssertThrowsError(try PaperAssistantService.request(service:insecure,apiKey:"key",question:"explain",context:context,history:[]))
    }
    func testRequestRetainsInstructionBoundaryAndBoundsHistory() throws {
        let history=(0..<60).map { PaperAssistantMessage(role:$0.isMultiple(of:2) ? .user : .assistant,text:String(repeating:"H",count:5_000)) }
        let r=try PaperAssistantService.request(service:.defaultOllama,apiKey:"",question:"Explain",context:context,history:history)
        let m=try XCTUnwrap(try payload(r)["messages"] as? [[String:String]])
        XCTAssertEqual(m.first?["role"],"system")
        XCTAssertTrue(m.first?["content"]?.contains("untrusted evidence") == true)
        XCTAssertTrue(m.first?["content"]?.contains("[P3]") == true)
        XCTAssertLessThanOrEqual(m.dropFirst().dropLast().reduce(0){$0+($1["content"]?.count ?? 0)},8_000)
        XCTAssertLessThanOrEqual(m.count,12)
    }
    func testInvalidOrOversizedContextCannotBypassModeBoundaries() {
        let invalid: [PaperAssistantContext] = [
            .init(mode:.freeQuestion,totalPages:20,scannedPages:[],evidence:evidence),
            .init(mode:.currentPage,totalPages:20,scannedPages:[],evidence:[.init(page:21,text:"bad")]),
            .init(mode:.selection,totalPages:20,scannedPages:[],evidence:[.init(page:1,text:String(repeating:"x",count:6_001))]),
            .init(mode:.currentPage,totalPages:20,scannedPages:[],evidence:evidence+evidence),
            .init(mode:.currentPage,totalPages:0,scannedPages:[],evidence:[.init(page:1,text:"bad")])]
        for c in invalid { XCTAssertThrowsError(try PaperAssistantService.request(service:.defaultOllama,apiKey:"",question:"Explain",context:c,history:[])) }
        XCTAssertThrowsError(try PaperAssistantService.request(service:.defaultOllama,apiKey:"",question:String(repeating:"x",count:2_001),context:context,history:[]))
    }
    func testOnlyActuallySuppliedCitationPagesCanNavigate() {
        let answer=PaperAssistantAnswer(text:"Supported [P3]. Invented [P1], [P999]. Repeated [P3].",evidence:evidence)
        XCTAssertEqual(answer.citations,evidence);XCTAssertEqual(answer.unverifiedPages,[1,999])
        let noDocument=PaperAssistantAnswer(text:"[P3]",evidence:[])
        XCTAssertTrue(noDocument.citations.isEmpty);XCTAssertEqual(noDocument.unverifiedPages,[3])
    }
    func testResponseErrorsAndTruncationAreNotPublished() throws {
        let valid=Data(#"{"choices":[{"message":{"content":"解释 [P3]"},"finish_reason":"stop"}]}"#.utf8)
        XCTAssertEqual(try PaperAssistantService.parseResponse(valid,kind:.openAI,evidence:evidence).citations,evidence)
        for raw in [#"{"choices":[{"message":{"content":"partial"},"finish_reason":"length"}]}"#,#"{"choices":[{"message":{"content":"  "},"finish_reason":"stop"}]}"#,"not json"] {
            XCTAssertThrowsError(try PaperAssistantService.parseResponse(Data(raw.utf8),kind:.openAI,evidence:evidence))
        }
        XCTAssertThrowsError(try PaperAssistantService.parseResponse(Data(#"{"message":{"content":"partial"},"done":false}"#.utf8),kind:.ollama,evidence:evidence))
    }
    func testEphemeralSessionHasNoCookieCacheOrCredentialPersistence() {
        let c=PaperAssistantService.sessionConfiguration(timeout:37)
        XCTAssertNil(c.urlCache);XCTAssertNil(c.httpCookieStorage);XCTAssertNil(c.urlCredentialStorage)
        XCTAssertFalse(c.httpShouldSetCookies);XCTAssertEqual(c.timeoutIntervalForResource,37)
    }
    func testRedirectDelegateRefusesCredentialForwarding() throws {
        let delegate=PaperAssistantNoRedirect(),session=URLSession(configuration:.ephemeral)
        defer{session.invalidateAndCancel()}
        let first=URLRequest(url:URL(string:"https://example.com")!),task=session.dataTask(with:first)
        let response=try XCTUnwrap(HTTPURLResponse(url:first.url!,statusCode:302,httpVersion:nil,headerFields:["Location":"https://other.example/"]))
        var called=false
        delegate.urlSession(session,task:task,willPerformHTTPRedirection:response,newRequest:URLRequest(url:URL(string:"https://other.example")!)){redirect in
            called=true;XCTAssertNil(redirect)
        }
        XCTAssertTrue(called)
    }
    func testHistoryIsolatedByDocumentAndSourceAndContainsNoServiceCredentials() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:root)}
        let store=try PaperAssistantHistoryStore(directory:root),a=record(),b=record()
        try store.save([.init(role:.user,text:"A question"),.init(role:.assistant,text:"A answer",citations:evidence)],document:a)
        try store.save([.init(role:.user,text:"B question")],document:b)
        XCTAssertEqual(try store.load(document:a).last?.text,"A answer")
        XCTAssertEqual(try store.load(document:b).first?.text,"B question")
        XCTAssertTrue(try store.load(document:record(id:a.id,hash:"changed-source")).isEmpty)
        let raw=try String(contentsOf:store.url(for:a.id),encoding:.utf8)
        for forbidden in ["apiKey","Authorization","baseURL","model","fixture-secret"] { XCTAssertFalse(raw.contains(forbidden)) }
        try store.clear(document:a)
        XCTAssertTrue(try store.load(document:a).isEmpty);XCTAssertEqual(try store.load(document:b).count,1)
    }
    func testHistoryLimitAndAtomicReplacement() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:root)}
        let store=try PaperAssistantHistoryStore(directory:root),doc=record()
        let many=(0..<100).map { PaperAssistantMessage(role:.assistant,text:"\($0) "+String(repeating:"x",count:10_000),citations:evidence) }
        try store.save(many,document:doc)
        let restored=try store.load(document:doc)
        XCTAssertLessThanOrEqual(restored.count,40)
        XCTAssertLessThanOrEqual(restored.reduce(0){$0+$1.text.count+$1.citations.reduce(0){$0+$1.text.count}},160_000)
        XCTAssertTrue(restored.last!.text.hasPrefix("99 "))
        try store.save([.init(role:.user,text:"new")],document:doc)
        XCTAssertEqual(try store.load(document:doc).map(\.text),["new"])
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath:root.path).count,1)
    }
    func testCancelledOrReplacedRequestCannotPublishToAnotherConversation() {
        let a=record(),b=record()
        var gate=PaperAssistantRequestGate()
        let first=gate.begin(document:a)
        XCTAssertEqual(first.documentID,a.id)
        XCTAssertTrue(gate.accepts(first,sourceSHA256:a.inputSHA256))
        gate.cancel()
        XCTAssertFalse(gate.accepts(first,sourceSHA256:a.inputSHA256))
        let next=gate.begin(document:b)
        XCTAssertFalse(gate.accepts(first,sourceSHA256:a.inputSHA256))
        XCTAssertEqual(next.documentID,b.id)
        XCTAssertTrue(gate.accepts(next,sourceSHA256:b.inputSHA256))
        XCTAssertFalse(gate.accepts(next,sourceSHA256:"changed source"))
    }
    func testMalformedHistoryRemainsUntouchedWhenReadFails() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:root)}
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        let store=try PaperAssistantHistoryStore(directory:root),doc=record()
        let broken=Data("{not a valid conversation".utf8)
        try broken.write(to:store.url(for:doc.id))
        XCTAssertThrowsError(try store.load(document:doc))
        XCTAssertEqual(try Data(contentsOf:store.url(for:doc.id)),broken)
    }
    func testHistoryWrongIdentityAndInvalidCitationCannotBeLoaded() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer{try? FileManager.default.removeItem(at:root)}
        let store=try PaperAssistantHistoryStore(directory:root),a=record(),b=record()
        try store.save([.init(role:.assistant,text:"answer")],document:a)
        try FileManager.default.copyItem(at:store.url(for:a.id),to:store.url(for:b.id))
        XCTAssertThrowsError(try store.load(document:b))
        try store.save([.init(role:.assistant,text:"answer",citations:[.init(page:999,text:"fake")])],document:a)
        XCTAssertThrowsError(try store.load(document:a))
    }

    func testGroupedCitationsKeepOnlyOfferedPagesAndReportInventedPages() {
        let supplied: [PaperAssistantEvidence] = [.init(page:2,text:"second page"),.init(page:3,text:"third page")]
        let a=PaperAssistantAnswer(text:"组合 [P2, P3]，中文标点 [P2，P99]，单独 [P3]。",evidence:supplied)
        XCTAssertEqual(a.citations.map(\.page),[2,3]);XCTAssertEqual(a.unverifiedPages,[99])
        let malformed=PaperAssistantAnswer(text:"[P2 or P99] [P2, https://example.com]",evidence:supplied)
        XCTAssertTrue(malformed.citations.isEmpty)
    }
    func testPromptExplainsPhysicalPageLabelsAndPlainText() throws {
        let r=try PaperAssistantService.request(service:.defaultOllama,apiKey:"",question:"Explain",context:context,history:[])
        let messages=try XCTUnwrap(try payload(r)["messages"] as? [[String:String]])
        let system=try XCTUnwrap(messages.first?["content"])
        XCTAssertTrue(system.contains("actual one-based physical PDF page numbers"))
        XCTAssertTrue(system.contains("page numbers ARE available"))
        XCTAssertTrue(system.contains("may differ from page numbers printed"))
        XCTAssertTrue(system.contains("Use plain text"))
        XCTAssertTrue(system.contains("[P2][P3]"))
        XCTAssertTrue(system.contains("document-reading assistant"))
        XCTAssertTrue(system.contains("manuals, reports, contracts, textbooks and research papers"))
        XCTAssertTrue(system.contains("do not assume every document describes research"))
    }

}
