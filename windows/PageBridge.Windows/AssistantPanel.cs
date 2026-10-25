using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using global::Windows.ApplicationModel.DataTransfer;

namespace PageBridge.Windows;

/// <summary>Document-scoped assistant using saved services and bounded local evidence.</summary>
internal sealed class AssistantPanel : Grid, IDisposable
{
    private readonly BackendClient backend;
    private readonly Action<int, string> navigate;
    private readonly Func<int> currentPage;
    private readonly Func<int> selectionPage;
    private readonly Func<string> currentVariant, selectedText;
    private readonly Func<string> selectionVariant;
    private readonly Func<string, Task>? saveNote;
    private readonly Func<ContentDialog, Task<ContentDialogResult>> showDialog;
    private readonly TextBlock documentName = Text("", 11, .6);
    private readonly ComboBox scope = new() { HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 12, MinWidth = 0 };
    private readonly ComboBox models = new() { HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 12, MinWidth = 0 };
    private string modelDescription = "", destinationHint = "", contextHint = "";
    private readonly TextBlock selectionPreview = Text("", 10, .6);
    private readonly TextBlock error = Text("", 11);
    private readonly TextBlock progress = Text("", 11, .65);
    private readonly TextBox question = new() { PlaceholderText = "询问文档内容、条款或操作步骤…", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 64, MaxHeight = 112, MaxLength = 2000, FontSize = 12 };
    private readonly TextBlock counter = Text("0/2,000", 10, .55);
    private readonly Button send = new() { Content = "发送", MinWidth = 64, MinHeight = 28, FontSize = 12 };
    private readonly Button cancel = new() { Content = "取消", MinHeight = 28, FontSize = 12, Visibility = Visibility.Collapsed };
    private readonly Button clear;
    private readonly StackPanel messagesPanel = new() { Spacing = 12, Margin = new Thickness(12) };
    private readonly ScrollViewer scroll;
    private readonly List<Border> cards = [];
    private readonly List<MenuFlyoutItem> quickItems = [];
    private readonly DropDownButton quickQuestions = new() { Content = "快捷提问", MinHeight = 28, FontSize = 12, Padding = new Thickness(8, 3, 8, 3) };
    private readonly ScrollViewer feedback;
    private readonly Border header, composer;
    private JsonArray messages = [];
    private string documentId = "", serviceId = "";
    private int generation, messagesLayoutVersion;
    private bool busy, loadingModels, disposed, clearDialogOpen;
    private CancellationTokenSource? pending;
    private CancellationTokenSource binding = new();
    internal bool IsBusy => busy || pending is not null;
    internal bool HasDraft => !string.IsNullOrWhiteSpace(question.Text);

