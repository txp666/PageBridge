using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.UI;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace PageBridge.Windows;

/// <summary>OpenAlex discovery and selectable full-text sources, matching the Mac search sheet.</summary>
internal sealed class LiteratureSearchPanel : Grid, IDisposable
{
    private readonly BackendClient backend;
    private readonly Func<string, Task> imported;
    private readonly Action close;
    private readonly Grid searchContent = new();
    private readonly Grid browserHost = new() { Visibility = Visibility.Collapsed };
    private readonly TextBox query = new() { PlaceholderText = "标题、关键词或 DOI / doi.org 链接", MaxLength = 1000, FontSize = 13 };
    private readonly ComboBox source = new() { Width = 140, MinWidth = 0, FontSize = 12 };
    private readonly CheckBox openAccess = new() { Content = "仅开放获取", FontSize = 12 };
    private readonly Button search = Button("搜索"), settingsToggle = Button("地址与检索配置");
    private readonly Button cancelSearch = Button("取消搜索");
    private readonly TextBlock searchStatus = Text("", 11, .6);
    private readonly ProgressRing searching = new() { Width = 16, Height = 16, IsActive = false, Visibility = Visibility.Collapsed };
    private readonly StackPanel results = new() { Spacing = 12, Margin = new Thickness(20) };
    private readonly ScrollViewer resultsScroll;
    private readonly ScrollViewer headerScroll;
    private readonly StackPanel empty = new() { Spacing = 12, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(24) };
    private readonly TextBlock emptyTitle = Text("从一个标题或 DOI 开始", 16);
    private readonly TextBlock emptyHint = Text("例如：Ten Simple Rules for Better Figures\n或 10.1371/journal.pcbi.1003833", 12, .6);
    private readonly Button directOpen = Button("直接打开此 DOI 的全文"), directCancel = Button("取消下载"), loadMore = Button("加载更多");
    private readonly StackPanel settingsPanel = new() { Spacing = 8, Visibility = Visibility.Collapsed };
    private readonly TextBox address = new() { PlaceholderText = "Sci-Hub HTTPS 地址", Text = "https://sci-hub.ru", MaxLength = 4096, FontSize = 12 };
    private readonly PasswordBox apiKey = new() { PlaceholderText = "可选：粘贴 OpenAlex API Key", PasswordRevealMode = PasswordRevealMode.Peek, MaxLength = 4096 };
    private readonly TextBlock keyState = Text("匿名检索可直接使用；API Key 可提高额度。", 11, .6);
    private readonly Button saveSettings = Button("保存配置"), clearKey = Button("清除已保存 Key");
    private readonly StackPanel errorPanel = new() { Spacing = 7, Margin = new Thickness(22, 0, 22, 14), Visibility = Visibility.Collapsed };
    private readonly TextBlock error = Text("", 12);
    private readonly Grid errorActions = new() { ColumnSpacing = 8 };
    private readonly Button verify = Button("在 App 中验证并继续"), external = Button("在浏览器查看来源页面"), tryPublic = Button("尝试公开 PDF");
    private readonly TextBlock downloadHint = Text("", 12, .65);
    private readonly List<Border> cards = [];
    private readonly Border header, footer;
    private JsonArray works = [];
    private JsonObject? failedWork;
    private Uri? fallback;
    private SciHubBrowserPanel? browser;
    private CancellationTokenSource? searchOperation, downloadOperation;
    private readonly CancellationTokenSource lifetime = new();
    private string downloadId = "", savedAddress = "https://sci-hub.ru";
    private int searchRevision, downloadRevision, page;
    private bool hasSearched, hasMore, filling, disposed, savingSettings, canTryPublic, loadingSettings = true;

