using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Text;

namespace PageBridge.Windows;

internal static class ServiceSettings
{
    public static async Task<ServiceSettingsView> CreateViewAsync(XamlRoot xamlRoot, BackendClient backend, Action onClose)
    {
        var view = new ServiceSettingsView(xamlRoot, backend, onClose);
        await view.InitializeAsync();
        return view;
    }

    // Compatibility for callers that do not own a document workspace.
    public static async Task ShowAsync(XamlRoot xamlRoot, BackendClient backend)
    {
        var dialog = new ContentDialog { XamlRoot = xamlRoot, Padding = new Thickness(0) };
        var themeRoot = xamlRoot.Content as FrameworkElement;
        void FollowRootTheme(FrameworkElement sender, object args) => dialog.RequestedTheme = sender.ActualTheme;
        if (themeRoot is not null) dialog.RequestedTheme = themeRoot.ActualTheme;
        dialog.Resources["ContentDialogMaxWidth"] = 1040d;
        dialog.Resources["ContentDialogPadding"] = new Thickness(0);
        var view = await CreateViewAsync(xamlRoot, backend, dialog.Hide);
        view.Width = Math.Max(640, Math.Min(1000, xamlRoot.Size.Width - 80));
        view.Height = Math.Max(440, xamlRoot.Size.Height - 100);
        dialog.Content = view;
        bool confirmed = false;
        dialog.Closing += async (_, args) =>
        {
            if (confirmed) return;
            var deferral = args.GetDeferral();
            args.Cancel = !await view.RequestCloseAsync();
            confirmed = !args.Cancel;
            deferral.Complete();
        };
        if (themeRoot is not null) { dialog.RequestedTheme = themeRoot.ActualTheme; themeRoot.ActualThemeChanged += FollowRootTheme; }
        try { await dialog.ShowAsync(); }
        finally
        {
            if (themeRoot is not null) themeRoot.ActualThemeChanged -= FollowRootTheme;
            view.Dispose();
        }
    }
}

