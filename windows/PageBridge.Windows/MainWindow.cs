using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using global::Windows.Storage.Pickers;
using WinRT.Interop;

namespace PageBridge.Windows;

public sealed partial class MainWindow : Window
{
    private readonly BackendClient backend;
    private readonly Grid root = new();
    private readonly ListView library = new() { SelectionMode = ListViewSelectionMode.Single };
    private readonly TextBox search = new() { PlaceholderText = "搜索文档名称" };
    private readonly ComboBox filter = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TextBlock title = new() { Text = "打开一份 PDF", FontSize = 14, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis };
    private readonly TextBlock subtitle = new() { Text = $"PageBridge {AppVersion.Display} · Windows", Opacity = .65, FontSize = 12 };
    private readonly TextBlock status = new() { Text = "就绪", TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
    private readonly ProgressBar progress = new() { Minimum = 0, Maximum = 100, Width = 170, Visibility = Visibility.Collapsed };
    private readonly ComboBox mode = new() { Width = 100 };
    private readonly ComboBox zoom = new() { Width = 88 };
    private readonly PageNumberInput page = new();
    private readonly TextBlock pageCount = new() { Text = "/ 0 页", VerticalAlignment = VerticalAlignment.Center };
    private readonly ToggleSwitch sync = new() { IsOn = true, OnContent = "", OffContent = "", MinWidth = 0 };
    private ContinuousPdfPane originalPages = null!, translatedPages = null!, activePages = null!;
    private readonly Dictionary<string, int> pendingReading = [];
    private Microsoft.UI.Dispatching.DispatcherQueueTimer? readingTimer;
    private bool bindingPages, savingReading, closingAfterReading, allowClose;
    private readonly SemaphoreSlim readingGate = new(1, 1);
    private ScrollViewer leftScroll = null!;
    private ScrollViewer rightScroll = null!;
    private readonly Grid reader = new() { ColumnSpacing = 2 };
    private Grid leftPane = null!;
    private Grid rightPane = null!;
    private readonly TextBlock emptyTranslation = new() { Text = "完成翻译后在这里阅读译文", HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Opacity = .55 };
    private readonly Button translateButton = new() { Content = "翻译", MinWidth = 90 };
    private readonly List<Button> mutationButtons = [];
    private readonly SemaphoreSlim dialogs = new(1, 1);
    private JsonArray documents = [];
    private JsonArray folders = [];
    private JsonObject? selected;
    private CancellationTokenSource? translationCancellation;
    private bool loading, translationPreparing, windowClosed;
    private WindowSizing? sizing;
    private int renderGeneration, selectionGeneration;
    private double magnification = 1;

    private sealed record DocumentRow(JsonObject Document)
    {
        public string Id => Text(Document, "id");
        public override string ToString() => Text(Document, "name");
    }

    public MainWindow(string? dataRoot = null)
    {
        backend = new BackendClient(dataRoot);
        Title = $"PageBridge {AppVersion.Display} · Windows" + (dataRoot is null ? "" : " · 验证文档库");
        SystemBackdrop = new MicaBackdrop();
        var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "PageBridge.ico");
        if (File.Exists(iconPath)) AppWindow.SetIcon(iconPath);
        BuildMacLayout();
        InitializeDocumentSearch(); InitializeShortcuts();

        search.TextChanged += async (_, _) => { if (loading) return; RefreshList(); await Attempt(ReconcileSelectionAsync); };
        filter.SelectionChanged += async (_, _) => { if (loading) return; RefreshList(); RefreshNavigation(); await Attempt(ReconcileSelectionAsync); };
        library.SelectionChanged += async (_, _) => { if (!loading && library.SelectedItem is ListViewItem { Tag: DocumentRow row }) await Attempt(() => OpenAsync(row.Document)); };
        mode.SelectionChanged += (_, _) => ApplyMode();
        zoom.SelectionChanged += (_, _) => { var text = zoom.SelectedItem?.ToString() ?? ""; if (double.TryParse(text.TrimEnd('%'), out var percent)) SetReaderZoom(percent / 100); else if (text == "适合宽度") SetReaderZoom(1); };
        page.ValueChanged += (_, _) => { if (!loading && selected is not null && !double.IsNaN(page.Value)) JumpToPage((int)page.Value); };
        translateButton.Click += async (_, _) => await Attempt(TranslateAsync);
        foreach (var pane in new[] { originalPages, translatedPages })
        {
            pane.Activated += value => ActivateVariant(value.Variant);
            pane.PositionChanged += ReaderPositionChanged;
            pane.PreviewError += message => status.Text = "预览加载失败：" + message;
        }
        sync.Toggled += (_, _) => { if (sync.IsOn && mode.SelectedIndex == 1) OtherPane(activePages).MoveTo(activePages.Position); UpdatePDFPaneLabels(); };
        Closed += (_, _) => { windowClosed = true; documentSearchCancellation?.Cancel(); readingTimer?.Stop(); assistantPanel.Dispose(); annotationsPanel.Dispose(); originalPages.Clear(); translatedPages.Clear(); backend.Dispose(); };
        AppWindow.Closing += async (_, e) =>
        {
            if (allowClose) return;
            if (translationCancellation is not null || translationPreparing) { e.Cancel = true; status.Text = "请先结束或取消翻译，任务结束后再关闭窗口。"; return; }
            if (pendingReading.Count == 0 && !savingReading) return;
            e.Cancel = true;
            if (closingAfterReading) return;
            closingAfterReading = true; root.IsHitTestVisible = false; readingTimer?.Stop();
            await PersistReadingAsync(); allowClose = true; Close();
        };
        root.Loaded += async (_, _) => await Attempt(async () => { sizing ??= new WindowSizing(this, root.XamlRoot.RasterizationScale); await backend.CallAsync("recover"); await ReloadAsync(); });
        ApplyMode();
    }

