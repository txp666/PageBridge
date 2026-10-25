using System.IO;
using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Web.WebView2.Core;

namespace PageBridge.Windows;

/// <summary>A private, user-operated verification page. No cookies or page scripts cross the RPC boundary.</summary>
internal sealed class SciHubBrowserPanel : Grid, IDisposable
{
    private readonly WebView2 web = new();
    private readonly Uri initialURL;
    private readonly string doi, profileDirectory;
    private readonly Func<Uri, Task> onPDF;
    private readonly TextBlock host = new() { FontSize = 11, Opacity = .65, TextTrimming = TextTrimming.CharacterEllipsis };
    private readonly TextBlock status = new() { FontSize = 12, Opacity = .7, TextWrapping = TextWrapping.Wrap, Text = "网页若要求验证，请自行完成。随后点击“继续打开全文”。" };
    private readonly ProgressRing loading = new() { Width = 18, Height = 18, IsActive = true };
    private readonly Button proceed = new() { Content = "继续打开全文", FontSize = 12, IsEnabled = false };
    private readonly Grid webHost = new();
    private readonly StackPanel unavailable = new() { Spacing = 14, Margin = new Thickness(28), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed };
    private CoreWebView2Environment? environment;
    private Uri? currentURL, pdfResponse;
    private int navigationRevision;
    private bool initialized, disposed, delivered, probing;