    internal LiteratureSearchPanel(BackendClient backend, Func<string, Task> imported, Action close)
    {
        this.backend = backend; this.imported = imported; this.close = close;
        HorizontalAlignment = HorizontalAlignment.Stretch; VerticalAlignment = VerticalAlignment.Stretch;
        Children.Add(searchContent); Children.Add(browserHost);
        searchContent.RowDefinitions.Add(new() { Height = GridLength.Auto });
        searchContent.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        searchContent.RowDefinitions.Add(new() { Height = GridLength.Auto });
        searchContent.RowDefinitions.Add(new() { Height = GridLength.Auto });
        var heading = new Grid(); heading.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); heading.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var titles = new StackPanel { Spacing = 5 }; var title = Text("搜索在线文献", 22); title.FontWeight = FontWeights.SemiBold; titles.Children.Add(title);
        titles.Children.Add(Text("用 OpenAlex 搜索文献，选择全文来源后直接阅读。", 12, .65)); heading.Children.Add(titles);
        var closeButton = Button("关闭"); closeButton.Click += (_, _) => { Cancel(); close(); }; Grid.SetColumn(closeButton, 1); heading.Children.Add(closeButton);
        var searchRow = new Grid { ColumnSpacing = 10 }; searchRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); searchRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        searchRow.Children.Add(query); Grid.SetColumn(search, 1); searchRow.Children.Add(search);
        var choices = new Grid { ColumnSpacing = 10 }; choices.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); choices.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); choices.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); choices.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        source.Items.Add(new ComboBoxItem { Content = "Sci-Hub", Tag = "sciHub" }); source.Items.Add(new ComboBoxItem { Content = "公开 PDF", Tag = "openAccess" }); source.SelectedIndex = 0;
        var sourceChoice = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 }; sourceChoice.Children.Add(new TextBlock { Text = "全文来源", FontSize = 12, VerticalAlignment = VerticalAlignment.Center }); sourceChoice.Children.Add(source);
        choices.Children.Add(sourceChoice); Grid.SetColumn(settingsToggle, 1); choices.Children.Add(settingsToggle); Grid.SetColumn(openAccess, 3); choices.Children.Add(openAccess);
        var addressRow = new Grid { ColumnSpacing = 8 }; addressRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); addressRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); addressRow.Children.Add(address);
        var resetAddress = Button("恢复默认"); resetAddress.Click += (_, _) => address.Text = "https://sci-hub.ru"; Grid.SetColumn(resetAddress, 1); addressRow.Children.Add(resetAddress);
        settingsPanel.Children.Add(Text("Sci-Hub 地址", 11, .65)); settingsPanel.Children.Add(addressRow);
        var keyRow = new Grid { ColumnSpacing = 8 }; keyRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); keyRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); keyRow.Children.Add(apiKey);
        var keyLink = Button("获取 Key"); keyLink.Click += async (_, _) => await LaunchAsync("https://openalex.org/settings/api"); Grid.SetColumn(keyLink, 1); keyRow.Children.Add(keyLink);
        settingsPanel.Children.Add(keyRow); settingsPanel.Children.Add(keyState);
        var settingsButtons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 }; settingsButtons.Children.Add(saveSettings); settingsButtons.Children.Add(clearKey); settingsPanel.Children.Add(settingsButtons);
        var notice = new Grid { ColumnSpacing = 8 }; notice.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); notice.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        notice.Children.Add(Text("检索结果不代表所选来源已收录全文。", 11, .6)); var progress = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center }; progress.Children.Add(searching); progress.Children.Add(searchStatus); progress.Children.Add(cancelSearch); Grid.SetColumn(progress, 1); notice.Children.Add(progress);
        var headerBody = new StackPanel { Spacing = 14 }; headerBody.Children.Add(heading); headerBody.Children.Add(searchRow); headerBody.Children.Add(choices); headerBody.Children.Add(settingsPanel); headerBody.Children.Add(notice);
        headerScroll = new ScrollViewer { Content = headerBody, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        header = new Border { Child = headerScroll, Padding = new Thickness(22, 18, 22, 16), BorderThickness = new Thickness(0, 0, 0, 1) }; searchContent.Children.Add(header);
        var center = new Grid(); resultsScroll = new ScrollViewer { Content = results, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }; center.Children.Add(resultsScroll);
        emptyTitle.FontWeight = FontWeights.SemiBold; emptyTitle.TextAlignment = emptyHint.TextAlignment = TextAlignment.Center;
        empty.Children.Add(ReaderIcons.Make("globe", 34)); empty.Children.Add(emptyTitle); empty.Children.Add(emptyHint); empty.Children.Add(directOpen); empty.Children.Add(directCancel); empty.Children.Add(downloadHint); center.Children.Add(empty);
        Grid.SetRow(center, 1); searchContent.Children.Add(center);
        error.Foreground = new SolidColorBrush(Colors.IndianRed); error.MaxLines = 4; error.TextTrimming = TextTrimming.CharacterEllipsis; errorPanel.Children.Add(error);
        for (int i = 0; i < 3; i++) errorActions.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        for (int i = 0; i < 2; i++) errorActions.RowDefinitions.Add(new() { Height = GridLength.Auto });
        errorActions.Children.Add(verify); Grid.SetColumn(tryPublic, 1); errorActions.Children.Add(tryPublic); Grid.SetColumn(external, 2); errorActions.Children.Add(external); errorPanel.Children.Add(errorActions); Grid.SetRow(errorPanel, 2); searchContent.Children.Add(errorPanel);
        footer = new Border { Child = Text("搜索词发送给 OpenAlex；Sci-Hub 获取全文时发送 DOI，公开 PDF 则请求来源链接。PDF 保存到本机后可翻译、批注。网页验证由你自行完成。", 11, .6), Padding = new Thickness(18), BorderThickness = new Thickness(0, 1, 0, 0) }; Grid.SetRow(footer, 3); searchContent.Children.Add(footer);
        Id(query, "LiteratureQuery", "文献搜索词"); Id(search, "LiteratureSearch", "搜索"); Id(source, "LiteratureSource", "全文来源"); Id(openAccess, "LiteratureOpenAccessOnly", "仅开放获取");
        Id(address, "LiteratureSciHubAddress", "Sci-Hub 地址"); Id(apiKey, "LiteratureOpenAlexKey", "OpenAlex API Key"); Id(saveSettings, "LiteratureSaveSettings", "保存检索配置"); Id(verify, "LiteratureVerify", "在 App 中验证并继续"); Id(directOpen, "LiteratureDirectDOI", "直接打开此 DOI 的全文");
        Id(resultsScroll, "LiteratureResults", "文献搜索结果"); Id(loadMore, "LiteratureMore", "加载更多文献"); Id(directCancel, "LiteratureCancelDownload", "取消全文下载"); Id(tryPublic, "LiteratureTryPublicPDF", "尝试公开 PDF");
        query.TextChanged += (_, _) => { if (!filling) ResetSearch(); }; query.KeyDown += async (_, e) => { if (e.Key == global::Windows.System.VirtualKey.Enter) { e.Handled = true; await SearchAsync(false); } };
        search.Click += async (_, _) => await SearchAsync(false); cancelSearch.Click += (_, _) => CancelSearch();
        openAccess.Checked += (_, _) => { if (!filling) ResetSearch(); }; openAccess.Unchecked += (_, _) => { if (!filling) ResetSearch(); };
        source.SelectionChanged += async (_, _) => { if (filling || disposed) return; ClearError(); Render(); await SaveSettingsAsync(false, false); };
        settingsToggle.Click += (_, _) => settingsPanel.Visibility = settingsPanel.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;
        saveSettings.Click += async (_, _) => await SaveSettingsAsync(true, false); clearKey.Click += async (_, _) => await SaveSettingsAsync(false, true);
        directOpen.Click += async (_, _) => { if (DirectWork is { } work) await OpenAsync(work); };
        directCancel.Click += (_, _) => CancelDownload(); loadMore.Click += async (_, _) => await SearchAsync(true);
        tryPublic.Click += async (_, _) => { if (failedWork is not { } work) return; var captured = (JsonObject)work.DeepClone(); source.SelectedIndex = 1; await OpenAsync(captured); };
        verify.Click += async (_, _) => await OpenBrowserAsync(); external.Click += async (_, _) => { if (fallback is not null) await LaunchAsync(fallback.AbsoluteUri); };
        ActualThemeChanged += (_, _) => ApplyTheme(); Loaded += (_, _) => query.Focus(FocusState.Programmatic);
        SizeChanged += (_, _) => UpdateHeaderLimit(); footer.SizeChanged += (_, _) => UpdateHeaderLimit(); errorPanel.SizeChanged += (_, _) => UpdateHeaderLimit();
        Render(); ApplyTheme();
    }

    internal async Task InitializeAsync()
    {
        try
        {
            var settings = await backend.CallAsync("literature_settings", cancellationToken: lifetime.Token);
            if (disposed) return;
            filling = true;
            try
            {
                source.SelectedIndex = String(settings, "source") == "openAccess" ? 1 : 0;
                address.Text = savedAddress = String(settings, "scihub_base_url", "https://sci-hub.ru"); UpdateKeyState(settings);
            }
            finally { filling = false; }
            Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!disposed) ShowError(ex.Message); }
        finally { loadingSettings = false; if (!disposed) { UpdateControls(); query.Focus(FocusState.Programmatic); } }
    }

    private string Source => (source.SelectedItem as ComboBoxItem)?.Tag as string ?? "sciHub";
    private JsonObject? DirectWork => LiteratureAddress.DOI(query.Text) is { } doi ? new JsonObject { ["id"] = "doi:" + doi, ["title"] = doi, ["doi"] = doi, ["authors"] = new JsonArray(), ["landing_url"] = "https://doi.org/" + doi } : null;
    private static string String(JsonNode? item, string name, string fallback = "") => item?[name]?.GetValue<string>() ?? fallback;
    private static Button Button(string title) => new() { Content = title, FontSize = 12, MinHeight = 28, Padding = new Thickness(10, 5, 10, 5) };
    private static TextBlock Text(string text, double size, double opacity = 1) => new() { Text = text, FontSize = size, Opacity = opacity, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
    private static void Id(DependencyObject control, string id, string name) { AutomationProperties.SetAutomationId(control, id); AutomationProperties.SetName(control, name); }

    private async Task SaveSettingsAsync(bool includeInput, bool clearStoredKey)
    {
        if (disposed || savingSettings) return;
        savingSettings = true; UpdateControls();
        try
        {
            var request = new JsonObject { ["source"] = Source, ["scihub_base_url"] = includeInput ? address.Text.Trim() : savedAddress };
            if (clearStoredKey) request["openalex_api_key"] = "";
            else if (includeInput && apiKey.Password.Length > 0) request["openalex_api_key"] = apiKey.Password;
            var saved = await backend.CallAsync("literature_save_settings", request, lifetime.Token);
            if (disposed) return;
            savedAddress = String(saved, "scihub_base_url", savedAddress); address.Text = savedAddress;
            if (includeInput || clearStoredKey) apiKey.Password = "";
            UpdateKeyState(saved); if (includeInput || clearStoredKey) ClearError();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!disposed) ShowError(ex.Message); }
        finally { savingSettings = false; if (!disposed) UpdateControls(); }
    }

    private void UpdateKeyState(JsonNode? settings)
    {
        bool hasKey = settings?["openalex_has_key"]?.GetValue<bool>() == true;
        clearKey.Visibility = hasKey ? Visibility.Visible : Visibility.Collapsed;
        keyState.Text = hasKey ? "已保存 OpenAlex Key（" + String(settings, "credential_storage", "凭据存储") + "）；留空保留现有 Key。" : "匿名检索可直接使用；可选 API Key 可提高额度。";
    }

    private void ResetSearch()
    {
        CancelSearch(); works = []; page = 0; hasSearched = hasMore = false; ClearError(); Render();
    }

    private async Task SearchAsync(bool more)
    {
        if (disposed || loadingSettings || downloadId.Length > 0 || string.IsNullOrWhiteSpace(query.Text)) return;
        if (more && (!hasMore || searchOperation is not null)) return;
        CancelSearch();
        if (!more) { works = []; page = 0; hasSearched = hasMore = false; }
        int revision = ++searchRevision, requestedPage = page + 1;
        var cancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token); searchOperation = cancellation;
        ClearError(); Render();
        try
        {
            var response = await backend.CallAsync("literature_search", new JsonObject { ["query"] = query.Text.Trim(), ["page"] = requestedPage, ["open_access_only"] = openAccess.IsChecked == true }, cancellation.Token);
            if (disposed || revision != searchRevision || cancellation.IsCancellationRequested) return;
            var known = works.OfType<JsonObject>().Select(work => String(work, "doi", String(work, "id"))).ToHashSet(StringComparer.OrdinalIgnoreCase);
            foreach (var work in response?["works"]?.AsArray().OfType<JsonObject>() ?? [])
                if (known.Add(String(work, "doi", String(work, "id")))) works.Add(work.DeepClone());
            page = requestedPage; hasMore = response?["has_more"]?.GetValue<bool>() == true; hasSearched = true;
            Render();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!disposed && revision == searchRevision) ShowError(ex.Message); }
        finally
        {
            if (ReferenceEquals(searchOperation, cancellation)) searchOperation = null;
            cancellation.Dispose(); if (!disposed && revision == searchRevision) UpdateControls();
        }
    }

    private async Task OpenAsync(JsonObject work, string? resolved = null)
    {
        if (disposed || downloadId.Length > 0 || !CanOpen(work)) return;
        CancelSearch(); ClearError();
        int revision = ++downloadRevision; string operationId = Guid.NewGuid().ToString();
        string chosenSource = Source, chosenAddress = address.Text.Trim();
        var cancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token); downloadOperation = cancellation; downloadId = String(work, "id"); Render();
        try
        {
            var request = new JsonObject { ["work"] = work.DeepClone(), ["source"] = chosenSource, ["scihub_base_url"] = chosenAddress, ["operation_id"] = operationId };
            if (resolved is not null) request["resolved_url"] = resolved;
            var result = await backend.CallAsync("literature_open", request, cancellation.Token);
            if (disposed || revision != downloadRevision || cancellation.IsCancellationRequested) return;
            if (result?["requires_browser"]?.GetValue<bool>() == true)
            {
                failedWork = (JsonObject)work.DeepClone(); fallback = LiteratureAddress.PublicURL(String(result, "page_url")); canTryPublic = result?["can_try_open_access"]?.GetValue<bool>() == true;
                ShowError(String(result, "message", "需要打开来源页面完成验证后继续。"));
                return;
            }
            string id = String(result?["document"], "id");
            if (!Guid.TryParse(id, out _)) throw new InvalidOperationException("下载结果未返回有效文档，请重试。");
            await imported(id);
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            if (!disposed && revision == downloadRevision)
            {
                failedWork = (JsonObject)work.DeepClone(); fallback = chosenSource == "sciHub" ? LiteratureAddress.ArticleURL(chosenAddress, String(work, "doi")) : LiteratureAddress.PublicURL(String(work, "landing_url")); canTryPublic = chosenSource == "sciHub" && LiteratureAddress.PublicURL(String(work, "pdf_url")) is not null;
                ShowError(ex.Message);
            }
        }
        finally
        {
            if (cancellation.IsCancellationRequested)
                try { await backend.CallAsync("literature_cleanup", new JsonObject { ["operation_id"] = operationId }); } catch { /* The process has exited; startup cleanup can remove a remaining staging directory. */ }
            if (ReferenceEquals(downloadOperation, cancellation)) downloadOperation = null;
            cancellation.Dispose(); if (!disposed && revision == downloadRevision) { downloadId = ""; Render(); }
        }
    }

    private bool CanOpen(JsonObject work) => Source == "sciHub" ? LiteratureAddress.DOI(String(work, "doi")) is not null : LiteratureAddress.PublicURL(String(work, "pdf_url")) is not null;

    private async Task OpenBrowserAsync()
    {
        if (disposed || browser is not null || failedWork is not { } work || fallback is null) return;
        var captured = (JsonObject)work.DeepClone(); string doi = String(work, "doi");
        browser = new SciHubBrowserPanel(backend.DataRoot, doi, fallback, String(work, "title"), async url =>
        {
            CloseBrowser(); if (!disposed) await OpenAsync(captured, url.AbsoluteUri);
        }, CloseBrowser);
        browserHost.Children.Add(browser); browserHost.Visibility = Visibility.Visible; searchContent.Visibility = Visibility.Collapsed;
        await browser.InitializeAsync();
    }

    private void CloseBrowser()
    {
        browser?.Dispose(); browser = null; browserHost.Children.Clear(); browserHost.Visibility = Visibility.Collapsed; searchContent.Visibility = Visibility.Visible;
    }

    private void Render()
    {
        results.Children.Clear(); cards.Clear();
        int index = 0;
        foreach (var work in works.OfType<JsonObject>())
        {
            var captured = (JsonObject)work.DeepClone(); var content = new StackPanel { Spacing = 8 };
            var title = Text(String(work, "title"), 14); title.FontWeight = FontWeights.SemiBold; content.Children.Add(title);
            var authors = work["authors"]?.AsArray().Select(a => a?.GetValue<string>() ?? "").ToArray() ?? [];
            if (authors.Length > 0) content.Children.Add(Text(string.Join(" · ", authors.Take(5)) + (authors.Length > 5 ? " 等" : ""), 11, .6));
            var details = new[] { String(work, "publication"), work["year"]?.ToString() ?? "" }.Where(s => s.Length > 0);
            content.Children.Add(Text(string.Join(" · ", details), 11, .6));
            if (String(work, "doi") is { Length: > 0 } doi) content.Children.Add(Text("DOI：" + doi, 11, .6));
            var actions = new Grid { ColumnSpacing = 10 }; actions.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); actions.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); actions.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            if (downloadId == String(work, "id"))
            {
                actions.Children.Add(Text("正在下载并校验 PDF…", 11, .6)); var stop = Button("取消下载"); stop.Click += (_, _) => CancelDownload(); Grid.SetColumn(stop, 1); actions.Children.Add(stop);
            }
            else
            {
                actions.Children.Add(Text(CanOpen(work) ? (Source == "sciHub" ? "通过 Sci-Hub 获取全文" : "公开 PDF") : (Source == "sciHub" ? "没有 DOI，可尝试公开 PDF 来源" : "未提供公开 PDF"), 11, .6));
                if (CanOpen(work)) { var open = Button("打开全文"); open.IsEnabled = downloadId.Length == 0; Id(open, "LiteratureOpen-" + index, "打开全文：" + String(work, "title")); open.Click += async (_, _) => await OpenAsync(captured); Grid.SetColumn(open, 1); actions.Children.Add(open); }
            }
            if (LiteratureAddress.PublicURL(String(work, "landing_url")) is { } landing) { var original = Button("原文页面"); original.Click += async (_, _) => await LaunchAsync(landing.AbsoluteUri); Grid.SetColumn(original, 2); actions.Children.Add(original); }
            content.Children.Add(actions); var card = new Border { Child = content, Padding = new Thickness(16), CornerRadius = new CornerRadius(10) }; cards.Add(card); results.Children.Add(card); index++;
        }
        if (hasMore) { loadMore.HorizontalAlignment = HorizontalAlignment.Center; results.Children.Add(loadMore); }
        empty.Visibility = works.Count == 0 ? Visibility.Visible : Visibility.Collapsed; resultsScroll.Visibility = works.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        emptyTitle.Text = hasSearched ? "没有找到匹配的文献" : "从一个标题或 DOI 开始";
        emptyHint.Text = hasSearched ? "试试英文标题、较短的关键词，或取消“仅开放获取”。" : "例如：Ten Simple Rules for Better Figures\n或 10.1371/journal.pcbi.1003833";
        directOpen.Content = downloadId.Length == 0 ? "直接打开此 DOI 的全文" : "正在打开全文…";
        downloadHint.Text = downloadId.Length > 0 ? "正在查找并校验 PDF。" : "无需等待 OpenAlex 返回检索结果。";
        directOpen.Visibility = downloadHint.Visibility = Source == "sciHub" && DirectWork is not null ? Visibility.Visible : Visibility.Collapsed;
        directCancel.Visibility = downloadId.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        ApplyTheme(); UpdateControls();
    }

    private void UpdateControls()
    {
        bool downloading = downloadId.Length > 0, isSearching = searchOperation is not null;
        query.IsEnabled = source.IsEnabled = openAccess.IsEnabled = !downloading && !savingSettings && !loadingSettings;
        search.IsEnabled = !disposed && !loadingSettings && !downloading && !string.IsNullOrWhiteSpace(query.Text);
        directOpen.IsEnabled = !disposed && !downloading; settingsPanel.IsHitTestVisible = !downloading && !savingSettings;
        saveSettings.IsEnabled = clearKey.IsEnabled = address.IsEnabled = apiKey.IsEnabled = !loadingSettings && !downloading && !savingSettings;
        loadMore.IsEnabled = !downloading && !isSearching;
        searching.Visibility = cancelSearch.Visibility = isSearching ? Visibility.Visible : Visibility.Collapsed; searching.IsActive = isSearching;
        searchStatus.Text = isSearching ? "正在搜索…" : hasSearched ? $"已显示 {works.Count} 条结果" : "";
        verify.Visibility = Source == "sciHub" && fallback is not null && failedWork is not null && LiteratureAddress.DOI(String(failedWork, "doi")) is not null ? Visibility.Visible : Visibility.Collapsed;
        external.Visibility = fallback is not null ? Visibility.Visible : Visibility.Collapsed;
        tryPublic.Visibility = canTryPublic ? Visibility.Visible : Visibility.Collapsed; tryPublic.IsEnabled = !downloading;
        verify.IsEnabled = !downloading;
    }

    private void ClearError() { errorPanel.Visibility = Visibility.Collapsed; failedWork = null; fallback = null; canTryPublic = false; UpdateHeaderLimit(); }
    private void ShowError(string text) { error.Text = text; ToolTipService.SetToolTip(error, text); errorPanel.Visibility = Visibility.Visible; UpdateControls(); UpdateHeaderLimit(); }
    private void UpdateHeaderLimit()
    {
        if (ActualHeight <= 0) return;
        bool narrow = ActualWidth < 660;
        Grid.SetRow(external, narrow ? 1 : 0); Grid.SetColumn(external, narrow ? 0 : 2); Grid.SetColumnSpan(external, narrow ? 2 : 1);
        external.HorizontalAlignment = HorizontalAlignment.Left; errorActions.RowSpacing = narrow ? 7 : 0;
        double footerHeight = Math.Max(50, footer.ActualHeight);
        double errorHeight = errorPanel.Visibility == Visibility.Visible ? Math.Max(70, errorPanel.ActualHeight + 14) : 0;
        headerScroll.MaxHeight = Math.Max(130, ActualHeight - footerHeight - errorHeight - 134);
    }
    private async Task LaunchAsync(string address)
    {
        if (LiteratureAddress.PublicURL(address) is not { } url) { ShowError("来源链接不是有效的 HTTPS 公共地址。"); return; }
        try { if (!await global::Windows.System.Launcher.LaunchUriAsync(url)) ShowError("无法打开系统浏览器，请稍后重试。"); }
        catch { if (!disposed) ShowError("无法打开系统浏览器，请稍后重试。"); }
    }

    private void ApplyTheme()
    {
        bool dark = ActualTheme == ElementTheme.Dark;
        Background = new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 31, 31, 31) : global::Windows.UI.Color.FromArgb(255, 245, 245, 245));
        header.BorderBrush = footer.BorderBrush = new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 58, 58, 58) : global::Windows.UI.Color.FromArgb(255, 220, 220, 220));
        foreach (var card in cards) card.Background = new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 41, 41, 41) : Colors.White);
    }

    private void CancelSearch() { searchRevision++; searchOperation?.Cancel(); searchOperation = null; if (!disposed) UpdateControls(); }
    private void CancelDownload() { downloadRevision++; downloadOperation?.Cancel(); downloadId = ""; if (!disposed) Render(); }
    internal void Cancel() { CancelSearch(); CancelDownload(); CloseBrowser(); }
    public void Dispose() { if (disposed) return; disposed = true; Cancel(); lifetime.Cancel(); lifetime.Dispose(); apiKey.Password = ""; }
}