    internal AssistantPanel(BackendClient backend, Action<int, string> navigate, Func<int> currentPage,
        Func<string> currentVariant, Func<string> selectedText, Func<string, Task>? saveNote = null,
        Func<ContentDialog, Task<ContentDialogResult>>? showDialog = null,
        Func<int>? selectionPage = null, Func<string>? selectionVariant = null)
    {
        this.backend = backend; this.navigate = navigate; this.currentPage = currentPage;
        this.currentVariant = currentVariant; this.selectedText = selectedText; this.saveNote = saveNote;
        this.selectionPage = selectionPage ?? currentPage; this.selectionVariant = selectionVariant ?? currentVariant;
        this.showDialog = showDialog ?? (async dialog => await dialog.ShowAsync());
        HorizontalAlignment = HorizontalAlignment.Stretch; VerticalAlignment = VerticalAlignment.Stretch;
        RowDefinitions.Add(new() { Height = GridLength.Auto });
        RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        RowDefinitions.Add(new() { Height = GridLength.Auto });
        var headerContent = new StackPanel { Spacing = 3 };
        var heading = new Grid(); heading.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); heading.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        heading.Children.Add(new TextBlock { Text = "文档助手", FontSize = 14, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        clear = IconButton("trash", "清空当前文档对话", ClearAsync); Grid.SetColumn(clear, 1); heading.Children.Add(clear); headerContent.Children.Add(heading);
        documentName.MaxLines = 1; documentName.TextTrimming = TextTrimming.CharacterEllipsis; headerContent.Children.Add(documentName);
        header = new Border { Child = headerContent, Padding = new Thickness(12, 9, 12, 9), BorderThickness = new Thickness(0, 0, 0, 1) }; Children.Add(header);
        AutomationProperties.SetAutomationId(header, "AssistantHeader");
        scroll = new ScrollViewer { Content = messagesPanel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        AutomationProperties.SetAutomationId(scroll, "AssistantMessages");
        AutomationProperties.SetName(scroll, "助手对话");
        Grid.SetRow(scroll, 1); Children.Add(scroll);

        var composerContent = new StackPanel { Spacing = 7 };
        var selectors = new Grid { ColumnSpacing = 6 };
        selectors.ColumnDefinitions.Add(new() { Width = new GridLength(1.45, GridUnitType.Star) });
        selectors.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        models.PlaceholderText = "请先配置模型"; models.MinHeight = 28;
        models.Padding = new Thickness(8, 3, 28, 3); selectors.Children.Add(models);
        foreach (var item in new[] { ("文档问答", "paper"), ("当前页", "currentPage"), ("选中文字", "selection"), ("自由提问", "freeQuestion") })
            scope.Items.Add(new ComboBoxItem { Content = item.Item1, Tag = item.Item2 });
        scope.SelectedIndex = 0; scope.MinHeight = 28; scope.Padding = new Thickness(8, 3, 28, 3);
        Grid.SetColumn(scope, 1); selectors.Children.Add(scope); composerContent.Children.Add(selectors);
        AutomationProperties.SetAutomationId(scope, "AssistantScope"); AutomationProperties.SetName(scope, "提问范围");
        AutomationProperties.SetAutomationId(models, "AssistantModel"); AutomationProperties.SetName(models, "助手模型");
        selectionPreview.MaxLines = 1; selectionPreview.TextTrimming = TextTrimming.CharacterEllipsis; composerContent.Children.Add(selectionPreview);
        composerContent.Children.Add(question);
        var feedbackContent = new StackPanel { Spacing = 3 }; feedbackContent.Children.Add(error); feedbackContent.Children.Add(progress);
        feedback = new ScrollViewer { Content = feedbackContent, MaxHeight = 56, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Visibility = Visibility.Collapsed };
        AutomationProperties.SetAutomationId(feedback, "AssistantStatus"); composerContent.Children.Add(feedback);
        var quickMenu = new MenuFlyout();
        foreach (var item in new[] {
            ("文档概述", "请根据提供的内容，概述文档的主题、用途和主要内容，并说明无法从现有片段确认的部分。"),
            ("提炼要点", "请提炼文档的重要信息、结论、要求或数据，并区分原文明示的信息与推断。"),
            ("操作与注意事项", "请提取文档中明确的操作步骤、前提条件和注意事项。如果没有相关内容，请直接说明。"),
            ("解释选文／公式", "请解释当前页或选中段落的含义；如有公式，请解释变量、假设和公式作用，不要猜测缺失符号。") })
        {
            var choice = new MenuFlyoutItem { Text = item.Item1 };
            string prompt = item.Item2; choice.Click += async (_, _) => await SendAsync(prompt);
            quickItems.Add(choice); quickMenu.Items.Add(choice);
        }
        quickQuestions.Flyout = quickMenu;
        AutomationProperties.SetAutomationId(quickQuestions, "AssistantQuickQuestions");
        AutomationProperties.SetName(quickQuestions, "快捷提问");
        ToolTipService.SetToolTip(quickQuestions, "选择问题后，立即使用当前模型和范围发送。");
        AutomationProperties.SetHelpText(quickQuestions, "选择问题后立即发送，不会先填入输入框。");
        var bottom = new Grid { ColumnSpacing = 8 };
        bottom.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        bottom.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        bottom.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); bottom.Children.Add(quickQuestions);
        counter.VerticalAlignment = VerticalAlignment.Center; counter.HorizontalAlignment = HorizontalAlignment.Right;
        Grid.SetColumn(counter, 1); bottom.Children.Add(counter);
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 }; buttons.Children.Add(cancel); buttons.Children.Add(send); Grid.SetColumn(buttons, 2); bottom.Children.Add(buttons); composerContent.Children.Add(bottom);
        composer = new Border { Child = composerContent, Padding = new Thickness(12, 10, 12, 10), BorderThickness = new Thickness(0, 1, 0, 0) }; Grid.SetRow(composer, 2); Children.Add(composer);
        AutomationProperties.SetAutomationId(composer, "AssistantComposer");
        AutomationProperties.SetAutomationId(question, "AssistantQuestion"); AutomationProperties.SetName(question, "助手问题");
        ToolTipService.SetToolTip(question, "Ctrl+Enter 发送。回答可能出错，请核对引用片段；对话保存在本机，最多保留最近 40 条。");
        AutomationProperties.SetAutomationId(send, "AssistantSend"); AutomationProperties.SetAutomationId(cancel, "AssistantCancel");
        question.TextChanged += (_, _) => { counter.Text = $"{question.Text.Length:N0}/2,000"; UpdateControls(); };
        question.KeyDown += async (_, args) =>
        {
            if (args.Key == global::Windows.System.VirtualKey.Enter &&
                Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(global::Windows.System.VirtualKey.Control).HasFlag(global::Windows.UI.Core.CoreVirtualKeyStates.Down))
            { args.Handled = true; await SendAsync(question.Text); }
        };
        scope.SelectionChanged += (_, _) => RefreshContext();
        models.SelectionChanged += (_, _) =>
        {
            if (loadingModels) return;
            Cancel(); serviceId = SelectedProfile?["id"]?.GetValue<string>() ?? "";
            UpdateModelDescription(); UpdateControls();
        };
        send.Click += async (_, _) => await SendAsync(question.Text);
        cancel.Click += (_, _) => Cancel();
        ActualThemeChanged += (_, _) => ApplyTheme();
        Loaded += (_, _) => ApplyTheme();
        error.Foreground = new SolidColorBrush(Colors.Orange); error.Visibility = Visibility.Collapsed;
        ApplyTheme(); RenderMessages(); RefreshContext();
    }