/// <summary>The same header, service rail, cards and footer as the Mac settings workspace.</summary>
internal sealed class ServiceSettingsView : Grid, IDisposable
{
    private sealed record Provider(string Id, string Title, string Hint, string[] Models);
    private static readonly Provider[] Providers = [
        new("deepSeek", "DeepSeek", "服务地址已配置，选择模型并粘贴 API Key 即可。", ["deepseek-flash", "deepseek-v4-pro"]),
        new("alibaba", "阿里云百炼（北京）", "使用北京地域的 API Key；其他地域或专属地址可选“自定义 API”。", ["qwen-plus", "qwen-flash"]),
        new("siliconFlow", "硅基流动（中国站）", "使用中国站 API Key；国际站可选“自定义 API”。", ["deepseek-ai/DeepSeek-V4-Flash"]),
        new("openAI", "OpenAI", "服务地址已配置，选择模型并粘贴 API Key 即可。", ["gpt-6-luna"]),
        new("ollama", "Ollama · 本机", "启动本机 Ollama 后，点击“获取模型”选择已下载的模型。", ["qwen3.5:35b-a3b-int4"]),
        new("custom", "自定义 API", "连接支持 OpenAI Chat Completions 的服务。", [])
    ];
    private readonly BackendClient backend;
    private readonly Action onClose;
    private readonly SolidColorBrush background, card, secondary, line, accent, selected;
    private readonly StackPanel profilesPanel = new() { Spacing = 5 };
    private readonly ComboBox provider = new() { HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 14 };
    private readonly ComboBox model = new() { HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 14 };
    private readonly TextBox name = new() { MaxLength = 48, FontSize = 14 };
    private readonly TextBox address = new() { PlaceholderText = "https://api.example.com/v1", FontSize = 14 };
    private readonly TextBox localAddress = new() { FontSize = 14 };
    private readonly TextBox manualModel = new() { PlaceholderText = "输入服务提供方的模型 ID", FontSize = 14 };
    private readonly PasswordBox key = new() { PlaceholderText = "粘贴厂商提供的 API Key", FontSize = 14, PasswordRevealMode = PasswordRevealMode.Peek };
    private readonly NumberBox timeout = new() { Minimum = 10, Maximum = 600, Value = 120, Width = 140, HorizontalAlignment = HorizontalAlignment.Left, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
    private readonly NumberBox concurrency = new() { Minimum = 1, Maximum = 8, Value = 1, Width = 130, HorizontalAlignment = HorizontalAlignment.Left, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
    private readonly TextBlock hint = Copy("", 11);
    private readonly TextBlock host = Copy("", 11);
    private readonly TextBlock modelNotice = Copy("", 11);
    private readonly TextBlock modelError = Copy("", 12);
    private readonly TextBlock testResult = Copy("", 13);
    private readonly TextBlock testError = Copy("", 12);
    private readonly TextBlock privacy = Copy("", 11);
    private readonly TextBlock status = Copy("", 11);
    private readonly TextBlock endpoint = Copy("", 11);
    private readonly Button modelsButton = SmallButton("获取模型");
    private readonly Button testButton = SmallButton("测试连接");
    private readonly Button saveButton = SmallButton("保存配置");
    private readonly Button resetButton = SmallButton("重置");
    private readonly Button deleteButton = SmallButton("删除");
    private readonly ProgressRing networkProgress = new() { Width = 18, Height = 18, IsActive = false, Visibility = Visibility.Collapsed };
    private readonly Expander advanced = new() { HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(0) };
    private readonly StackPanel customAddressField, localAddressField, manualModelField, keyField;
    private readonly Grid fixedAddress;
    private readonly Grid confirmation = new() { Visibility = Visibility.Collapsed, Background = new SolidColorBrush(global::Windows.UI.Color.FromArgb(90, 0, 0, 0)) };
    private readonly TextBlock confirmTitle = Copy("", 18, true), confirmMessage = Copy("", 13);
    private readonly Button confirmAccept = SmallButton("放弃更改");
    private TaskCompletionSource<bool>? confirmationResult;
    private List<JsonObject> profiles = [];
    private JsonObject current = new(), baseline = new();
    private string activeId = "";
    private bool filling, isNew, manual, credentialInvalidated, disposed, saving;
    private List<string>? discoveredModels;
    private CancellationTokenSource? operation;
    private string? operationKind;
    private Provider ChosenProvider => Providers.First(p => p.Id == Text(current, "provider"));
    private bool IsLocal => Text(current, "kind") == "ollama";
    private bool Dirty => isNew || key.Password.Length > 0 || credentialInvalidated || !JsonNode.DeepEquals(Capture(), baseline);

    public ServiceSettingsView(XamlRoot xamlRoot, BackendClient backend, Action onClose)
    {
        this.backend = backend;
        this.onClose = onClose;
        var dark = (xamlRoot.Content as FrameworkElement)?.ActualTheme == ElementTheme.Dark;
        background = Brush(dark ? 0x202020u : 0xf5f5f5u);
        card = Brush(dark ? 0x292929u : 0xffffffu);
        secondary = Brush(dark ? 0xa1a1a5u : 0x6f6f74u);
        line = Brush(dark ? 0x3b3b3du : 0xdededfu);
        accent = Brush(0x1687f8);
        selected = Brush(dark ? 0x24384eu : 0xe7f1fbu);
        Background = background;
        RowDefinitions.Add(new() { Height = GridLength.Auto });
        RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        RowDefinitions.Add(new() { Height = GridLength.Auto });
        foreach (var text in new[] { hint, host, modelNotice, privacy, status, endpoint }) text.Foreground = secondary;
        modelError.Foreground = testError.Foreground = Brush(0xe46262);
        modelError.Visibility = testError.Visibility = testResult.Visibility = Visibility.Collapsed;

        var header = new Grid { Padding = new Thickness(28, 24, 28, 24), ColumnSpacing = 20 };
        header.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        header.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var heading = new StackPanel { Spacing = 7 };
        heading.Children.Add(Copy("翻译服务", 25, true));
        var subtitle = Copy("选择厂商和模型，填入 API Key 即可开始翻译。", 13); subtitle.Foreground = secondary;
        heading.Children.Add(subtitle); header.Children.Add(heading);
        var back = SmallButton("返回文档"); back.VerticalAlignment = VerticalAlignment.Center;
        var backContent = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7 };
        backContent.Children.Add(ReaderIcons.Make("left", 12)); backContent.Children.Add(Copy("返回文档", 12)); back.Content = backContent;
        AutomationProperties.SetName(back, "返回文档");
        back.Click += async (_, _) => { if (await RequestCloseAsync()) onClose(); };
        Grid.SetColumn(back, 1); header.Children.Add(back);
        Children.Add(new Border { Child = header, BorderBrush = line, BorderThickness = new Thickness(0, 0, 0, 1) });

        var body = new Grid();
        body.ColumnDefinitions.Add(new() { Width = new GridLength(205) });
        body.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetRow(body, 1); Children.Add(body);
        var rail = new Grid { Padding = new Thickness(16) };
        rail.RowDefinitions.Add(new() { Height = GridLength.Auto });
        rail.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        rail.RowDefinitions.Add(new() { Height = GridLength.Auto });
        var railHeader = new Grid { Margin = new Thickness(9, 0, 0, 12) };
        var railTitle = Copy("我的服务", 12, true); railTitle.Foreground = secondary; railTitle.VerticalAlignment = VerticalAlignment.Center;
        railHeader.Children.Add(railTitle);
        var add = SmallButton(""); add.Content = ReaderIcons.Make("plus", 15); add.HorizontalAlignment = HorizontalAlignment.Right; add.Padding = new Thickness(6, 2, 6, 2);
        ToolTipService.SetToolTip(add, "添加服务配置");
        AutomationProperties.SetName(add, "添加服务配置");
        var addMenu = new MenuFlyout();
        foreach (var item in Providers)
        {
            var entry = new MenuFlyoutItem { Text = item.Title };
            entry.Click += async (_, _) => { if (!saving && await ConfirmDiscardAsync()) await NewAsync(item.Id); };
            addMenu.Items.Add(entry);
            provider.Items.Add(new ComboBoxItem { Content = item.Title, Tag = item.Id });
        }
        add.Flyout = addMenu; railHeader.Children.Add(add); rail.Children.Add(railHeader);
        var railScroll = new ScrollViewer { Content = profilesPanel, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        Grid.SetRow(railScroll, 1); rail.Children.Add(railScroll);
        var railNote = Copy("服务设置仅影响新任务。", 11); railNote.Foreground = secondary; railNote.Margin = new Thickness(9, 12, 0, 0);
        Grid.SetRow(railNote, 2); rail.Children.Add(railNote);
        body.Children.Add(new Border { Child = rail, BorderBrush = line, BorderThickness = new Thickness(0, 0, 1, 0) });

        var content = new StackPanel { Spacing = 18, MaxWidth = 760, HorizontalAlignment = HorizontalAlignment.Stretch };
        var connection = new StackPanel { Spacing = 18 };
        connection.Children.Add(Copy("服务配置", 14, true));
        connection.Children.Add(Field("厂商", provider));
        connection.Children.Add(hint);
        customAddressField = Field("服务地址", address, "填写 Base URL，不包含 /chat/completions。");
        connection.Children.Add(customAddressField); connection.Children.Add(host);
        var modelRow = new Grid { ColumnSpacing = 10 };
        AutomationProperties.SetName(model, "模型");
        modelRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        modelRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        modelRow.Children.Add(model); Grid.SetColumn(modelsButton, 1); modelRow.Children.Add(modelsButton);
        connection.Children.Add(Field("模型", modelRow));
        manualModelField = Field("模型 ID", manualModel); connection.Children.Add(manualModelField);
        connection.Children.Add(modelNotice); connection.Children.Add(modelError);
        keyField = Field("API Key", key, "仅保存在 Windows 凭据管理器。获取模型和测试连接均由你点击后发送。");
        connection.Children.Add(keyField);
        connection.Children.Add(new Border { Height = 1, Background = line });
        var advancedTitle = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 20 };
        advancedTitle.Children.Add(Copy("高级设置", 12, true));
        var advancedHint = Copy("名称、地址与性能", 11); advancedHint.Foreground = secondary; advancedTitle.Children.Add(advancedHint);
        advanced.Header = advancedTitle;
        var advancedBody = new StackPanel { Spacing = 14, Margin = new Thickness(0, 13, 0, 0) };
        advancedBody.Children.Add(Field("配置名称", name));
        localAddressField = Field("本机服务地址", localAddress); advancedBody.Children.Add(localAddressField);
        fixedAddress = new Grid { ColumnSpacing = 12 };
        fixedAddress.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        fixedAddress.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        endpoint.IsTextSelectionEnabled = true; fixedAddress.Children.Add(endpoint);
        var custom = SmallButton("使用自定义地址"); Grid.SetColumn(custom, 1); fixedAddress.Children.Add(custom);
        custom.Click += (_, _) => { CancelOperation(); current = Capture(); current["provider"] = "custom"; InvalidateCredential(); Populate(); advanced.IsExpanded = true; };
        advancedBody.Children.Add(fixedAddress);
        var performance = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 20 };
        performance.Children.Add(Field("请求超时（秒）", timeout)); performance.Children.Add(Field("最大并发请求", concurrency));
        advancedBody.Children.Add(performance);
        var perfHint = Copy("本机大模型建议并发 1；在线服务的并发越高，越容易触发速率限制。", 11); perfHint.Foreground = secondary;
        advancedBody.Children.Add(perfHint); advanced.Content = advancedBody; connection.Children.Add(advanced);
        content.Children.Add(Card(connection));

        var testCard = new StackPanel { Spacing = 15 };
        var testHeading = new Grid { ColumnSpacing = 12 };
        testHeading.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        testHeading.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var testLabels = new StackPanel { Spacing = 6 }; testLabels.Children.Add(Copy("连接测试", 14, true));
        var testHint = Copy("发送固定短句，验证模型能够返回实际译文。", 12); testHint.Foreground = secondary; testLabels.Children.Add(testHint);
        testHeading.Children.Add(testLabels);
        var testActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
        testActions.Children.Add(networkProgress); testActions.Children.Add(testButton); Grid.SetColumn(testActions, 1); testHeading.Children.Add(testActions);
        testCard.Children.Add(testHeading);
        var testDisclosure = Copy("测试内容：The document is ready. 不发送你的 PDF。在线 API 可能产生少量费用。", 11); testDisclosure.Foreground = secondary;
        testCard.Children.Add(testDisclosure); testCard.Children.Add(testResult); testCard.Children.Add(testError);
        testResult.IsTextSelectionEnabled = true;
        content.Children.Add(Card(testCard)); content.Children.Add(privacy);
        var scroll = new ScrollViewer { Content = content, Padding = new Thickness(26), HorizontalContentAlignment = HorizontalAlignment.Stretch, HorizontalScrollMode = ScrollMode.Disabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        // A vertical ScrollViewer can otherwise measure a StackPanel wider
        // than its viewport, clipping controls on high-DPI/narrow windows.
        void FitCards() { content.Width = Math.Max(0, Math.Min(760, scroll.ActualWidth - 52)); content.HorizontalAlignment = HorizontalAlignment.Center; }
        scroll.SizeChanged += (_, _) => FitCards(); scroll.Loaded += (_, _) => FitCards();
        Grid.SetColumn(scroll, 1); body.Children.Add(scroll);

        var footer = new Grid { Padding = new Thickness(25, 16, 25, 16), ColumnSpacing = 15 };
        footer.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        footer.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        status.VerticalAlignment = VerticalAlignment.Center; footer.Children.Add(status);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 15 };
        deleteButton.Content = ReaderIcons.Make("trash", 15); ToolTipService.SetToolTip(deleteButton, "删除服务配置");
        AutomationProperties.SetName(deleteButton, "删除服务配置");
        saveButton.Background = accent; saveButton.Foreground = Brush(0xffffff);
        actions.Children.Add(deleteButton); actions.Children.Add(resetButton); actions.Children.Add(saveButton);
        Grid.SetColumn(actions, 1); footer.Children.Add(actions);
        var footerBorder = new Border { Child = footer, BorderBrush = line, BorderThickness = new Thickness(0, 1, 0, 0) };
        Grid.SetRow(footerBorder, 2); Children.Add(footerBorder);

        var confirmPanel = new StackPanel { Spacing = 14 }; confirmPanel.Children.Add(confirmTitle); confirmPanel.Children.Add(confirmMessage);
        var confirmButtons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, HorizontalAlignment = HorizontalAlignment.Right };
        var confirmCancel = SmallButton("取消"); confirmCancel.Click += (_, _) => FinishConfirmation(false);
        confirmAccept.Click += (_, _) => FinishConfirmation(true); confirmButtons.Children.Add(confirmCancel); confirmButtons.Children.Add(confirmAccept); confirmPanel.Children.Add(confirmButtons);
        confirmation.Children.Add(new Border { Child = confirmPanel, Background = card, Padding = new Thickness(24), CornerRadius = new CornerRadius(10), MaxWidth = 390, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, BorderBrush = line, BorderThickness = new Thickness(1) });
        Grid.SetRowSpan(confirmation, 3); Children.Add(confirmation);
        provider.SelectionChanged += async (_, _) =>
        {
            if (filling || provider.SelectedItem is not ComboBoxItem { Tag: string chosen } || chosen == Text(current, "provider")) return;
            try
            {
                CancelOperation(); var id = Text(current, "id");
                var preset = (await backend.CallAsync("new_profile", new JsonObject { ["provider"] = chosen }))!.AsObject();
                if (disposed || (provider.SelectedItem as ComboBoxItem)?.Tag?.ToString() != chosen) return;
                preset["id"] = id; preset["name"] = UniqueName(Text(preset, "name"), id); current = preset;
                manual = chosen == "custom";
                InvalidateCredential(); Populate(); advanced.IsExpanded = false;
                SetStatus(IsLocal ? "已切换为本机 Ollama。" : "服务已切换，请填写对应厂商的 API Key。");
            }
            catch (Exception error) { SetStatus(error.Message, true); }
        };
        model.SelectionChanged += (_, _) =>
        {
            if (filling || model.SelectedItem is not ComboBoxItem { Tag: string choice }) return;
            manual = choice == "__manual__";
            if (!manual) { filling = true; manualModel.Text = choice; filling = false; }
            manualModelField.Visibility = manual ? Visibility.Visible : Visibility.Collapsed; Changed();
        };
        name.TextChanged += (_, _) => { if (!filling) { Changed(); RenderProfiles(); } };
        manualModel.TextChanged += (_, _) => { if (!filling) Changed(); };
        address.TextChanged += (_, _) => { if (!filling) { InvalidateCredential(); Changed(); } };
        localAddress.TextChanged += (_, _) => { if (!filling) { InvalidateCredential(); Changed(); } };
        key.PasswordChanged += (_, _) => { if (!filling) { discoveredModels = null; PopulateModels(); Changed(); } };
        timeout.ValueChanged += (_, _) => { if (!filling) Changed(); };
        concurrency.ValueChanged += (_, _) => { if (!filling) Changed(); };
        modelsButton.Click += async (_, _) => { if (operationKind == "models") CancelOperation(); else await NetworkAsync("models"); };
        testButton.Click += async (_, _) => { if (operationKind == "probe") CancelOperation(); else await NetworkAsync("probe"); };
        resetButton.Click += async (_, _) =>
        {
            if (isNew) { var previous = profiles.FirstOrDefault(p => Text(p, "id") == activeId) ?? profiles.FirstOrDefault(); if (previous is not null) Load(previous); else await NewAsync("ollama"); }
            else Load(baseline);
        };
        saveButton.Click += async (_, _) => await SaveAsync();
        deleteButton.Click += async (_, _) => await DeleteAsync();
        Loaded += (_, _) => ApplySettingsTheme();
        ActualThemeChanged += (_, _) => ApplySettingsTheme();
        Unloaded += (_, _) => Dispose();
    }

    public async Task InitializeAsync()
    {
        await RefreshAsync();
        var profile = profiles.FirstOrDefault(p => Text(p, "id") == activeId) ?? profiles.FirstOrDefault();
        if (profile is not null) Load(profile); else await NewAsync("ollama");
    }

    public async Task<bool> RequestCloseAsync()
    {
        if (saving) return false;
        if (!await ConfirmDiscardAsync()) return false;
        CancelOperation(); return true;
    }

    private async Task RefreshAsync()
    {
        var stored = await backend.CallAsync("profiles");
        profiles = stored?["profiles"]?.AsArray().OfType<JsonObject>().Select(p => p.DeepClone().AsObject()).ToList() ?? [];
        activeId = Text(stored, "active_id");
    }

    private void Load(JsonObject profile)
    {
        CancelOperation(); current = profile.DeepClone().AsObject(); baseline = current.DeepClone().AsObject();
        isNew = credentialInvalidated = false; discoveredModels = null; manual = Text(current, "provider") == "custom" || Text(current, "model") == "";
        filling = true; key.Password = ""; filling = false;
        Populate(); advanced.IsExpanded = false;
    }

    private async Task NewAsync(string providerId)
    {
        try
        {
            CancelOperation(); current = (await backend.CallAsync("new_profile", new JsonObject { ["provider"] = providerId }))!.AsObject();
            current["name"] = UniqueName(Text(current, "name")); baseline = current.DeepClone().AsObject(); isNew = true;
            credentialInvalidated = false; discoveredModels = null; manual = providerId == "custom";
            filling = true; key.Password = ""; filling = false;
            Populate(); advanced.IsExpanded = false;
        }
        catch (Exception error) { SetStatus(error.Message, true); }
    }

    private void Populate()
    {
        filling = true;
        provider.SelectedItem = provider.Items.OfType<ComboBoxItem>().First(p => (string)p.Tag == Text(current, "provider"));
        name.Text = Text(current, "name"); address.Text = localAddress.Text = Text(current, "baseURL");
        manualModel.Text = Text(current, "model");
        timeout.Value = current["timeoutSeconds"]?.GetValue<double>() ?? 120; concurrency.Value = current["concurrency"]?.GetValue<int>() ?? 1;
        hint.Text = ChosenProvider.Hint; endpoint.Text = Text(current, "baseURL");
        host.Text = (IsLocal ? "▣  " : "✓  ") + (Uri.TryCreate(endpoint.Text, UriKind.Absolute, out var uri) ? uri.Authority : endpoint.Text);
        var custom = Text(current, "provider") == "custom";
        customAddressField.Visibility = custom ? Visibility.Visible : Visibility.Collapsed; host.Visibility = custom ? Visibility.Collapsed : Visibility.Visible;
        keyField.Visibility = IsLocal ? Visibility.Collapsed : Visibility.Visible;
        localAddressField.Visibility = IsLocal ? Visibility.Visible : Visibility.Collapsed;
        fixedAddress.Visibility = !custom && !IsLocal ? Visibility.Visible : Visibility.Collapsed;
        key.PlaceholderText = !isNew && !credentialInvalidated ? "已保存的密钥留空保持不变" : "粘贴厂商提供的 API Key";
        PopulateModels();
        privacy.Text = IsLocal ? "当前地址指向本机。文档解析与排版在本机完成，文本发送至此本机服务。离线使用前需准备全部翻译资源和模型。" : "文档解析与排版在本机完成；待翻译文本会发送至所选远程服务。请确认服务地址及其隐私政策。";
        filling = false; ClearResults(); RenderProfiles(); UpdateControls();
    }

    private void PopulateModels()
    {
        var previous = filling; filling = true;
        var choices = (discoveredModels ?? ChosenProvider.Models.ToList()).ToList();
        var selectedModel = manualModel.Text;
        if (selectedModel != "" && !choices.Contains(selectedModel)) choices.Insert(0, selectedModel);
        model.Items.Clear();
        if (selectedModel == "") model.Items.Add(new ComboBoxItem { Content = "请选择模型", Tag = "" });
        foreach (var value in choices) model.Items.Add(new ComboBoxItem { Content = value, Tag = value });
        model.Items.Add(new ComboBoxItem { Content = "手动输入模型 ID…", Tag = "__manual__" });
        model.SelectedItem = model.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string)item.Tag == (manual ? "__manual__" : selectedModel));
        manualModelField.Visibility = manual ? Visibility.Visible : Visibility.Collapsed;
        modelNotice.Text = discoveredModels is null ? "可直接使用预设模型；填入密钥后点击“获取模型”更新列表。" : $"已获取 {discoveredModels.Count} 个模型，选择后可测试连接。";
        filling = previous;
    }

    private JsonObject Capture()
    {
        var result = current.DeepClone().AsObject();
        result["name"] = name.Text.Trim(); result["baseURL"] = (IsLocal ? localAddress.Text : Text(current, "provider") == "custom" ? address.Text : Text(current, "baseURL")).Trim();
        result["model"] = manual ? manualModel.Text.Trim() : (model.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? Text(current, "model");
        result["timeoutSeconds"] = double.IsNaN(timeout.Value) ? 120 : timeout.Value;
        result["concurrency"] = double.IsNaN(concurrency.Value) ? 1 : (int)concurrency.Value;
        return result;
    }

    private JsonObject Request()
    {
        if (!IsLocal && (isNew || credentialInvalidated) && string.IsNullOrWhiteSpace(key.Password)) throw new InvalidOperationException("请填写对应厂商的 API Key。");
        var request = new JsonObject { ["profile"] = Capture() };
        if (key.Password != "" || credentialInvalidated || IsLocal) request["apiKey"] = IsLocal ? "" : key.Password.Trim();
        return request;
    }

    private async Task SaveAsync()
    {
        try
        {
            var request = Request(); var captured = request["profile"]!.AsObject();
            if (profiles.Any(p => Text(p, "id") != Text(current, "id") && Text(p, "name") == Text(captured, "name"))) throw new InvalidOperationException("配置名称已经存在，请使用不同名称。");
            saving = true; UpdateControls();
            var saved = (await backend.CallAsync("save_profile", request))!.AsObject();
            await backend.CallAsync("set_active", new JsonObject { ["service_id"] = Text(saved, "id") });
            await RefreshAsync(); Load(saved); SetStatus("已保存并设为当前服务。仅影响新任务。");
        }
        catch (Exception error) { SetStatus(error.Message, true); }
        finally { saving = false; UpdateControls(false); }
    }

    private async Task DeleteAsync()
    {
        if (isNew || profiles.Count <= 1 || saving) return;
        if (!await ConfirmAsync($"删除“{name.Text}”？", "这会删除此服务配置及其系统凭据密钥。已有文档和正在运行的任务不受影响。", "删除服务")) return;
        try
        {
            CancelOperation(); saving = true; UpdateControls();
            await backend.CallAsync("delete_profile", new JsonObject { ["service_id"] = Text(current, "id") });
            await RefreshAsync(); Load(profiles.FirstOrDefault(p => Text(p, "id") == activeId) ?? profiles[0]);
        }
        catch (Exception error) { SetStatus(error.Message, true); }
        finally { saving = false; UpdateControls(false); }
    }

    private async Task NetworkAsync(string kind)
    {
        CancellationTokenSource? source = null;
        try
        {
            var request = Request(); CancelOperation(); ClearResults();
            operation = source = new CancellationTokenSource(); operationKind = kind; UpdateControls();
            var result = await backend.CallAsync(kind == "models" ? "list_models" : "probe", request, source.Token);
            if (disposed || operation != source || source.IsCancellationRequested) return;
            operation = null; operationKind = null;
            if (kind == "models")
            {
                discoveredModels = result?.AsArray().Select(n => n!.GetValue<string>()).ToList() ?? [];
                if (manualModel.Text == "" && discoveredModels.Count > 0) { filling = true; manualModel.Text = discoveredModels[0]; filling = false; manual = false; }
                PopulateModels();
            }
            else
            {
                testResult.Text = $"✓  连接成功 · 已收到译文\n\n{result?.GetValue<string>()}\n\n验证于 {DateTime.Now:HH:mm} · {host.Text.TrimStart('▣', '✓', ' ')}";
                testResult.Foreground = Brush(0x46b87c); testResult.Visibility = Visibility.Visible;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (source is null || operation == source)
            {
                var label = kind == "models" ? modelError : testError; label.Text = error.Message; label.Visibility = Visibility.Visible;
            }
        }
        finally
        {
            if (operation == source) { operation = null; operationKind = null; }
            source?.Dispose(); if (!disposed) UpdateControls(false);
        }
    }

    private void Changed()
    {
        CancelOperation(); ClearResults(); UpdateControls();
    }

    private void InvalidateCredential()
    {
        CancelOperation(); credentialInvalidated = true; discoveredModels = null;
        var previous = filling; filling = true; key.Password = ""; filling = previous;
        key.PlaceholderText = "粘贴厂商提供的 API Key";
    }

    private void CancelOperation()
    {
        var previous = operation; operation = null; operationKind = null; previous?.Cancel();
        if (!disposed) UpdateControls(false);
    }

    private void ClearResults() { testResult.Visibility = testError.Visibility = modelError.Visibility = Visibility.Collapsed; }

    private void UpdateControls(bool updateStatus = true)
    {
        if (current.Count == 0) return;
        modelsButton.Content = operationKind == "models" ? "取消" : "获取模型";
        testButton.Content = operationKind == "probe" ? "取消" : "测试连接";
        modelsButton.IsEnabled = !saving && operationKind != "probe" && (IsLocal || (!isNew && !credentialInvalidated) || key.Password.Length > 0);
        testButton.IsEnabled = !saving && operationKind != "models";
        networkProgress.IsActive = operation is not null; networkProgress.Visibility = operation is null ? Visibility.Collapsed : Visibility.Visible;
        saveButton.Content = Text(current, "id") == activeId ? "保存配置" : "保存并使用";
        saveButton.IsEnabled = !saving && operation is null;
        deleteButton.IsEnabled = !saving && !isNew && profiles.Count > 1;
        resetButton.IsEnabled = !saving && Dirty;
        foreach (var control in new Control[] { provider, model, name, address, localAddress, manualModel, key, timeout, concurrency, advanced }) control.IsEnabled = !saving;
        foreach (var row in profilesPanel.Children.OfType<Button>()) row.IsEnabled = !saving;
        if (updateStatus) SetStatus(Dirty ? "●  有未保存的更改" : "密钥由 Windows 凭据管理器保管");
    }

    private void RenderProfiles()
    {
        profilesPanel.Children.Clear();
        foreach (var profile in profiles.Concat(isNew ? [current] : Array.Empty<JsonObject>()))
        {
            var id = Text(profile, "id"); var draft = id == Text(current, "id"); var active = id == activeId;
            var row = new Grid { ColumnSpacing = 10 };
            row.ColumnDefinitions.Add(new() { Width = new GridLength(20) }); row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            row.Children.Add(new FontIcon { Glyph = Text(profile, "kind") == "ollama" ? "\uE7F4" : "\uE753", FontSize = 18 });
            var labels = new StackPanel { Spacing = 5 };
            var title = Copy(draft ? name.Text : Text(profile, "name"), 12, true); title.TextWrapping = TextWrapping.NoWrap; title.TextTrimming = TextTrimming.CharacterEllipsis;
            labels.Children.Add(title); var detail = Copy(active ? "当前使用" : isNew && draft ? "尚未保存" : Providers.First(p => p.Id == Text(profile, "provider")).Title, 11); detail.Foreground = secondary; labels.Children.Add(detail);
            Grid.SetColumn(labels, 1); row.Children.Add(labels);
            if (active) { var check = Copy("✓", 11, true); Grid.SetColumn(check, 2); row.Children.Add(check); }
            var button = new Button { Content = row, Padding = new Thickness(11), HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(7), Background = draft ? selected : new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
            AutomationProperties.SetName(button, draft ? name.Text : Text(profile, "name"));
            if (draft) button.Foreground = accent;
            button.Click += async (_, _) => { if (!saving && id != Text(current, "id") && await ConfirmDiscardAsync()) Load(profile); };
            profilesPanel.Children.Add(button);
        }
    }

    private Task<bool> ConfirmDiscardAsync() => !Dirty ? Task.FromResult(true) : ConfirmAsync("放弃未保存的更改？", "当前修改尚未保存，离开后将丢失。", "放弃更改");
    private Task<bool> ConfirmAsync(string title, string message, string accept)
    {
        if (confirmationResult is not null) return confirmationResult.Task;
        confirmTitle.Text = title; confirmMessage.Text = message; confirmAccept.Content = accept;
        confirmationResult = new(TaskCreationOptions.RunContinuationsAsynchronously); confirmation.Visibility = Visibility.Visible;
        return confirmationResult.Task;
    }
    private void FinishConfirmation(bool value) { confirmation.Visibility = Visibility.Collapsed; var pending = confirmationResult; confirmationResult = null; pending?.TrySetResult(value); }
    private string UniqueName(string value, string? exclude = null) { var result = value; int suffix = 2; while (profiles.Any(p => Text(p, "id") != exclude && Text(p, "name") == result)) result = value + " " + suffix++; return result; }
    private void SetStatus(string message, bool error = false) { status.Text = message; status.Foreground = error ? Brush(0xe46262) : secondary; }
    private void ApplySettingsTheme()
    {
        // Mutate the shared brushes already used by cards, borders, labels and
        // profile rows. New controls and open settings then follow the same
        // effective theme as the document workspace, including live changes.
        bool dark = ActualTheme == ElementTheme.Dark;
        background.Color = Color(dark ? 0x202020u : 0xf5f5f5u);
        card.Color = Color(dark ? 0x292929u : 0xffffffu);
        secondary.Color = Color(dark ? 0xa1a1a5u : 0x6f6f74u);
        line.Color = Color(dark ? 0x3b3b3du : 0xdededfu);
        selected.Color = Color(dark ? 0x24384eu : 0xe7f1fbu);
    }
    private Border Card(UIElement child) => new() { Child = child, Padding = new Thickness(23), Background = card, CornerRadius = new CornerRadius(10), BorderBrush = line, BorderThickness = new Thickness(1) };
    private StackPanel Field(string title, UIElement control, string? note = null)
    {
        AutomationProperties.SetName(control, title);
        var stack = new StackPanel { Spacing = 7 }; var label = Copy(title, 12, true); label.Foreground = secondary; stack.Children.Add(label); stack.Children.Add(control);
        if (note is not null) { var text = Copy(note, 11); text.Foreground = secondary; stack.Children.Add(text); }
        return stack;
    }
    private static string Text(JsonNode? node, string key) => node?[key]?.GetValue<string>() ?? "";
    private static TextBlock Copy(string text, double size, bool bold = false) => new() { Text = text, FontSize = size, TextWrapping = TextWrapping.Wrap, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal };
    private static Button SmallButton(string text) => new() { Content = text, FontSize = 12, Padding = new Thickness(12, 7, 12, 7), CornerRadius = new CornerRadius(6) };
    private static global::Windows.UI.Color Color(uint rgb) => global::Windows.UI.Color.FromArgb(255, (byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);
    private static SolidColorBrush Brush(uint rgb) => new(Color(rgb));
    public void Dispose() { if (disposed) return; disposed = true; CancelOperation(); FinishConfirmation(false); filling = true; key.Password = ""; }
}