    internal SciHubBrowserPanel(string dataRoot, string doi, Uri initialURL, string title, Func<Uri, Task> onPDF, Action onBack)
    {
        this.doi = LiteratureAddress.DOI(doi) ?? ""; this.initialURL = initialURL; this.onPDF = onPDF;
        profileDirectory = Path.Combine(Path.GetFullPath(dataRoot), "webview-sessions", Guid.NewGuid().ToString("N"));
        RowDefinitions.Add(new() { Height = GridLength.Auto }); RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) }); RowDefinitions.Add(new() { Height = GridLength.Auto });
        var top = new Grid { Padding = new Thickness(18), ColumnSpacing = 12 };
        top.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); top.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); top.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var labels = new StackPanel { Spacing = 5 }; labels.Children.Add(new TextBlock { Text = title, FontSize = 15, FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis }); labels.Children.Add(host); top.Children.Add(labels);
        Grid.SetColumn(loading, 1); top.Children.Add(loading); var back = new Button { Content = "返回搜索", FontSize = 12 }; back.Click += (_, _) => onBack(); Grid.SetColumn(back, 2); top.Children.Add(back); Children.Add(top);
        webHost.Children.Add(web);
        unavailable.Children.Add(new TextBlock { Text = "无法启动内嵌浏览器", FontSize = 17, FontWeight = FontWeights.SemiBold });
        unavailable.Children.Add(new TextBlock { Text = "需要 Microsoft Edge WebView2 Runtime。你也可以返回搜索，在系统浏览器查看来源页面。", TextWrapping = TextWrapping.Wrap, MaxWidth = 420, Opacity = .7 });
        var runtime = new Button { Content = "查看 WebView2 Runtime 下载页面" }; runtime.Click += async (_, _) =>
        {
            try { await global::Windows.System.Launcher.LaunchUriAsync(new Uri("https://developer.microsoft.com/microsoft-edge/webview2/")); }
            catch { if (!disposed) status.Text = "无法打开系统浏览器，请稍后重试。"; }
        };
        unavailable.Children.Add(runtime); webHost.Children.Add(unavailable); Grid.SetRow(webHost, 1); Children.Add(webHost);
        var bottom = new Grid { Padding = new Thickness(18), ColumnSpacing = 12 }; bottom.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); bottom.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); bottom.Children.Add(status); Grid.SetColumn(proceed, 1); bottom.Children.Add(proceed); Grid.SetRow(bottom, 2); Children.Add(bottom);
        proceed.Click += async (_, _) => await ReadPageAsync();
        AutomationProperties.SetAutomationId(web, "LiteratureBrowser"); AutomationProperties.SetName(web, "Sci-Hub 验证网页");
        AutomationProperties.SetAutomationId(proceed, "LiteratureBrowserContinue"); AutomationProperties.SetAutomationId(back, "LiteratureBrowserBack");
        ActualThemeChanged += (_, _) => ApplyTheme(); ApplyTheme(); host.Text = initialURL.Host;
    }

    internal async Task InitializeAsync()
    {
        if (initialized || disposed) return; initialized = true;
        try
        {
            if (LiteratureAddress.PublicURL(initialURL.AbsoluteUri) is null) throw new InvalidOperationException("网页地址不是 HTTPS 公共地址。");
            Directory.CreateDirectory(profileDirectory);
            environment = await CoreWebView2Environment.CreateWithOptionsAsync("", profileDirectory, new CoreWebView2EnvironmentOptions());
            environment.BrowserProcessExited += (_, _) => CleanupProfile();
            if (disposed) { CleanupProfile(); return; }
            var options = environment.CreateCoreWebView2ControllerOptions(); options.IsInPrivateModeEnabled = true; options.ProfileName = "PageBridgeVerification";
            await web.EnsureCoreWebView2Async(environment, options);
            if (disposed) { web.Close(); CleanupProfile(); return; }
            var core = web.CoreWebView2;
            core.Settings.IsPasswordAutosaveEnabled = false; core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.AreHostObjectsAllowed = false; core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreDevToolsEnabled = false; core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsStatusBarEnabled = false; core.IsMuted = true;
            core.NavigationStarting += NavigationStarting;
            core.NavigationCompleted += async (_, args) =>
            {
                if (disposed || delivered) return;
                SetLoading(false);
                if (!args.IsSuccess) { status.Text = "页面加载失败，请返回搜索检查站点地址，或在系统浏览器打开来源页面。"; return; }
                await ReadPageAsync();
            };
            core.NewWindowRequested += (_, args) => { args.Handled = true; if (!disposed) status.Text = "页面请求打开新窗口。请在当前页面完成验证，或返回搜索使用系统浏览器。"; };
            core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
            core.ServerCertificateErrorDetected += (_, args) => args.Action = CoreWebView2ServerCertificateErrorAction.Cancel;
            core.DownloadStarting += (_, args) =>
            {
                args.Cancel = true; args.Handled = true;
                if (!disposed && !delivered && args.DownloadOperation.MimeType.Split(';')[0].Trim().Equals("application/pdf", StringComparison.OrdinalIgnoreCase)
                    && LiteratureAddress.PublicURL(args.DownloadOperation.Uri) is { } url)
                {
                    pdfResponse = url; status.Text = "已找到 PDF，点击“继续打开全文”下载并导入。"; SetLoading(false);
                }
            };
            core.WebResourceResponseReceived += (_, args) =>
            {
                // Only the top-level response is relevant. Do not read response bodies, credentials, or cookies.
                if (disposed || delivered || currentURL is null || args.Request.Uri != currentURL.AbsoluteUri) return;
                try
                {
                    var mime = args.Response.Headers.GetHeader("Content-Type");
                    if (mime.Split(';')[0].Trim().Equals("application/pdf", StringComparison.OrdinalIgnoreCase) && LiteratureAddress.PublicURL(args.Request.Uri) is { } url)
                        pdfResponse = url;
                }
                catch { /* A missing Content-Type is handled by the DOM probe and final PDF validation. */ }
            };
            core.Navigate(initialURL.AbsoluteUri);
        }
        catch
        {
            if (disposed) return;
            SetLoading(false); proceed.IsEnabled = false; web.Visibility = Visibility.Collapsed; unavailable.Visibility = Visibility.Visible;
            status.Text = "内嵌浏览器未就绪。返回搜索后仍可直接下载公开 PDF，或在系统浏览器查看来源页面。";
        }
    }

    private void NavigationStarting(CoreWebView2 sender, CoreWebView2NavigationStartingEventArgs args)
    {
        if (disposed || delivered) { args.Cancel = true; return; }
        if (LiteratureAddress.PublicURL(args.Uri) is not { } url)
        {
            args.Cancel = true; status.Text = "此页面尝试打开不支持的地址。仅允许 HTTPS 公共网页。"; SetLoading(false); return;
        }
        navigationRevision++; pdfResponse = null; currentURL = url; host.Text = url.Host; SetLoading(true);
    }

    private async Task ReadPageAsync()
    {
        if (disposed || delivered || probing || web.CoreWebView2 is null || currentURL is null) return;
        probing = true; proceed.IsEnabled = false; int revision = navigationRevision;
        try
        {
            if (pdfResponse is { } response) { await DeliverAsync(response, revision); return; }
            string raw = await web.CoreWebView2.ExecuteScriptAsync(ReadMetadataScript);
            if (disposed || delivered || revision != navigationRevision) return;
            if (raw.Length > 65_536) { status.Text = "网页返回的信息过多，请返回搜索或在浏览器查看。"; return; }
            var result = JsonNode.Parse(raw)?.AsObject();
            if (result is null) { status.Text = "尚未读到全文链接。请完成网站验证后再继续。"; return; }
            var pageURL = LiteratureAddress.PublicURL(result["page_url"]?.GetValue<string>() ?? "");
            if (pageURL is null || pageURL.AbsoluteUri != currentURL.AbsoluteUri) return;
            string rawCitation = result["doi"]?.GetValue<string>() ?? "";
            string? citation = LiteratureAddress.DOI(rawCitation);
            if (rawCitation.Length > 0 && doi.Length > 0 && citation != doi)
            { status.Text = "网页 DOI 与所选文献不同，已停止导入。请返回搜索确认文献。"; return; }
            if (result["challenge"]?.GetValue<bool>() == true)
            { status.Text = "请在页面中手动完成网站验证，再点击“继续打开全文”。"; return; }
            foreach (var item in result["candidates"]?.AsArray() ?? [])
                if (LiteratureAddress.PublicURL(item?.GetValue<string>() ?? "") is { } candidate)
                { await DeliverAsync(candidate, revision); return; }
            status.Text = "尚未发现可下载的 PDF。请完成验证后再继续，或返回搜索选择公开 PDF。";
        }
        catch { if (!disposed && revision == navigationRevision) status.Text = "无法读取当前网页。请完成验证后重试，或返回搜索。"; }
        finally { probing = false; if (!disposed && !delivered && revision == navigationRevision) proceed.IsEnabled = !loading.IsActive; }
    }

    private async Task DeliverAsync(Uri url, int revision)
    {
        if (disposed || delivered || revision != navigationRevision) return;
        delivered = true; status.Text = "正在交给下载器校验 PDF…"; proceed.IsEnabled = false;
        try { await onPDF(url); }
        catch { if (!disposed) { delivered = false; status.Text = "未能开始下载，请返回搜索重试。"; } }
    }

    // Read-only metadata inspection. No auto-clicks, form submissions, challenge solving, or document body transfer.
    private const string ReadMetadataScript = """
        (() => {
          const limit = value => String(value || '').slice(0, 4096);
          const meta = name => document.querySelector(`meta[name="${name}" i],meta[property="${name}" i]`)?.getAttribute('content') || '';
          const urls = [];
          const add = raw => {
            const value = String(raw || '').trim();
            if (!value || value.length > 4096) return;
            try { const u = new URL(value, document.baseURI); if (u.protocol === 'https:' && u.href.length <= 4096 && !urls.includes(u.href) && urls.length < 5) urls.push(u.href); } catch {}
          };
          add(meta('citation_pdf_url'));
          let inspected = 0;
          for (const element of document.querySelectorAll('iframe,embed,object')) {
            if (++inspected > 200) break;
            const value = element.getAttribute('src') || element.getAttribute('data') || '';
            if (value && (element.getAttribute('type') === 'application/pdf' || element.id === 'pdf' || /\.pdf(?:[?#]|$)/i.test(value))) add(value);
            if (urls.length >= 5) break;
          }
          const challenge = !!document.querySelector('input[name="captcha"],input[name="captcha_code"],#challenge-form,.g-recaptcha,.h-captcha,altcha-widget')
            || /^(just a moment|attention required|checking your browser)/i.test(document.title || '');
          return {page_url: limit(location.href), doi: limit(meta('citation_doi')).slice(0, 513), candidates: urls, challenge};
        })()
        """;

    private void SetLoading(bool value) { loading.IsActive = value; loading.Visibility = value ? Visibility.Visible : Visibility.Collapsed; proceed.IsEnabled = !value && !delivered; }
    private void ApplyTheme() => Background = new SolidColorBrush(ActualTheme == ElementTheme.Dark ? global::Windows.UI.Color.FromArgb(255, 31, 31, 31) : Colors.White);
    private void CleanupProfile()
    {
        _ = Task.Run(() => { try { if (Directory.Exists(profileDirectory)) Directory.Delete(profileDirectory, true); } catch (IOException) { } catch (UnauthorizedAccessException) { } });
    }
    public void Dispose()
    {
        if (disposed) return; disposed = true; navigationRevision++;
        try { web.CoreWebView2?.Stop(); web.Close(); } catch { }
        CleanupProfile();
    }
}