    private static TextBlock Text(string value, double size, double opacity = 1) => new() { Text = value, FontSize = size, Opacity = opacity, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
    private string Mode => (scope.SelectedItem as ComboBoxItem)?.Tag as string ?? "paper";
    private JsonObject? SelectedProfile => (models.SelectedItem as ComboBoxItem)?.Tag as JsonObject;
    private static string String(JsonNode? node, string key) => node?[key]?.GetValue<string>() ?? "";
    private Button IconButton(string icon, string label, Func<Task> action)
    {
        var button = new Button { Content = ReaderIcons.Make(icon, 14), Padding = new Thickness(4), MinHeight = 24, MinWidth = 24, BorderThickness = new Thickness(0), Background = new SolidColorBrush(Colors.Transparent) };
        AutomationProperties.SetName(button, label); ToolTipService.SetToolTip(button, label);
        button.Click += async (_, _) => { try { await action(); } catch (Exception ex) { ShowError(ex.Message); } finally { UpdateFeedback(); } };
        return button;
    }

    internal async Task BindAsync(string id, string title)
    {
        if (disposed) return;
        if (documentId != id)
        {
            Cancel(); generation++; binding.Cancel(); binding.Dispose(); binding = new();
            ResetModelDisplay();
            documentId = id; question.Text = ""; messages = []; serviceId = ""; busy = false;
            error.Visibility = Visibility.Collapsed; progress.Text = ""; RenderMessages();
        }
        documentName.Text = title; ToolTipService.SetToolTip(documentName, title); RefreshContext();
        await RefreshAsync();
    }

    internal async Task RefreshAsync()
    {
        if (disposed || documentId.Length == 0) return;
        int version = generation; string id = documentId;
        ResetModelDisplay(); UpdateControls();
        try
        {
            var history = await backend.CallAsync("assistant_history", new JsonObject { ["document_id"] = id }, binding.Token);
            if (disposed || generation != version) return;
            if (!busy) { messages = history?["messages"]?.DeepClone() as JsonArray ?? []; RenderMessages(); }
            if (serviceId.Length == 0) serviceId = String(history, "service_id");
            var configured = await backend.CallAsync("profiles", cancellationToken: binding.Token);
            if (disposed || generation != version) return;
            loadingModels = true;
            try
            {
                models.Items.Clear();
                foreach (var profile in configured?["profiles"]?.AsArray().OfType<JsonObject>() ?? [])
                {
                    string label = $"{String(profile, "name")} · {String(profile, "model")}";
                    var item = new ComboBoxItem { Content = new TextBlock { Text = label, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 }, Tag = profile.DeepClone() };
                    AutomationProperties.SetName(item, label); ToolTipService.SetToolTip(item, label); models.Items.Add(item);
                }
                if (serviceId.Length == 0) serviceId = String(configured, "active_id");
                models.SelectedItem = models.Items.OfType<ComboBoxItem>().FirstOrDefault(x => String((JsonNode?)x.Tag, "id") == serviceId) ?? models.Items.OfType<ComboBoxItem>().FirstOrDefault();
                serviceId = String(SelectedProfile, "id");
            }
            finally { loadingModels = false; }
            UpdateModelDescription(); RefreshContext();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!disposed && generation == version) ShowError(ex.Message); }
        finally { if (!disposed && generation == version) { models.PlaceholderText = models.Items.Count == 0 ? "请先配置模型" : "选择模型"; models.IsEnabled = models.Items.Count > 0; UpdateModelDescription(); UpdateControls(); } }
    }

