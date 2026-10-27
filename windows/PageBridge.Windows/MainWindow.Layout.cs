using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using global::Windows.ApplicationModel.DataTransfer;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private readonly Grid workspace = new();
    private Border modeSegments = null!;
    private bool sidebarVisible = true;
    private readonly StackPanel navigationRows = new() { Spacing = 2 };
    private readonly StackPanel folderRows = new() { Spacing = 2 };
    private readonly TextBlock libraryCaption = new() { Text = "全部文档", FontSize = 11, Opacity = .65 };
    private readonly Grid emptyLibrary = new();
    private readonly Grid pageNavigation = new() { Width = 158 };
    private readonly ListView pageThumbnails = new();
    private readonly List<Button> modeButtons = [];
    private readonly List<FrameworkElement> themedBars = [];
    private readonly Dictionary<string, string> thumbnailPaths = [];
    private readonly SemaphoreSlim thumbnailGate = new(1, 1);
    private readonly Button exportButton = new();
    private readonly TextBlock documentStatus = new() { FontSize = 11, Opacity = .65, VerticalAlignment = VerticalAlignment.Center };
    private readonly StackPanel readingTools = new() { Orientation = Orientation.Horizontal, Spacing = 9 };
    private readonly Grid readerArea = new();
    private FrameworkElement? settingsOverlay;
    private bool settingsOpening;
    private string sortOrder = "recent";
    private static readonly (string Code, string Name)[] Languages = [("en", "英语"), ("zh", "简体中文"), ("zh-TW", "繁体中文"), ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("ru", "俄语")];
    private static string LanguageName(string code) => Languages.FirstOrDefault(x => x.Code == code).Name ?? (code == "" ? "简体中文" : code);
    private static double Number(JsonNode? node, string key) => node?[key]?.GetValue<double>() ?? 0;
    private bool Dark => root.ActualTheme == ElementTheme.Dark;
    private static SolidColorBrush Brush(byte r, byte g, byte b) => new(global::Windows.UI.Color.FromArgb(255, r, g, b));
    private static SolidColorBrush Clear => new(Colors.Transparent);
    private static SolidColorBrush Blue => Brush(10, 132, 255);
    private Brush BarBrush => Dark ? Brush(33, 33, 33) : Brush(248, 248, 248);
    private Brush LineBrush => Dark ? Brush(59, 59, 59) : Brush(220, 220, 220);

    private static StackPanel Row(params UIElement[] children)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 7, VerticalAlignment = VerticalAlignment.Center };
        foreach (var child in children) row.Children.Add(child);
        return row;
    }
    private Button Tool(string icon, string? label, string help, Func<Task>? action = null)
    {
        var content = Row(ReaderIcons.Make(icon));
        if (label is not null) content.Children.Add(new TextBlock { Text = label, FontSize = 12, VerticalAlignment = VerticalAlignment.Center });
        var button = new Button { Content = content, Background = Clear, BorderBrush = Clear, BorderThickness = new Thickness(0), Padding = new Thickness(4), MinHeight = 0, Height = 28, CornerRadius = new CornerRadius(5) };
        button.Resources["ButtonBackgroundDisabled"] = Clear; button.Resources["ButtonBorderBrushDisabled"] = Clear;
        ToolTipService.SetToolTip(button, help);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, help);
        if (action is not null) button.Click += async (_, _) => await Attempt(action);
        return button;
    }
    private Border Divider(bool vertical = false) => new() { Background = LineBrush, Width = vertical ? 1 : double.NaN, Height = vertical ? 18 : 1, Margin = vertical ? new Thickness(2, 0, 2, 0) : new Thickness(0) };
    private static TextBlock SectionTitle(string text) => new() { Text = text, FontSize = 11, Opacity = .5, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Margin = new Thickness(5, 13, 0, 6) };

    private void BuildMacLayout()
    {
        if (Environment.GetCommandLineArgs().Contains("--dark")) root.RequestedTheme = ElementTheme.Dark;
        root.ColumnDefinitions.Add(new() { Width = new GridLength(252) });
        root.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        interactionHost.Content = root; Content = interactionHost;
        var sidebar = new Grid { BorderBrush = LineBrush, BorderThickness = new Thickness(0, 0, 1, 0) };
        sidebar.RowDefinitions.Add(new() { Height = GridLength.Auto });
        sidebar.RowDefinitions.Add(new() { Height = GridLength.Auto });
        sidebar.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        sidebar.RowDefinitions.Add(new() { Height = GridLength.Auto });
        var brand = new Grid { Margin = new Thickness(15, 14, 15, 13), ColumnSpacing = 9 };
        brand.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); brand.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); brand.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var icon = new Image { Width = 30, Height = 30, Source = new BitmapImage(new Uri("ms-appx:///Assets/PageBridge.png")) }; brand.Children.Add(icon);
        var brandTitle = new TextBlock { Text = "PageBridge", FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center }; Grid.SetColumn(brandTitle, 1); brand.Children.Add(brandTitle);
        var add = Tool("plus", null, "添加 PDF 或文件夹");
        var addMenu = new MenuFlyout();
        addMenu.Items.Add(MenuItem("添加 PDF", ImportAsync));
        addMenu.Items.Add(MenuItem("搜索在线文献…", LiteratureSearchAsync));
        addMenu.Items.Add(MenuItem("新建文件夹", CreateFolderAsync));
        addMenu.Items.Add(new MenuFlyoutSeparator());
        var updates = MenuItem("检查更新…", ShowUpdatesAsync);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(updates, "MenuCheckUpdates");
        addMenu.Items.Add(updates); add.Flyout = addMenu;
        Grid.SetColumn(add, 2); brand.Children.Add(add); sidebar.Children.Add(brand);
        var searchArea = new StackPanel { Spacing = 6, Margin = new Thickness(12, 0, 12, 0) };
        search.FontSize = 12; search.Height = 32; search.MinHeight = 0; search.Padding = new Thickness(9, 5, 9, 5); search.CornerRadius = new CornerRadius(7); search.BorderThickness = new Thickness(0);
        searchArea.Children.Add(search);
        var online = Tool("globe", "搜索在线文献", "搜索在线文献", LiteratureSearchAsync); online.HorizontalAlignment = HorizontalAlignment.Stretch; online.HorizontalContentAlignment = HorizontalAlignment.Left; searchArea.Children.Add(online);
        Grid.SetRow(searchArea, 1); sidebar.Children.Add(searchArea);
        var sections = new StackPanel { Margin = new Thickness(12, 0, 12, 8) };
        sections.Children.Add(SectionTitle("文档库")); sections.Children.Add(navigationRows); sections.Children.Add(SectionTitle("文件夹")); sections.Children.Add(folderRows);
        var newFolder = Tool("folder", "新建文件夹", "新建文件夹", CreateFolderAsync); newFolder.Opacity = .65; newFolder.HorizontalAlignment = HorizontalAlignment.Stretch; newFolder.HorizontalContentAlignment = HorizontalAlignment.Left; sections.Children.Add(newFolder);
        var listHeader = new Grid { Margin = new Thickness(5, 15, 0, 4) }; listHeader.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); listHeader.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); listHeader.Children.Add(libraryCaption);
        var sort = Tool("down", "排序", "文档排序"); var sortMenu = new MenuFlyout();
        foreach (var item in new[] { ("最近打开", "recent"), ("最新导入", "imported"), ("最早导入", "oldest"), ("名称 A → Z", "name"), ("名称 Z → A", "nameDescending"), ("页数", "pages") }) { var value = item.Item2; sortMenu.Items.Add(MenuItem(item.Item1, () => { sortOrder = value; RefreshList(); return Task.CompletedTask; })); }
        sort.Flyout = sortMenu; Grid.SetColumn(sort, 1); listHeader.Children.Add(sort); sections.Children.Add(listHeader);
        library.BorderThickness = new Thickness(0); library.Background = Clear; library.Padding = new Thickness(0); library.IsTabStop = true;
        ScrollViewer.SetVerticalScrollBarVisibility(library, ScrollBarVisibility.Disabled); sections.Children.Add(library);
        var sideScroll = new ScrollViewer { Content = sections, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled }; Grid.SetRow(sideScroll, 2); sidebar.Children.Add(sideScroll);
        var bottom = new Grid { Padding = new Thickness(15, 10, 15, 10), BorderThickness = new Thickness(0, 1, 0, 0), BorderBrush = LineBrush };
        bottom.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); bottom.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var demos = Tool("books", "示例 PDF", "示例 PDF", DemosAsync); demos.HorizontalAlignment = HorizontalAlignment.Left; bottom.Children.Add(demos);
        var settings = Tool("settings", null, "翻译服务设置", SettingsAsync); Grid.SetColumn(settings, 1); bottom.Children.Add(settings); Grid.SetRow(bottom, 3); sidebar.Children.Add(bottom);
        root.Children.Add(sidebar); themedBars.Add(sidebar);

        foreach (var height in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto, GridLength.Auto }) workspace.RowDefinitions.Add(new() { Height = height });
        Grid.SetColumn(workspace, 1); root.Children.Add(workspace);
        var header = new Grid { Padding = new Thickness(16, 7, 16, 7), ColumnSpacing = 10, MinHeight = 43 };
        header.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); header.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var names = new Grid { Margin = new Thickness(32, 0, 0, 0), ColumnSpacing = 10, VerticalAlignment = VerticalAlignment.Center }; names.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); names.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); names.Children.Add(title); subtitle.FontSize = 10.5; subtitle.VerticalAlignment = VerticalAlignment.Center; Grid.SetColumn(subtitle, 1); names.Children.Add(subtitle); header.Children.Add(names);
        var sidebarToggle = Tool("sidebar", null, "显示或隐藏文档库", () => { ToggleSidebar(); return Task.CompletedTask; });
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(sidebarToggle, "LibrarySidebarToggle");
        sidebarToggle.HorizontalAlignment = HorizontalAlignment.Left; header.Children.Add(sidebarToggle);
        header.SizeChanged += (_, _) => subtitle.Visibility = header.ActualWidth > 1050 ? Visibility.Visible : Visibility.Collapsed;
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 9, VerticalAlignment = VerticalAlignment.Center };
        var segments = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2 };
        foreach (var label in new[] { "原文", "对照", "译文" }) { int i = mode.Items.Count; mode.Items.Add(label); var button = new Button { Content = label, Width = 44, Height = 27, MinHeight = 0, Padding = new Thickness(0), FontSize = 12, BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(5) }; button.Click += (_, _) => mode.SelectedIndex = i; modeButtons.Add(button); segments.Children.Add(button); }
        modeSegments = new Border { Child = segments, Padding = new Thickness(2), CornerRadius = new CornerRadius(7) }; actions.Children.Add(modeSegments); mode.SelectedIndex = 0;
        foreach (var item in new[] { "100%", "适合宽度", "50%", "75%", "125%", "150%", "200%" }) zoom.Items.Add(item); zoom.SelectedIndex = 0; zoom.MinHeight = 0; zoom.Height = 28; zoom.FontSize = 12; zoom.Padding = new Thickness(7, 2, 7, 2); ToolTipService.SetToolTip(zoom, "缩放，100% 适合阅读区宽度"); actions.Children.Add(zoom);
        actions.Children.Add(Tool("search", null, "搜索文档", () => { ShowDocumentSearch(); return Task.CompletedTask; }));
        translateButton.Content = Row(ReaderIcons.Make("translate"), new TextBlock { Text = "翻译", FontSize = 12 }); translateButton.Height = 28; translateButton.MinHeight = 0; translateButton.Padding = new Thickness(8, 3, 8, 3); translateButton.MinWidth = 0; actions.Children.Add(translateButton);
        exportButton.Content = Row(new TextBlock { Text = "导出 PDF", FontSize = 12 }, ReaderIcons.Make("down", 12)); exportButton.Height = 28; exportButton.MinHeight = 0; exportButton.Padding = new Thickness(8, 3, 8, 3);
        var exportMenu = new MenuFlyout(); foreach (var item in new[] { ("原文 PDF…", "original"), ("译文 PDF…", "translated"), ("双语 PDF…", "bilingual") }) { var kind = item.Item2; exportMenu.Items.Add(MenuItem(item.Item1, () => ExportAsync(kind))); }
        exportMenu.Items.Add(new MenuFlyoutSeparator()); exportMenu.Items.Add(MenuItem("带批注的 PDF…", () => ExportReadingAsync("pdf", ActiveVariant, AnnotationHash(ActiveVariant)))); exportMenu.Items.Add(MenuItem("阅读笔记（Markdown）…", () => ExportReadingAsync("markdown", ActiveVariant, AnnotationHash(ActiveVariant)))); exportMenu.Items.Add(new MenuFlyoutSeparator()); exportMenu.Items.Add(MenuItem("在资源管理器中显示输出", RevealOutputAsync)); exportButton.Flyout = exportMenu; actions.Children.Add(exportButton);
        Grid.SetColumn(actions, 1); header.Children.Add(actions); workspace.Children.Add(header); themedBars.Add(header);
        var toolsBar = new Grid { Padding = new Thickness(16, 3, 16, 5), BorderThickness = new Thickness(0, 0, 0, 1), BorderBrush = LineBrush }; toolsBar.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); toolsBar.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        readingTools.Children.Add(Tool("sidebar", null, "页面导航", () => { TogglePageNavigation(); return Task.CompletedTask; })); readingTools.Children.Add(Divider(true));
        highlightTool = Tool("highlight", "高亮", "为选中文字添加高亮", () => AddMarkupAsync("highlight")); underlineTool = Tool("underline", null, "下划线", () => AddMarkupAsync("underline"));
        readingTools.Children.Add(highlightTool); readingTools.Children.Add(underlineTool); readingTools.Children.Add(Tool("note", "笔记", "记录页面笔记", AddNoteAsync)); readingTools.Children.Add(Tool("bookmark", null, "当前页书签", ToggleBookmarkAsync));
        var copy = Tool("copy", null, "复制选文或页码");
        var copyMenu = new MenuFlyout();
        var copyText = MenuItem("复制选中文字", () => { activePages.CopySelection(); return Task.CompletedTask; }); copyMenu.Items.Add(copyText);
        copyMenu.Items.Add(MenuItem("复制文档与页码", () => { if (selected is not null) { var data = new DataPackage(); data.SetText($"{Text(selected, "name")} · {(ActiveVariant == "translated" ? "译文" : "原文")} · 第 {(int)page.Value} 页"); Clipboard.SetContent(data); } return Task.CompletedTask; }));
        copyMenu.Opening += (_, _) => copyText.IsEnabled = activePages.Selection is not null;
        copy.Flyout = copyMenu; readingTools.Children.Add(copy); toolsBar.Children.Add(readingTools);
        notesTool = Tool("notes", "阅读笔记", "阅读笔记", () => ToggleInspectorAsync("notes")); assistantTool = Tool("sparkles", "助手", "文档助手", () => ToggleInspectorAsync("assistant"));
        var inspectors = Row(selectionCaption, notesTool, assistantTool); Grid.SetColumn(inspectors, 1); toolsBar.Children.Add(inspectors); Grid.SetRow(toolsBar, 1); workspace.Children.Add(toolsBar); themedBars.Add(toolsBar);
        originalPages = new ContinuousPdfPane(backend); translatedPages = new ContinuousPdfPane(backend); activePages = originalPages;
        leftScroll = originalPages.Scroll; rightScroll = translatedPages.Scroll;
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(leftScroll, "OriginalPages");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(rightScroll, "TranslatedPages");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(leftScroll, "原文连续阅读");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(rightScroll, "译文连续阅读"); leftPane = MakePDFPane("原文", leftScroll, false); rightPane = MakePDFPane("译文", rightScroll, true); Grid.SetRow(emptyTranslation, 1); rightPane.Children.Add(emptyTranslation);
        reader.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); reader.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); reader.Children.Add(leftPane); reader.Children.Add(rightPane); Grid.SetColumn(rightPane, 1);
        readerArea.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); readerArea.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); readerArea.Children.Add(pageNavigation); Grid.SetColumn(reader, 1); readerArea.Children.Add(reader); Grid.SetRow(readerArea, 2); workspace.Children.Add(readerArea);
        InitializeStructuredReading();
        InitializeReadingWorkspace();
        var statusBar = new Grid { Padding = new Thickness(16, 6, 16, 6), ColumnSpacing = 8 }; statusBar.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); statusBar.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); statusBar.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); statusBar.Children.Add(ReaderIcons.Make("info", 12)); status.FontSize = 11; status.Opacity = .65; Grid.SetColumn(status, 1); statusBar.Children.Add(status); Grid.SetColumn(progress, 2); statusBar.Children.Add(progress); Grid.SetRow(statusBar, 3); workspace.Children.Add(statusBar); themedBars.Add(statusBar);
        var footer = new Grid { Padding = new Thickness(20, 8, 20, 8), BorderThickness = new Thickness(0, 1, 0, 0), BorderBrush = LineBrush };
        footer.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); footer.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); footer.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); footer.Children.Add(documentStatus);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(page, "ReaderPageNumber");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(page, "当前页码");
        page.Background = Clear; ToolTipService.SetToolTip(page, "输入页码，按 Enter 跳转"); pageCount.FontSize = 12; pageCount.Opacity = .65;
        var paging = Row(Tool("left", null, "上一页", () => ChangePage(-1)), new TextBlock { Text = "第", FontSize = 12, Opacity = .65, VerticalAlignment = VerticalAlignment.Center }, page, pageCount, Tool("right", null, "下一页", () => ChangePage(1))); paging.Spacing = 4; Grid.SetColumn(paging, 1); footer.Children.Add(paging);
        sync.MinWidth = 40; sync.Height = 28; sync.Padding = new Thickness(0); var syncPanel = Row(ReaderIcons.Make("link", 13), new TextBlock { Text = "同步滚动", FontSize = 11, VerticalAlignment = VerticalAlignment.Center }, sync); syncPanel.HorizontalAlignment = HorizontalAlignment.Right; Grid.SetColumn(syncPanel, 2); footer.Children.Add(syncPanel); Grid.SetRow(footer, 4); workspace.Children.Add(footer); themedBars.Add(footer);
        BuildEmptyLibrary(); Grid.SetColumn(emptyLibrary, 1); root.Children.Add(emptyLibrary);
        root.AllowDrop = true; root.DragOver += (_, e) => { if (e.DataView.Contains(StandardDataFormats.StorageItems)) e.AcceptedOperation = DataPackageOperation.Copy; };
        root.Drop += async (_, e) => await Attempt(async () => { if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return; var files = await e.DataView.GetStorageItemsAsync(); await ImportPathsAsync(files.Where(f => f.Path.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)).Select(f => f.Path)); });
        root.ActualThemeChanged += (_, _) => ApplyTheme(); ApplyTheme(); UpdateDocumentState();
    }

    private void ToggleSidebar()
    {
        sidebarVisible = !sidebarVisible;
        root.ColumnDefinitions[0].Width = new GridLength(sidebarVisible ? 252 : 0);
        root.Children[0].Visibility = sidebarVisible ? Visibility.Visible : Visibility.Collapsed;
    }
    private void ApplyTheme()
    {
        root.Background = BarBrush;
        if (modeSegments is not null) modeSegments.Background = Dark ? Brush(47, 47, 47) : Brush(232, 232, 232);
        foreach (var pane in new[] { leftPane, rightPane }) if (pane?.Children[0] is Grid header) header.Background = Dark ? Brush(49, 49, 49) : Brush(230, 230, 230);
        foreach (var element in themedBars) if (element is Panel panel) panel.Background = BarBrush;
        if (root.Children[0] is Grid sidebar) sidebar.Background = Dark ? Brush(38, 38, 38) : Brush(243, 243, 243);
        if (leftScroll is not null) leftScroll.Background = BarBrush;
        if (rightScroll is not null) rightScroll.Background = BarBrush;
        emptyLibrary.Background = BarBrush; search.Background = Dark ? Brush(46, 46, 46) : Brush(232, 232, 232);
        AppWindow.TitleBar.BackgroundColor = Dark ? global::Windows.UI.Color.FromArgb(255, 38, 38, 38) : global::Windows.UI.Color.FromArgb(255, 243, 243, 243);
        AppWindow.TitleBar.ForegroundColor = Dark ? Colors.White : Colors.Black; AppWindow.TitleBar.ButtonBackgroundColor = AppWindow.TitleBar.BackgroundColor; AppWindow.TitleBar.ButtonForegroundColor = AppWindow.TitleBar.ForegroundColor;
        AppWindow.TitleBar.InactiveBackgroundColor = AppWindow.TitleBar.BackgroundColor;
        AppWindow.TitleBar.InactiveForegroundColor = Dark ? global::Windows.UI.Color.FromArgb(255, 155, 155, 155) : global::Windows.UI.Color.FromArgb(255, 100, 100, 100);
        AppWindow.TitleBar.ButtonInactiveBackgroundColor = AppWindow.TitleBar.BackgroundColor;
        AppWindow.TitleBar.ButtonInactiveForegroundColor = AppWindow.TitleBar.InactiveForegroundColor;
        UpdateModeButtons(); RefreshNavigation();
        UpdateReadingTheme();
    }
    private Grid MakePDFPane(string caption, ScrollViewer scroll, bool target)
    {
        var panel = new Grid(); panel.RowDefinitions.Add(new() { Height = GridLength.Auto }); panel.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        var header = new Grid { Padding = new Thickness(16, 4, 16, 4), Background = Dark ? Brush(49, 49, 49) : Brush(230, 230, 230), Tag = "paneHeader" };
        header.Children.Add(new TextBlock { Text = caption, FontSize = 10.5, Opacity = .7 });
        var language = new TextBlock { FontSize = 10.5, Opacity = .4, HorizontalAlignment = HorizontalAlignment.Right, Tag = "paneLanguage" };
        header.Children.Add(language); panel.Children.Add(header); Grid.SetRow(scroll, 1); panel.Children.Add(scroll);
        scroll.ViewChanged += (_, _) => UpdatePDFPaneLabels();
        return panel;
    }
    private void UpdateModeButtons()
    {
        for (int i = 0; i < modeButtons.Count; i++) { modeButtons[i].Background = i == mode.SelectedIndex ? Blue : Clear; modeButtons[i].Foreground = i == mode.SelectedIndex ? new SolidColorBrush(Colors.White) : new SolidColorBrush(Dark ? Colors.White : Colors.Black); modeButtons[i].IsEnabled = i == 0 || HasTranslation; }
        if (leftPane is not null) ((FrameworkElement)leftPane.Children[0]).Visibility = mode.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
        if (rightPane is not null) ((FrameworkElement)rightPane.Children[0]).Visibility = mode.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
        sync.IsEnabled = mode.SelectedIndex == 1 && !ShowingReading;
    }
    private void UpdateDocumentState()
    {
        UpdatePDFPaneLabels();
        bool hasDoc = selected is not null; emptyLibrary.Visibility = !hasDoc && settingsOverlay is null ? Visibility.Visible : Visibility.Collapsed; workspace.Visibility = hasDoc && settingsOverlay is null ? Visibility.Visible : Visibility.Collapsed;
        translateButton.IsEnabled = hasDoc; exportButton.IsEnabled = hasDoc; documentStatus.Text = hasDoc ? StatusName(Text(selected, "status")) : "";
        if (hasDoc && !HasTranslation) mode.SelectedIndex = 0;
        if (translationCancellation is null) translateButton.Content = Row(ReaderIcons.Make("translate"), new TextBlock { Text = !HasTranslation ? "翻译" : "重新翻译", FontSize = 12 });
        if (exportButton.Flyout is MenuFlyout menu) { menu.Items[1].IsEnabled = Text(selected, "translated_path") != ""; menu.Items[2].IsEnabled = Text(selected, "bilingual_path") != ""; }
        UpdateModeButtons();
    }
    private void BuildEmptyLibrary()
    {
        var content = new StackPanel { Spacing = 18, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        content.Children.Add(new Viewbox { Width = 60, Height = 60, Child = ReaderIcons.Make("search", 60), HorizontalAlignment = HorizontalAlignment.Center });
        content.Children.Add(new TextBlock { Text = "把 PDF 带到这里", FontSize = 25, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center });
        content.Children.Add(new TextBlock { Text = "拖入文档，或从电脑选择 PDF。\n保留原文阅读，翻译完成后并排对照。", FontSize = 14, Opacity = .65, TextAlignment = TextAlignment.Center, LineHeight = 24 });
        var import = ActionButton("＋  选择 PDF…", ImportAsync, true); import.Background = Blue; import.Foreground = new SolidColorBrush(Colors.White); import.HorizontalAlignment = HorizontalAlignment.Center; import.Padding = new Thickness(18, 8, 18, 8); content.Children.Add(import);
        var demo = Tool("books", "先打开一份示例 PDF", "示例 PDF", DemosAsync); demo.HorizontalAlignment = HorizontalAlignment.Center; content.Children.Add(demo);
        content.Children.Add(new TextBlock { Text = "本地文档     ·     自定义 API     ·     原文与译文对照", FontSize = 12, Opacity = .4, HorizontalAlignment = HorizontalAlignment.Center }); emptyLibrary.Children.Add(content);
    }
    private IEnumerable<JsonObject> SortedDocuments() => sortOrder switch { "name" => documents.OfType<JsonObject>().OrderBy(d => Text(d, "name"), StringComparer.CurrentCultureIgnoreCase), "nameDescending" => documents.OfType<JsonObject>().OrderByDescending(d => Text(d, "name"), StringComparer.CurrentCultureIgnoreCase), "oldest" => documents.OfType<JsonObject>().OrderBy(d => Number(d, "imported_at")), "pages" => documents.OfType<JsonObject>().OrderByDescending(d => Number(d, "page_count")), "imported" => documents.OfType<JsonObject>().OrderByDescending(d => Number(d, "imported_at")), _ => documents.OfType<JsonObject>().OrderByDescending(d => Number(d, "last_opened")) };
    private void RefreshNavigation()
    {
        navigationRows.Children.Clear(); folderRows.Children.Clear(); string choice = (filter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        foreach (var item in new[] { ("全部文档", "all", "stack"), ("未分类", "unfiled", "tray"), ("收藏", "favorites", "star"), ("最近", "recent", "clock"), ("回收站", "trash", "trash") }) navigationRows.Children.Add(FilterButton(item.Item1, item.Item2, item.Item3, choice));
        foreach (var folder in folders) { var button = FilterButton(Text(folder, "name"), Text(folder, "id"), "folder", choice); button.ContextFlyout = FolderMenu(folder!.AsObject()); folderRows.Children.Add(button); }
        libraryCaption.Text = (filter.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "全部文档";
    }
    private Button FilterButton(string name, string key, string icon, string selectedKey)
    {
        int count = documents.OfType<JsonObject>().Count(d => Flag(d, "trashed") == (key == "trash") && (key switch { "unfiled" => Text(d, "folder_id") == "", "favorites" => Flag(d, "favorite"), "recent" => DateTimeOffset.UtcNow.ToUnixTimeSeconds() - Number(d, "last_opened") <= 30 * 86400, "all" or "trash" => true, _ => Text(d, "folder_id") == key }));
        var grid = new Grid { ColumnSpacing = 8 }; grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); grid.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); grid.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); grid.Children.Add(ReaderIcons.Make(icon)); var label = new TextBlock { Text = name, FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis }; Grid.SetColumn(label, 1); grid.Children.Add(label); var amount = new TextBlock { Text = count.ToString(), FontSize = 11, Opacity = .55 }; Grid.SetColumn(amount, 2); grid.Children.Add(amount);
        var button = new Button { Content = grid, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Background = key == selectedKey ? new SolidColorBrush(global::Windows.UI.Color.FromArgb(36, 10, 132, 255)) : Clear, BorderThickness = new Thickness(0), Padding = new Thickness(7, 6, 7, 6), MinHeight = 0, CornerRadius = new CornerRadius(6) };
        button.Click += (_, _) => { filter.SelectedItem = filter.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == key); }; return button;
    }
    private FrameworkElement DocumentCard(JsonObject doc)
    {
        var row = new Grid { ColumnSpacing = 9, Padding = new Thickness(3, 7, 3, 7) }; row.ColumnDefinitions.Add(new() { Width = new GridLength(38) }); row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        var image = new Image { Width = 38, Height = 52, Stretch = Stretch.Uniform }; row.Children.Add(image);
        var details = new StackPanel { Spacing = 5, VerticalAlignment = VerticalAlignment.Center }; details.Children.Add(new TextBlock { Text = Text(doc, "name"), FontSize = 12, FontWeight = Microsoft.UI.Text.FontWeights.Medium, TextWrapping = TextWrapping.Wrap, MaxLines = 2, TextTrimming = TextTrimming.CharacterEllipsis }); details.Children.Add(new TextBlock { Text = $"{doc["page_count"]} 页 · {StatusName(Text(doc, "status"))}" + (Flag(doc, "favorite") ? "  ★" : ""), FontSize = 10.5, Opacity = .6 }); Grid.SetColumn(details, 1); row.Children.Add(details);
        image.Loaded += async (_, _) => await LoadThumbnailAsync(image, doc, 0, 140); return row;
    }
    private async Task LoadThumbnailAsync(Image image, JsonObject doc, int index, int width, string variant = "original")
    {
        // Only one low-priority preview may enter the backend queue at a time.
        await thumbnailGate.WaitAsync();
        try
        {
            if (windowClosed || !image.IsLoaded) return;
            var key = Text(doc, "id") + $"/{variant}/{index}/{width}/" + Text(doc, variant == "original" ? "input_path" : "translated_path");
            if (!thumbnailPaths.TryGetValue(key, out var path) || !File.Exists(path))
            {
                var result = await backend.CallAsync("render", new JsonObject { ["document_id"] = Text(doc, "id"), ["variant"] = variant, ["page"] = index, ["width"] = width });
                path = Text(result, "path"); thumbnailPaths[key] = path;
            }
            if (!windowClosed && image.IsLoaded) image.Source = new BitmapImage(new Uri(path));
        }
        catch { /* Missing previews must not block the document library. */ }
        finally { thumbnailGate.Release(); }
    }
    private MenuFlyoutItem MenuItem(string name, Func<Task> action) { var item = new MenuFlyoutItem { Text = name }; item.Click += async (_, _) => await Attempt(action); return item; }
    private MenuFlyout DocumentMenu(JsonObject doc)
    {
        var menu = new MenuFlyout(); string id = Text(doc, "id");
        async Task Change(string action, JsonNode? value) { await backend.CallAsync(action, new JsonObject { ["document_id"] = id, ["value"] = value }); await ReloadAsync(Text(selected, "id")); }
        if (Flag(doc, "trashed")) menu.Items.Add(MenuItem("恢复文档", () => Change("trash", false)));
        else {
            menu.Items.Add(MenuItem(Flag(doc, "favorite") ? "取消收藏" : "加入收藏", () => Change("favorite", !Flag(doc, "favorite"))));
            menu.Items.Add(MenuItem("重命名…", async () => { if (await PromptAsync("重命名文档", Text(doc, "name")) is { Length: > 0 } name) { await backend.CallAsync("rename", new JsonObject { ["document_id"] = id, ["name"] = name }); await ReloadAsync(Text(selected, "id")); } }));
            var move = new MenuFlyoutSubItem { Text = "移到文件夹" }; async Task Move(string? folder) { await backend.CallAsync("move", new JsonObject { ["document_id"] = id, ["folder_id"] = folder }); await ReloadAsync(Text(selected, "id")); } move.Items.Add(MenuItem("未分类", () => Move(null))); foreach (var folder in folders) { var folderID = Text(folder, "id"); move.Items.Add(MenuItem(Text(folder, "name"), () => Move(folderID))); } menu.Items.Add(move); menu.Items.Add(new MenuFlyoutSeparator()); menu.Items.Add(MenuItem("移入回收站", () => Change("trash", true)));
        }
        menu.Items.Add(MenuItem("在资源管理器中显示", () => RevealDocumentAsync(doc)));
        return menu;
    }
    private async Task DemosAsync()
    {
        string? demoRoot = null; for (DirectoryInfo? directory = new(AppContext.BaseDirectory); directory is not null; directory = directory.Parent) { foreach (var candidate in new[] { Path.Combine(directory.FullName, "resources", "demos", "pdfs"), Path.Combine(directory.FullName, "demos", "pdfs") }) if (Directory.Exists(candidate)) { demoRoot = candidate; break; } if (demoRoot is not null) break; }
        if (demoRoot is null) throw new InvalidOperationException("示例文件未随安装包找到，请导入本地 PDF。");
        var files = Directory.GetFiles(demoRoot, "*.pdf", SearchOption.AllDirectories); var choices = new ComboBox { MinWidth = 360, HorizontalAlignment = HorizontalAlignment.Stretch }; foreach (var file in files) choices.Items.Add(new ComboBoxItem { Content = Path.GetFileNameWithoutExtension(file), Tag = file }); choices.SelectedIndex = files.Length > 0 ? 0 : -1;
        if (await ShowDialogAsync(new ContentDialog { XamlRoot = root.XamlRoot, Title = "示例 PDF", Content = choices, PrimaryButtonText = "加入文档库", CloseButtonText = "取消", IsPrimaryButtonEnabled = files.Length > 0 }) == ContentDialogResult.Primary) { await ImportPathsAsync([(string)((ComboBoxItem)choices.SelectedItem).Tag]); }
    }
    private async Task SettingsAsync()
    {
        if (settingsOverlay is not null || settingsOpening) return;
        settingsOpening = true;
        try
        {
            settingsOverlay = await ServiceSettings.CreateViewAsync(root.XamlRoot, backend, () => { if (settingsOverlay is not null) root.Children.Remove(settingsOverlay); settingsOverlay = null; UpdateDocumentState(); });
            if (settingsOverlay is ServiceSettingsView view) view.CheckUpdates = () => Attempt(ShowUpdatesAsync);
            Grid.SetColumn(settingsOverlay, 1); root.Children.Add(settingsOverlay); UpdateDocumentState();
        }
        finally { settingsOpening = false; }
    }
    private async Task<bool> CloseSettingsAsync()
    {
        var overlay = settingsOverlay;
        if (overlay is null) return true;
        if (overlay is ServiceSettingsView view && !await view.RequestCloseAsync()) return false;
        if (ReferenceEquals(settingsOverlay, overlay))
        {
            root.Children.Remove(overlay); settingsOverlay = null; UpdateDocumentState();
        }
        return true;
    }

    private async Task ReconcileSelectionAsync()
    {
        if (loading) return;
        // A library filter can hide the running document. Keep its reader and
        // cancel button available until the task has ended.
        if (selected is not null && (translationCancellation is not null || translationPreparing)) return;
        var visible = library.Items.OfType<ListViewItem>().Select(item => ((DocumentRow)item.Tag).Document).ToList();
        if (selected is not null && !Flag(selected, "trashed") && visible.Any(d => Text(d, "id") == Text(selected, "id"))) return;
        var first = visible.FirstOrDefault(d => !Flag(d, "trashed"));
        if (first is not null) await OpenAsync(first);
        else { ResetDocumentSearch(); selected = null; selectionGeneration++; renderGeneration++; navigationGeneration++; navigationIdentity = ""; await annotationsPanel.BindAsync("", false); await assistantPanel.BindAsync("", ""); HideInspector(); annotationHashes.Clear(); annotationsByVariant.Clear(); pageThumbnails.Items.Clear(); outlineList.Items.Clear(); bookmarkList.Items.Clear(); bindingPages = false; originalPages.Clear(); translatedPages.Clear(); structuredReading.Clear(); ApplyStructuredVisibility(); if (!sidebarVisible) ToggleSidebar(); UpdateDocumentState(); }
    }
}
