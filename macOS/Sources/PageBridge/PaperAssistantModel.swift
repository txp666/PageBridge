import Foundation
import SwiftUI
import PageBridgeCore

@MainActor
final class PaperAssistantModel: ObservableObject {
    @Published private(set) var conversations: [UUID: [PaperAssistantMessage]] = [:]
    @Published private(set) var errors: [UUID: String] = [:]
    @Published private(set) var contexts: [UUID: PaperAssistantContext] = [:]
    @Published private(set) var activeDocumentID: UUID?
    @Published private(set) var status = ""
    private var sourceHashes: [UUID: String] = [:]
    private var previewRevisions: [UUID: UUID] = [:]
    private var task: Task<Void, Never>?
    private var requestGate = PaperAssistantRequestGate()
    private var unreadableHistory = Set<UUID>()
    private let store: PaperAssistantHistoryStore?
    private var storeInitializationError: String?
    var isBusy: Bool { activeDocumentID != nil }

    init(historyDirectory: URL? = nil) {
        do { store = try PaperAssistantHistoryStore(directory: historyDirectory) }
        catch { store = nil; storeInitializationError = "对话暂时无法保存到本机。" }
    }

    func load(document: DocumentRecord) {
        guard sourceHashes[document.id] != document.inputSHA256 else { return }
        sourceHashes[document.id] = document.inputSHA256
        contexts[document.id] = nil
        do {
            conversations[document.id] = try store?.load(document: document) ?? []
            unreadableHistory.remove(document.id)
            errors[document.id] = storeInitializationError
        } catch {
            unreadableHistory.insert(document.id)
            conversations[document.id] = []; errors[document.id] = "对话记录读取失败，原文件未修改。\(error.localizedDescription)"
        }
    }
    func messages(for document: DocumentRecord) -> [PaperAssistantMessage] {
        guard sourceHashes[document.id] == document.inputSHA256 else { return [] }
        return conversations[document.id] ?? []
    }
    func preview(document: DocumentRecord, mode: PaperAssistantMode, page: Int, selection: String) async {
        load(document: document)
        let revision = UUID(); previewRevisions[document.id] = revision
        do {
            let context = try await Self.context(document: document, mode: mode, page: page, selection: selection, question: "")
            guard !Task.isCancelled, previewRevisions[document.id] == revision else { return }
            contexts[document.id] = context
        } catch {
            guard !Task.isCancelled, previewRevisions[document.id] == revision else { return }
            contexts[document.id] = nil
            // Empty selection is an ordinary mode state, not a modal error.
        }
    }
    @discardableResult
    func send(question: String, document: DocumentRecord, mode: PaperAssistantMode, page: Int, selection: String,
              service: ServiceConfiguration, apiKey: () throws -> String) -> Bool {
        guard !isBusy else { return false }
        load(document: document)
        let question = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, question.count <= PaperAssistantLimits.questionCharacters else {
            errors[document.id] = PaperAssistantError.invalidQuestion.localizedDescription; return false
        }
        do {
            try service.validate()
            // Called synchronously on the explicit send action, before any suspension or profile switch.
            let key = service.kind == .ollama ? "" : try apiKey()
            let prior = messages(for: document)
            let token = requestGate.begin(document: document); activeDocumentID = document.id
            errors[document.id] = nil; status = "正在本机选取证据…"
            task = Task { [weak self] in
                guard let self else { return }
                defer {
                    if self.requestGate.active == token { self.requestGate.cancel(); self.activeDocumentID = nil; self.status = ""; self.task = nil }
                }
                do {
                    let context = try await Self.context(document: document, mode: mode, page: page, selection: selection, question: question)
                    try Task.checkCancellation()
                    guard self.requestGate.accepts(token, sourceSHA256: self.sourceHashes[document.id]) else { return }
                    self.contexts[document.id] = context
                    self.conversations[document.id] = PaperAssistantHistoryStore.bounded(prior + [.init(role: .user, text: question)])
                    self.persist(document)
                    self.status = "正在等待 \(service.model)…"
                    let answer = try await PaperAssistantService.answer(service: service, apiKey: key, question: question, context: context, history: prior)
                    try Task.checkCancellation()
                    guard self.requestGate.accepts(token, sourceSHA256: self.sourceHashes[document.id]) else { return }
                    // Never consult the currently selected document here: this request belongs to its captured source.
                    let message = PaperAssistantMessage(role: .assistant, text: answer.text, citations: answer.citations, unverifiedPages: answer.unverifiedPages)
                    self.conversations[document.id] = PaperAssistantHistoryStore.bounded((self.conversations[document.id] ?? []) + [message])
                    self.persist(document)
                } catch is CancellationError { }
                catch {
                    if self.requestGate.active == token { self.errors[document.id] = error.localizedDescription }
                }
            }
            return true
        } catch { errors[document.id] = error.localizedDescription; return false }
    }
    func cancel() {
        let id = activeDocumentID
        requestGate.cancel(); activeDocumentID = nil; task?.cancel(); task = nil; status = ""
        if let id { errors[id] = "已取消。已发送的问题仍保留，可重新提问。" }
    }
    func clear(document: DocumentRecord) {
        if activeDocumentID == document.id { cancel() }
        do {
            try store?.clear(document: document)
            unreadableHistory.remove(document.id)
            conversations[document.id] = []; errors[document.id] = nil
        } catch { errors[document.id] = "清空失败，已有对话仍保留。\(error.localizedDescription)" }
    }
    private func persist(_ document: DocumentRecord) {
        guard !unreadableHistory.contains(document.id) else {
            errors[document.id] = "原对话文件损坏，已保留未覆盖。本次对话暂存当前窗口；明确清空当前对话后才可重新保存。"
            return
        }
        do {
            guard let store else { errors[document.id] = storeInitializationError; return }
            try store.save(conversations[document.id] ?? [], document: document)
        } catch { errors[document.id] = "回答仍在当前窗口，但保存到本机失败。\(error.localizedDescription)" }
    }
    private static func context(document: DocumentRecord, mode: PaperAssistantMode, page: Int, selection: String, question: String) async throws -> PaperAssistantContext {
        let worker = Task.detached(priority: .userInitiated) {
            try PaperAssistantContextBuilder.prepare(document: document, mode: mode, currentPage: page, selection: selection, question: question)
        }
        return try await withTaskCancellationHandler(operation: { try await worker.value }, onCancel: { worker.cancel() })
    }
}