    internal void RefreshContext()
    {
        contextHint = Mode switch
        {
            "currentPage" => $"发送当前第 {currentPage() + 1} 页的有界片段（最多 2,400 字符），附带最近对话。",
            "selection" => "只发送选中文字（最多 6,000 字符）与最近对话，不附带整页。",
            "freeQuestion" => "不提取 PDF；仅发送问题与最近对话，对话可能含此前文档内容。",
            _ => "本机检索最多 500 页、24 万字符，发送最多 6 页原文片段、12,000 字符与最近对话。模型未通读全文。"
        };
        ToolTipService.SetToolTip(scope, "提问范围\n" + contextHint); AutomationProperties.SetHelpText(scope, contextHint);
        string selection = selectedText(); selectionPreview.Text = $"已选择 {selection.Length:N0} 字";
        ToolTipService.SetToolTip(selectionPreview, selection.Length > 600 ? selection[..600] + "…" : selection);
        selectionPreview.Visibility = Mode == "selection" && selection.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        UpdateControls();
    }

    internal void UseSelectionContext()
    {
        if (disposed) return;
        scope.SelectedItem = scope.Items.OfType<ComboBoxItem>().First(item => (string)item.Tag == "selection");
        RefreshContext(); question.Focus(FocusState.Programmatic);
    }

    private void ResetModelDisplay()
    {
        loadingModels = true;
        try { models.Items.Clear(); models.PlaceholderText = "加载模型…"; models.IsEnabled = false; }
        finally { loadingModels = false; }
        ToolTipService.SetToolTip(models, "正在加载已配置模型…");
    }

    private void UpdateModelDescription()
    {
        var profile = SelectedProfile;
        modelDescription = profile is null ? "请在翻译服务设置中添加模型。" : String(profile, "name") + " · " + String(profile, "model");
        if (profile is null) destinationHint = "";
        else if (String(profile, "kind") == "ollama") destinationHint = "使用本机 Ollama；模型不随应用打包。";
        else destinationHint = Uri.TryCreate(String(profile, "baseURL"), UriKind.Absolute, out var address)
            ? $"点击发送时，所选片段将发送至 {address.Host}。" : "";
        string detail = modelDescription + (destinationHint.Length > 0 ? "\n" + destinationHint : "");
        ToolTipService.SetToolTip(models, detail); AutomationProperties.SetHelpText(models, detail);
    }

    private void UpdateFeedback()
    {
        progress.Visibility = progress.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        feedback.Visibility = error.Visibility == Visibility.Visible || progress.Visibility == Visibility.Visible ? Visibility.Visible : Visibility.Collapsed;
    }

    private void UpdateControls()
    {
        bool ready = !disposed && documentId.Length > 0 && serviceId.Length > 0 && models.IsEnabled && SelectedProfile is not null && !busy && (Mode != "selection" || !string.IsNullOrWhiteSpace(selectedText()));
        send.IsEnabled = ready && !string.IsNullOrWhiteSpace(question.Text);
        quickQuestions.IsEnabled = ready;
        foreach (var item in quickItems) item.IsEnabled = ready;
        cancel.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        clear.IsEnabled = documentId.Length > 0 && !clearDialogOpen;
        UpdateFeedback();
    }