    private Button ActionButton(string label, Func<Task> action, bool mutation = false)
    {
        var button = new Button { Content = label, HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 12, MinHeight = 0, Padding = new Thickness(8, 4, 8, 4), CornerRadius = new CornerRadius(5) };
        button.Click += async (_, _) => await Attempt(action);
        if (mutation) mutationButtons.Add(button);
        return button;
    }
    private static string Text(JsonNode? node, string key) => node?[key]?.GetValue<string>() ?? "";
    private static bool Flag(JsonNode? node, string key) => node?[key]?.GetValue<bool>() ?? false;
    private JsonObject DocArgs() => new() { ["document_id"] = Text(selected, "id") };
    private async Task<ContentDialogResult> ShowDialogAsync(ContentDialog dialog)
    {
        await dialogs.WaitAsync();
        try { if (windowClosed) return ContentDialogResult.None; dialog.RequestedTheme = root.ActualTheme; return await dialog.ShowAsync(); }
        catch (Exception) when (windowClosed) { return ContentDialogResult.None; }
        finally { dialogs.Release(); }
    }
    private async Task Attempt(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error)
        {
            if (windowClosed) return;
            status.Text = error.Message;
            await ShowDialogAsync(new ContentDialog { XamlRoot = root.XamlRoot, Title = "无法完成操作", Content = error.Message, CloseButtonText = "好" });
        }
    }
    private async Task ReloadAsync(string? selectId = null)
    {
        documents = (await backend.CallAsync("list"))?.AsArray() ?? [];
        folders = (await backend.CallAsync("folders"))?.AsArray() ?? [];
        var previousFilter = (filter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        loading = true; filter.Items.Clear();
        foreach (var choice in new[] { ("全部文档", "all"), ("未分类", "unfiled"), ("收藏", "favorites"), ("最近", "recent"), ("回收站", "trash") }) filter.Items.Add(new ComboBoxItem { Content = choice.Item1, Tag = choice.Item2 });
        foreach (var folder in folders) filter.Items.Add(new ComboBoxItem { Content = Text(folder, "name"), Tag = Text(folder, "id") });
        filter.SelectedItem = filter.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == previousFilter) ?? filter.Items[0];
        loading = false; RefreshList(); RefreshNavigation();
        if (selectId is not null)
        {
            var doc = documents.OfType<JsonObject>().FirstOrDefault(x => Text(x, "id") == selectId);
            if (doc is not null && !Flag(doc, "trashed")) await OpenAsync(doc);
            else await ReconcileSelectionAsync();
        }
        else if (selected is null && documents.OfType<JsonObject>().FirstOrDefault(x => !Flag(x, "trashed")) is { } first) await OpenAsync(first);
    }
    private void RefreshList()
    {
        if (loading) return;
        var choice = (filter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        var query = search.Text.Trim(); loading = true; library.Items.Clear();
        foreach (var doc in SortedDocuments())
        {
            if (Flag(doc, "trashed") != (choice == "trash")) continue;
            if (choice == "favorites" && !Flag(doc, "favorite")) continue;
            if (choice == "recent" && DateTimeOffset.UtcNow.ToUnixTimeSeconds() - Number(doc, "last_opened") > 30 * 86400) continue;
            if (choice == "unfiled" && Text(doc, "folder_id") != "") continue;
            if (!new[] { "all", "trash", "favorites", "unfiled", "recent" }.Contains(choice) && Text(doc, "folder_id") != choice) continue;
            if (!Text(doc, "name").Contains(query, StringComparison.CurrentCultureIgnoreCase)) continue;
            var item = new ListViewItem { Content = DocumentCard(doc), Tag = new DocumentRow(doc), HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(4, 3, 4, 3), CornerRadius = new CornerRadius(7), Margin = new Thickness(0, 2, 0, 2) };
            item.Resources["ListViewItemBackgroundSelected"] = new SolidColorBrush(global::Windows.UI.Color.FromArgb(36, 10, 132, 255));
            item.Resources["ListViewItemBackgroundSelectedPointerOver"] = new SolidColorBrush(global::Windows.UI.Color.FromArgb(48, 10, 132, 255));
            item.Resources["ListViewItemSelectionIndicatorBrush"] = Clear;
            item.Resources["ListViewItemSelectionIndicatorBrushPointerOver"] = Clear;
            item.Resources["ListViewItemSelectionIndicatorBrushPressed"] = Clear;
            item.ContextFlyout = DocumentMenu(doc);
            library.Items.Add(item);
            if (Text(doc, "id") == Text(selected, "id")) library.SelectedItem = item;
        }
        loading = false;
    }
    private static string StatusName(string value) => value switch { "completed" => "已完成", "running" => "翻译中", "failed" => "翻译失败", "cancelled" => "已取消", _ => "未翻译" };
    private async Task OpenAsync(JsonObject doc)
    {
        int generation = ++selectionGeneration;
        if (!await CloseSettingsAsync() || generation != selectionGeneration) return;
        ResetDocumentSearch(); assistantPanel.Cancel(); annotationHashes.Clear(); annotationsByVariant.Clear(); navigationIdentity = ""; navigationGeneration++;
        await annotationsPanel.BindAsync("", false); await assistantPanel.BindAsync("", "");
        if (generation != selectionGeneration) return;
        bindingPages = true; selected = doc; loading = true; title.Text = Path.GetFileNameWithoutExtension(Text(doc, "name")); subtitle.Text = $"{LanguageName(Text(doc, "source_language"))} → {LanguageName(Text(doc, "target_language"))} · {doc["page_count"]} 页";
        if (mode.SelectedIndex == 2 && Text(doc, "translated_path") == "") mode.SelectedIndex = 0;
        page.Maximum = Math.Max(1, doc["page_count"]?.GetValue<int>() ?? 1); page.Value = Math.Clamp(doc["current_page"]?.GetValue<int>() ?? 1, 1, page.Maximum);
        pageCount.Text = $"/ {page.Maximum} 页"; loading = false; RefreshList(); UpdateDocumentState(); await RenderAsync();
        if (generation == selectionGeneration) await LoadReadingAsync();
    }
    private async Task RenderAsync()
    {
        if (selected is null) return;
        int generation = ++renderGeneration; var doc = selected; string id = Text(doc, "id");
        int targetPage = Math.Clamp((int)page.Value, 1, (int)page.Maximum);
        bindingPages = true;
        try
        {
            // Each completed translation has its own immutable result path.
            string revision = Text(doc, "translated_path");
            await originalPages.BindAsync(id, "original", id + "/original/" + Text(doc, "input_path"), targetPage);
            if (generation != renderGeneration) return;
            targetPage = Math.Clamp((int)page.Value, 1, (int)page.Maximum);
            bool hasTranslation = Text(doc, "translated_path") != "";
            if (hasTranslation) await translatedPages.BindAsync(id, "translated", id + "/translated/" + revision, targetPage);
            else translatedPages.Clear();
            if (generation != renderGeneration) return;
            emptyTranslation.Visibility = hasTranslation ? Visibility.Collapsed : Visibility.Visible;
        }
        finally { if (generation == renderGeneration) bindingPages = false; }
        if (generation == renderGeneration) RememberReading(id, Math.Clamp((int)page.Value, 1, (int)page.Maximum));
    }
    private void ApplyMode()
    {
        ResetDocumentSearch();
        UpdateModeButtons();
        leftPane.Visibility = mode.SelectedIndex == 2 ? Visibility.Collapsed : Visibility.Visible;
        rightPane.Visibility = mode.SelectedIndex == 0 ? Visibility.Collapsed : Visibility.Visible;
        originalPages.SetActive(mode.SelectedIndex != 2); translatedPages.SetActive(mode.SelectedIndex != 0);
        reader.ColumnDefinitions[0].Width = mode.SelectedIndex == 2 ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        reader.ColumnDefinitions[1].Width = mode.SelectedIndex == 0 ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        activePages = mode.SelectedIndex == 2 ? translatedPages : originalPages;
        if (!bindingPages && selected is not null && !double.IsNaN(page.Value)) { ActivateVariant(ActiveVariant); JumpToPage((int)page.Value); }
    }
    private ContinuousPdfPane OtherPane(ContinuousPdfPane pane) => ReferenceEquals(pane, originalPages) ? translatedPages : originalPages;
    private void JumpToPage(int value)
    {
        value = Math.Clamp(value, 1, (int)page.Maximum);
        activePages.Jump(value);
        if (sync.IsOn && mode.SelectedIndex == 1) OtherPane(activePages).Jump(value);
        if (selected is not null) RememberReading(Text(selected, "id"), value);
        UpdateNavigationPage();
    }
    private void ReaderPositionChanged(ContinuousPdfPane pane)
    {
        if (bindingPages || selected is null || !ReferenceEquals(activePages, pane)) return;
        if (sync.IsOn && mode.SelectedIndex == 1) OtherPane(pane).MoveTo(pane.Position);
        int value = Math.Clamp(pane.CurrentPage, 1, (int)page.Maximum);
        if ((int)page.Value == value) return;
        loading = true; page.Value = value; loading = false;
        RememberReading(Text(selected, "id"), value);
        UpdateNavigationPage();
    }
    private void RememberReading(string id, int value)
    {
        foreach (var document in documents.OfType<JsonObject>().Where(d => Text(d, "id") == id)) document["current_page"] = value;
        if (selected is not null && Text(selected, "id") == id) selected["current_page"] = value;
        pendingReading[id] = value;
        if (readingTimer is null)
        {
            readingTimer = DispatcherQueue.CreateTimer(); readingTimer.IsRepeating = true; readingTimer.Interval = TimeSpan.FromMilliseconds(350);
            readingTimer.Tick += async (_, _) => { if (!savingReading) await PersistReadingAsync(); };
        }
        readingTimer.Start();
    }
    private async Task PersistReadingAsync()
    {
        if (windowClosed) return;
        await readingGate.WaitAsync();
        if (windowClosed) { readingGate.Release(); return; }
        savingReading = true;
        try
        {
            foreach (var (id, value) in pendingReading.ToArray())
            {
                var reading = await backend.CallAsync("reading", new JsonObject { ["document_id"] = id, ["page"] = value });
                if (pendingReading.TryGetValue(id, out int latest) && latest == value) pendingReading.Remove(id);
                foreach (var document in documents.OfType<JsonObject>().Where(d => Text(d, "id") == id))
                    if (reading?["last_opened"] is JsonNode timestamp) document["last_opened"] = timestamp.DeepClone();
            }
            if (readingTimer is not null) readingTimer.Interval = TimeSpan.FromMilliseconds(350);
        }
        catch (Exception error)
        {
            if (!windowClosed) status.Text = "阅读位置保存失败：" + error.Message;
            if (readingTimer is not null) readingTimer.Interval = TimeSpan.FromSeconds(2);
        }
        finally { savingReading = false; readingGate.Release(); if (pendingReading.Count == 0) readingTimer?.Stop(); }
    }
    private Task ChangePage(int delta) { page.Value = Math.Clamp(page.Value + delta, 1, page.Maximum); return Task.CompletedTask; }
    private async Task ImportAsync()
    {
        var picker = new FileOpenPicker(); picker.FileTypeFilter.Add(".pdf"); InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var files = await picker.PickMultipleFilesAsync();
        await ImportPathsAsync(files.Select(file => file.Path));
    }
    private async Task ExportAsync(string kind)
    {
        if (selected is null) return;
        var documentId = Text(selected, "id");
        var picker = new FileSavePicker { SuggestedFileName = Path.GetFileNameWithoutExtension(Text(selected, "name")) + "-" + kind };
        picker.FileTypeChoices.Add("PDF 文档", new List<string> { ".pdf" }); InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var file = await picker.PickSaveFileAsync(); if (file is null) return;
        var args = new JsonObject { ["document_id"] = documentId, ["kind"] = kind, ["destination"] = file.Path }; await backend.CallAsync("export", args); status.Text = "已导出：" + file.Path;
    }
    private async Task<string?> PromptAsync(string heading, string value = "")
    {
        var input = new TextBox { Text = value, MinWidth = 320, MaxLength = 100 };
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = heading, Content = input, PrimaryButtonText = "保存", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary };
        return await ShowDialogAsync(dialog) == ContentDialogResult.Primary ? input.Text.Trim() : null;
    }
    private async Task CreateFolderAsync() { if (await PromptAsync("新建文件夹") is { Length: > 0 } name) { await backend.CallAsync("create_folder", new JsonObject { ["name"] = name }); await ReloadAsync(); } }
    private async Task RenameAsync() { if (selected is not null && await PromptAsync("重命名文档", Text(selected, "name")) is { Length: > 0 } name) { var args = DocArgs(); args["name"] = name; await backend.CallAsync("rename", args); await ReloadAsync(Text(selected, "id")); } }
    private async Task FavoriteAsync() { if (selected is null) return; var args = DocArgs(); args["value"] = !Flag(selected, "favorite"); await backend.CallAsync("favorite", args); await ReloadAsync(Text(selected, "id")); }
    private async Task TrashAsync() { if (selected is null) return; var args = DocArgs(); args["value"] = !Flag(selected, "trashed"); await backend.CallAsync("trash", args); await ReloadAsync(Text(selected, "id")); }
    private async Task MoveAsync()
    {
        if (selected is null) return;
        var choices = new ComboBox { MinWidth = 320 }; choices.Items.Add(new ComboBoxItem { Content = "未分类", Tag = "" });
        foreach (var folder in folders) choices.Items.Add(new ComboBoxItem { Content = Text(folder, "name"), Tag = Text(folder, "id") }); choices.SelectedIndex = 0;
        if (await ShowDialogAsync(new ContentDialog { XamlRoot = root.XamlRoot, Title = "移动到文件夹", Content = choices, PrimaryButtonText = "移动", CloseButtonText = "取消" }) == ContentDialogResult.Primary)
        { var args = DocArgs(); var id = (string)((ComboBoxItem)choices.SelectedItem).Tag; args["folder_id"] = id == "" ? null : id; await backend.CallAsync("move", args); await ReloadAsync(Text(selected, "id")); }
    }
    private async Task TranslateAsync()
    {
        if (translationCancellation is not null) { translationCancellation.Cancel(); status.Text = "正在取消并清理任务…"; return; }
        if (translationPreparing) return;
        if (selected is null) return;
        if (Flag(selected, "trashed")) throw new InvalidOperationException("请先从回收站恢复文档。");
        translationPreparing = true;
        try
        {
            var services = await backend.CallAsync("profiles"); var serviceId = Text(services, "active_id");
            if (serviceId == "") { await SettingsAsync(); return; }
            var docId = Text(selected, "id"); var name = Text(selected, "name");
            var choice = await TranslationDialogAsync(name, services);
            if (choice is null) return;
            using var cancellation = new CancellationTokenSource();
            translationCancellation = cancellation; translationPreparing = false;
            translateButton.Content = "取消翻译"; progress.Visibility = Visibility.Visible; progress.IsIndeterminate = true;
            foreach (var document in documents.OfType<JsonObject>().Where(d => Text(d, "id") == docId)) document["status"] = "running";
            if (selected is not null && Text(selected, "id") == docId) { selected["status"] = "running"; documentStatus.Text = "翻译中"; }
            RefreshList();
            foreach (var button in mutationButtons) button.IsEnabled = false;
            try
            {
                await backend.TranslateAsync(docId, serviceId, choice.Source, choice.Target, choice.Pages, evt => DispatcherQueue.TryEnqueue(() =>
                {
                    if (!ReferenceEquals(translationCancellation, cancellation)) return;
                    if (evt["progress"] is JsonValue value && value.TryGetValue<double>(out var amount)) { progress.IsIndeterminate = false; progress.Value = amount * 100; }
                    var message = Text(evt, "message"); if (message != "") status.Text = message;
                }), cancellation.Token);
                status.Text = cancellation.IsCancellationRequested ? "已取消，保留上次译文" : "翻译完成";
            }
            catch (OperationCanceledException) { status.Text = "已取消，保留上次译文"; }
            finally
            {
                translationPreparing = true;
                translationCancellation = null; translateButton.Content = "翻译"; progress.Visibility = Visibility.Collapsed;
                foreach (var button in mutationButtons) button.IsEnabled = true;
                await ReloadAsync(Text(selected, "id")); if (docId == Text(selected, "id") && Text(selected, "translated_path") != "") mode.SelectedIndex = 1;
            }
        }
        finally { translationPreparing = false; }
    }

}
