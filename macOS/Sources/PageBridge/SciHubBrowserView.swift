import Foundation
import PageBridgeCore
import SwiftUI
import WebKit

struct SciHubBrowserRequest: Identifiable {
    let id = UUID()
    let doi: String
    let pageURL: URL
    let title: String
}

struct SciHubBrowserView: View {
    let request: SciHubBrowserRequest
    let onPDF: (URL) -> Void
    @Environment(\.dismiss) private var dismiss
    @StateObject private var browser: SciHubBrowserController

    init(request: SciHubBrowserRequest, onPDF: @escaping (URL) -> Void) {
        self.request = request
        self.onPDF = onPDF
        _browser = StateObject(wrappedValue: SciHubBrowserController(request: request, onPDF: onPDF))
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(request.title).font(.headline).lineLimit(1).help(request.title)
                    Text(browser.currentURL.host ?? "Sci-Hub").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if browser.isLoading { ProgressView().controlSize(.small) }
                Button("关闭") { browser.stop(); dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(16)
            Divider()
            SciHubWebContent(browser: browser)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            HStack(alignment: .center, spacing: 16) {
                Text(browser.status).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("继续打开全文") { browser.readCurrentPage() }
                    .buttonStyle(.borderedProminent).disabled(browser.isLoading)
            }.padding(16)
        }
        .frame(width: 800, height: 650)
        .background(Color(nsColor: .windowBackgroundColor))
        .onDisappear { browser.stop() }
    }
}

@MainActor
private final class SciHubBrowserController: NSObject, ObservableObject, WKNavigationDelegate {
    @Published private(set) var currentURL: URL
    @Published private(set) var isLoading = true
    @Published private(set) var status = "网页若要求验证，请自行完成。随后点击“继续打开全文”。"
    private let request: SciHubBrowserRequest
    private let onPDF: (URL) -> Void
    private weak var webView: WKWebView?
    private var active = true
    private var delivered = false
    private var navigationRevision = UUID()
    private static let maximumHTMLBytes = 2 * 1_024 * 1_024

    init(request: SciHubBrowserRequest, onPDF: @escaping (URL) -> Void) {
        self.request = request
        self.onPDF = onPDF
        currentURL = request.pageURL
        super.init()
    }

    func start(_ view: WKWebView) {
        guard active else { return }
        webView = view
        do {
            let url = try checkedURL(request.pageURL)
            view.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30))
        } catch {
            isLoading = false
            status = error.localizedDescription
        }
    }

    func stop() {
        active = false
        navigationRevision = UUID()
        webView?.stopLoading()
        webView?.navigationDelegate = nil
    }

    func readCurrentPage() {
        guard active, !delivered, let view = webView, let url = view.url else { return }
        let revision = navigationRevision
        // Read only rendered HTML. Do not click controls, submit forms or invoke
        // challenge scripts. Bound the serialized result before crossing IPC.
        let script = """
        (() => {
            const html = document.documentElement ? document.documentElement.outerHTML : "";
            return new TextEncoder().encode(html).byteLength <= \(Self.maximumHTMLBytes) ? html : null;
        })()
        """
        view.evaluateJavaScript(script) { [weak self, weak view] value, error in
            guard let self, self.active, !self.delivered,
                  self.navigationRevision == revision, view?.url == url else { return }
            if error != nil {
                self.status = "暂时无法读取页面，请等待网页加载完成后再试。"
                return
            }
            guard let html = value as? String, html.utf8.count <= Self.maximumHTMLBytes else {
                self.status = "页面超过读取上限，请在浏览器中查看来源页面。"
                return
            }
            do {
                let pdf = try SciHubResolver.pdfURL(in: html, pageURL: url, expectedDOI: self.request.doi)
                self.deliver(pdf)
            } catch {
                // A challenge or no-result page stays visible for the user.
                self.status = error.localizedDescription
            }
        }
    }

    private func checkedURL(_ url: URL) throws -> URL {
        guard var origin = URLComponents(url: url, resolvingAgainstBaseURL: false),
              origin.scheme?.lowercased() == "https", url.absoluteString.count <= 4_096 else {
            throw BridgeError.message("此窗口只打开 HTTPS 网页。")
        }
        origin.path = ""; origin.query = nil; origin.fragment = nil
        guard let originURL = origin.url else { throw BridgeError.message("网页地址无效。") }
        // Reuse the Core hostname/credential/port policy and DOI validation.
        _ = try SciHubResolver.articleURL(doi: request.doi, baseURL: originURL.absoluteString)
        return url
    }

    private func deliver(_ url: URL) {
        guard active, !delivered else { return }
        do { _ = try checkedURL(url) }
        catch { status = error.localizedDescription; return }
        delivered = true
        stop()
        onPDF(url)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard active, let frame = navigationAction.targetFrame else {
            decisionHandler(.cancel)
            return
        }
        guard let url = navigationAction.request.url, (try? checkedURL(url)) != nil else {
            if frame.isMainFrame {
                isLoading = false
                status = "已停止打开不受支持的网页地址。此窗口只允许 HTTPS 公共站点。"
            }
            decisionHandler(.cancel)
            return
        }
        // No native navigation or external-app launch is triggered by frames or
        // window.open. HTTPS frame content may render normally in this web view.
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard active else { decisionHandler(.cancel); return }
        guard let url = navigationResponse.response.url, (try? checkedURL(url)) != nil else {
            if navigationResponse.isForMainFrame {
                isLoading = false
                status = "已停止打开不受支持的网页地址。此窗口只允许 HTTPS 公共站点。"
            }
            decisionHandler(.cancel)
            return
        }
        if navigationResponse.isForMainFrame,
           navigationResponse.response.mimeType?.lowercased() == "application/pdf" {
            decisionHandler(.cancel)
            deliver(url)
        } else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard active else { return }
        navigationRevision = UUID()
        isLoading = true
        if let url = webView.url { currentURL = url }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard active else { return }
        isLoading = false
        if let url = webView.url { currentURL = url }
        readCurrentPage()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    private func failed(_ error: Error) {
        guard active, (error as NSError).code != NSURLErrorCancelled else { return }
        isLoading = false
        status = "网页加载失败：\(error.localizedDescription)"
    }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        completionHandler(challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust
                          ? .performDefaultHandling : .cancelAuthenticationChallenge, nil)
    }
}

private struct SciHubWebContent: NSViewRepresentable {
    let browser: SciHubBrowserController

    func makeCoordinator() -> SciHubBrowserController { browser }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        // Leave customUserAgent unset: use WebKit's real platform behavior.
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        DispatchQueue.main.async { [weak view] in
            if let view { browser.start(view) }
        }
        return view
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    static func dismantleNSView(_ nsView: WKWebView, coordinator: SciHubBrowserController) {
        coordinator.stop()
    }
}