    private async Task SendAsync(string text)
    {
        text = text.Trim();
        if (disposed || busy || text.Length is < 1 or > 2000 || SelectedProfile is null || !models.IsEnabled || serviceId.Length == 0 || documentId.Length == 0) return;
        if (Mode == "selection" && string.IsNullOrWhiteSpace(selectedText())) { ShowError("请先在 PDF 中选择文字。"); return; }
        int version = generation; string id = documentId, capturedService = serviceId;
        var tokenSource = new CancellationTokenSource(); pending = tokenSource; busy = true; error.Visibility = Visibility.Collapsed;
        string selection = selectedText();
        var args = new JsonObject { ["document_id"] = id, ["service_id"] = capturedService, ["mode"] = Mode,
            ["page"] = Mode == "selection" ? selectionPage() : currentPage(), ["variant"] = Mode == "selection" ? selectionVariant() : currentVariant(), ["selection"] = selection.Length > 6000 ? selection[..6000] : selection,
            ["selection_truncated"] = selection.Length > 6000, ["question"] = text };
        question.Text = ""; progress.Text = "正在选取文档片段并等待模型回答…";
        var pendingMessage = new JsonObject { ["role"] = "user", ["text"] = text };
        messages.Add(pendingMessage); RenderMessages(); UpdateControls();
        try
        {
            var answer = await backend.CallAsync("assistant_ask", args, tokenSource.Token);
            if (disposed || version != generation || tokenSource.IsCancellationRequested) return;
            messages = answer?["messages"]?.DeepClone() as JsonArray ?? [];
            if (answer?["context"] is JsonObject context)
            {
                contextHint = String(context, "scope");
                ToolTipService.SetToolTip(scope, "提问范围\n" + contextHint); AutomationProperties.SetHelpText(scope, contextHint);
            }
            progress.Text = ""; RenderMessages();
        }
        catch (OperationCanceledException) { if (!disposed && generation == version) { progress.Text = "已取消。已发送的问题可在对话中保留。"; if (question.Text.Length == 0) question.Text = text; } }
        catch (Exception ex) { if (!disposed && generation == version) { progress.Text = ""; if (question.Text.Length == 0) question.Text = text; ShowError(ex.Message); } }
        finally
        {
            if (ReferenceEquals(pending, tokenSource)) pending = null;
            tokenSource.Dispose();
            if (!disposed && generation == version)
            {
                // Reload the captured document after cancellation/failure; this
                // never consults the document that replaced it during the await.
                try
                {
                    var history = await backend.CallAsync("assistant_history", new JsonObject { ["document_id"] = id }, binding.Token);
                    if (!disposed && generation == version) { messages = history?["messages"]?.DeepClone() as JsonArray ?? []; RenderMessages(); }
                }
                catch (OperationCanceledException) { }
                catch (Exception ex) { if (!disposed && generation == version) ShowError(ex.Message); }
                finally { if (!disposed && generation == version) { busy = false; UpdateControls(); } }
            }
        }
    }