internal static partial class LiteratureAddress
{
    [GeneratedRegex(@"^10\.[0-9]{4,9}/[^\s]+$", RegexOptions.CultureInvariant)] private static partial Regex DoiPattern();
    internal static string? DOI(string value)
    {
        value = value.Trim(); if (value.StartsWith("doi:", StringComparison.OrdinalIgnoreCase)) value = value[4..].Trim();
        if (Uri.TryCreate(value, UriKind.Absolute, out var url) && (url.Scheme is "https" or "http") && url.UserInfo.Length == 0 && new[] { "doi.org", "www.doi.org", "dx.doi.org" }.Contains(url.Host.ToLowerInvariant())) value = Uri.UnescapeDataString(url.AbsolutePath.TrimStart('/'));
        return value.Length <= 512 && !value.Any(char.IsControl) && DoiPattern().IsMatch(value) ? value.ToLowerInvariant() : null;
    }
    internal static Uri? PublicURL(string value)
    {
        if (value.Length > 4096 || !Uri.TryCreate(value, UriKind.Absolute, out var url) || url.Scheme != "https" || url.UserInfo.Length > 0 || url.Port != 443 || url.HostNameType != UriHostNameType.Dns) return null;
        var labels = url.IdnHost.TrimEnd('.').Split('.'); string[] reserved = ["localhost", "local", "internal", "lan", "home", "invalid", "test", "onion"];
        return labels.Length >= 2 && labels.All(s => s.Length > 0) && labels[^1].Any(char.IsLetter) && !reserved.Contains(labels[^1].ToLowerInvariant()) ? url : null;
    }
    internal static Uri? ArticleURL(string root, string doi)
    {
        if (PublicURL(root) is not { } url || DOI(doi) is not { } identifier || url.Query.Length > 0 || url.Fragment.Length > 0) return null;
        return PublicURL(url.AbsoluteUri.TrimEnd('/') + "/" + string.Join("/", identifier.Split('/').Select(Uri.EscapeDataString)));
    }
}