    private async Task ClearAsync()
    {
        if (documentId.Length == 0 || clearDialogOpen || XamlRoot is null) return;
        clearDialogOpen = true; UpdateControls(); int version = generation; string id = documentId;
        try
        {
            var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = "清空这份文档的助手对话？", Content = "只清空当前文档的对话记录，PDF 和阅读笔记仍保留。", PrimaryButtonText = "清空当前对话", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
            if (await showDialog(dialog) != ContentDialogResult.Primary || version != generation || disposed) return;
            Cancel();
            var result = await backend.CallAsync("assistant_clear", new JsonObject { ["document_id"] = id }, binding.Token);
            if (version != generation || disposed) return;
            messages = result?["messages"]?.DeepClone() as JsonArray ?? []; error.Visibility = Visibility.Collapsed; progress.Text = ""; RenderMessages();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!disposed && generation == version) ShowError(ex.Message); }
        finally { clearDialogOpen = false; UpdateControls(); }
    }

    private void RenderMessages()
    {
        messagesPanel.Children.Clear(); cards.Clear();
        if (messages.Count == 0)
            messagesPanel.Children.Add(Text("从下方提问，开始了解这份文档。", 12, .6));
        foreach (var message in messages.OfType<JsonObject>())
        {
            bool assistant = String(message, "role") == "assistant"; string body = String(message, "text");
            var contents = new StackPanel { Spacing = 8 };
            var heading = new Grid(); heading.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); heading.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); heading.Children.Add(Text(assistant ? "文档助手" : "你", 11, .6));
            if (assistant)
            {
                var tools = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
                tools.Children.Add(IconButton("copy", "复制回答", () => { var data = new DataPackage(); data.SetText(body); Clipboard.SetContent(data); progress.Text = "回答已复制"; return Task.CompletedTask; }));
                if (saveNote is not null) tools.Children.Add(IconButton("note", "保存到阅读笔记", async () =>
                {
                    string noteText = body.Length > 7800 ? body[..7800] + "\n\n（回答超过笔记长度上限，仅截取前 7,800 字符；完整回答可在助手中复制。）" : body;
                    await saveNote("AI 文档助手\n\n" + noteText + "\n\nAI 生成，请对照原文核实。"); progress.Text = "已交给阅读笔记";
                }));
                Grid.SetColumn(tools, 1); heading.Children.Add(tools);
            }
            contents.Children.Add(heading); contents.Children.Add(Text(body, 12));
            foreach (var evidence in message["citations"]?.AsArray().OfType<JsonObject>() ?? [])
            {
                int index = evidence["page"]!.GetValue<int>(); string variant = String(evidence, "variant");
                var jump = new Button { Content = $"P{index + 1} · 跳到{(variant == "translated" ? "译文" : "原文")}", FontSize = 11, Padding = new Thickness(7, 3, 7, 3), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 24 };
                jump.Click += (_, _) => navigate(index, variant); contents.Children.Add(jump);
                string fragment = String(evidence, "text") + (evidence["truncated"]?.GetValue<bool>() == true ? "\n\n发送时已截取，不是完整页面。" : "");
                contents.Children.Add(new Expander { Header = String(evidence, "origin") == "selection" ? "核对选文片段" : "核对页面片段", Content = Text(fragment, 11, .7), HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
            }
            if (message["unverified_pages"] is JsonArray missing && missing.Count > 0)
                contents.Children.Add(Text("未提供的页码引用：" + string.Join("、", missing.Select(n => "P" + (n!.GetValue<int>() + 1))) + "，无法核对，未提供跳转。", 10, .65));
            var card = new Border { Child = contents, Padding = new Thickness(10), CornerRadius = new CornerRadius(8), Tag = assistant };
            cards.Add(card); messagesPanel.Children.Add(card);
        }
        ApplyTheme(); UpdateControls();
        int layoutVersion = ++messagesLayoutVersion, bindingVersion = generation;
        string boundDocument = documentId;
        bool hasMessages = messages.Count > 0;
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            // A later render/binding owns the scroll position. Empty histories
            // start at the introduction; only conversations follow the latest reply.
            if (disposed || layoutVersion != messagesLayoutVersion || bindingVersion != generation || boundDocument != documentId) return;
            scroll.UpdateLayout();
            if (disposed || layoutVersion != messagesLayoutVersion || bindingVersion != generation || boundDocument != documentId) return;
            scroll.ChangeView(null, hasMessages ? scroll.ScrollableHeight : 0, null, true);
        });
    }

    private void ApplyTheme()
    {
        bool dark = ActualTheme == ElementTheme.Dark;
        Background = new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 31, 31, 31) : global::Windows.UI.Color.FromArgb(255, 248, 248, 248));
        var line = new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 58, 58, 58) : global::Windows.UI.Color.FromArgb(255, 221, 221, 221));
        header.BorderBrush = composer.BorderBrush = line;
        foreach (var card in cards) card.Background = new SolidColorBrush((bool)card.Tag
            ? (dark ? global::Windows.UI.Color.FromArgb(255, 39, 39, 39) : Colors.White)
            : (dark ? global::Windows.UI.Color.FromArgb(255, 36, 48, 63) : global::Windows.UI.Color.FromArgb(255, 232, 242, 252)));
    }

    private void ShowError(string message) { error.Text = message; error.Visibility = Visibility.Visible; UpdateFeedback(); }
    internal void Cancel() => pending?.Cancel();
    public void Dispose()
    {
        if (disposed) return; disposed = true; generation++; Cancel(); binding.Cancel(); binding.Dispose();
    }
}
