using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using global::Windows.Storage.Pickers;
using WinRT.Interop;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private readonly Grid readingInspector = new() { Width = 346, Visibility = Visibility.Collapsed, BorderThickness = new Thickness(1, 0, 0, 0), VerticalAlignment = VerticalAlignment.Stretch };
    private readonly Grid inspectorContent = new();
    private readonly ListView outlineList = new(), bookmarkList = new();
    private readonly TextBlock navigationEmpty = new() { TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, FontSize = 12, Opacity = .65, Margin = new Thickness(14), VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock selectionCaption = new() { FontSize = 10, Opacity = .65, VerticalAlignment = VerticalAlignment.Center };
    private readonly List<Button> navigationTabs = [], inspectorTabs = [];
    private readonly Dictionary<string, string> annotationHashes = [];
    private readonly Dictionary<string, JsonArray> annotationsByVariant = [];
    private Button highlightTool = null!, underlineTool = null!, notesTool = null!, assistantTool = null!;
    private AnnotationsPanel annotationsPanel = null!;
    private AssistantPanel assistantPanel = null!;
    private bool showsPageNavigation = true, updatingNavigation, switchingVariant;
    private int navigationTab, navigationGeneration;
    private string? inspectorKind;
    private string navigationIdentity = "";
    private string ActiveVariant => ReferenceEquals(activePages, translatedPages) ? "translated" : "original";
    private string AnnotationHash(string variant) => annotationHashes.GetValueOrDefault(variant, "");

    private void InitializeReadingWorkspace()
    {
        pageNavigation.RowDefinitions.Add(new() { Height = GridLength.Auto }); pageNavigation.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        pageNavigation.BorderThickness = new Thickness(0, 0, 1, 0);
        var tabs = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2, Margin = new Thickness(9), HorizontalAlignment = HorizontalAlignment.Center };
        foreach (var label in new[] { "页面", "目录", "书签" })
        {
            int tab = navigationTabs.Count;
            var button = new Button { Content = label, FontSize = 11, Width = 44, Height = 25, MinWidth = 0, MinHeight = 0, Padding = new Thickness(0), BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(4) };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(button, "NavigationTab" + tab);
            button.Click += (_, _) => { navigationTab = tab; UpdateNavigationDisplay(); }; tabs.Children.Add(button); navigationTabs.Add(button);
        }
        pageNavigation.Children.Add(tabs);
        foreach (var list in new[] { pageThumbnails, outlineList, bookmarkList })
        {
            list.Background = Clear; list.BorderThickness = new Thickness(0); list.Padding = new Thickness(0, 6, 0, 10);
            ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled); Grid.SetRow(list, 1); pageNavigation.Children.Add(list);
            list.SelectionChanged += (_, _) => { if (!updatingNavigation && list.SelectedItem is ListViewItem { Tag: int number }) NavigateReading(ActiveVariant, number); };
        }
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(pageThumbnails, "PageThumbnails");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(outlineList, "DocumentOutline");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(bookmarkList, "DocumentBookmarks");
        Grid.SetRow(navigationEmpty, 1); pageNavigation.Children.Add(navigationEmpty);
        readerArea.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); Grid.SetColumn(readingInspector, 2); readerArea.Children.Add(readingInspector);
        readingInspector.RowDefinitions.Add(new() { Height = GridLength.Auto }); readingInspector.RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        var inspectorHeader = new Grid { Padding = new Thickness(10), ColumnSpacing = 8, BorderThickness = new Thickness(0, 0, 0, 1) };
        inspectorHeader.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); inspectorHeader.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var segments = new Grid { ColumnSpacing = 2 }; segments.ColumnDefinitions.Add(new()); segments.ColumnDefinitions.Add(new());
        foreach (var (label, kind) in new[] { ("助手", "assistant"), ("笔记", "notes") })
        {
            var button = new Button { Content = label, HorizontalAlignment = HorizontalAlignment.Stretch, Height = 26, MinHeight = 0, Padding = new Thickness(0), BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(4) };
            button.Click += async (_, _) => await Attempt(() => ShowInspectorAsync(kind)); Grid.SetColumn(button, inspectorTabs.Count); segments.Children.Add(button); inspectorTabs.Add(button);
        }
        inspectorHeader.Children.Add(segments);
        var close = Tool("close", null, "关闭阅读侧栏", () => { HideInspector(); return Task.CompletedTask; }); Grid.SetColumn(close, 1); inspectorHeader.Children.Add(close); readingInspector.Children.Add(inspectorHeader);
        Grid.SetRow(inspectorContent, 1); readingInspector.Children.Add(inspectorContent);
        annotationsPanel = new AnnotationsPanel(backend, ShowDialogAsync, NavigateReading, AddMarkupAsync, AddNoteAsync, ToggleBookmarkAsync, ExportReadingAsync);
        annotationsPanel.AnnotationsChanged += AcceptAnnotations;
        annotationsPanel.VariantChanged += value => { if (!switchingVariant) ActivateVariant(value); };
        assistantPanel = new AssistantPanel(backend, (number, variant) => NavigateReading(variant, number), () => Math.Max(0, (int)page.Value - 1), () => ActiveVariant, () => activePages.Selection?.Text ?? "", SaveAssistantNoteAsync, ShowDialogAsync, () => activePages.Selection?.Page ?? Math.Max(0, (int)page.Value - 1), () => activePages.Selection?.Variant ?? ActiveVariant);
        inspectorContent.Children.Add(assistantPanel); inspectorContent.Children.Add(annotationsPanel);
        readerArea.SizeChanged += (_, _) => UpdateReadingVisibility();
        foreach (var pane in new[] { originalPages, translatedPages })
        {
            pane.SelectionChanged += ReaderSelectionChanged;
            pane.SelectionAction += async (source, action) => await Attempt(async () => { ActivateVariant(source.Variant); if (action == "assistant") { await ShowInspectorAsync("assistant"); assistantPanel.UseSelectionContext(); } else if (action == "note") await AddNoteAsync(); else await AddMarkupAsync(action); });
            pane.AnnotationInvoked += async (source, id) => await Attempt(async () => { ActivateVariant(source.Variant); await ShowInspectorAsync("notes"); await annotationsPanel.SetVariantAsync(source.Variant); await annotationsPanel.EditAnnotationAsync(id); });
        }
        UpdateReadingTheme(); UpdateReadingVisibility(); UpdateSelectionTools();
    }

    private void TogglePageNavigation()
    {
        showsPageNavigation = !showsPageNavigation;
        if (showsPageNavigation) HideInspector();
        UpdateReadingVisibility(); UpdateNavigationPage();
    }
    private void UpdateReadingVisibility()
    {
        pageNavigation.Visibility = showsPageNavigation && (inspectorKind is null || readerArea.ActualWidth >= 1180) ? Visibility.Visible : Visibility.Collapsed;
        readingInspector.Visibility = inspectorKind is null ? Visibility.Collapsed : Visibility.Visible;
    }
    private void HideInspector() { inspectorKind = null; UpdateReadingVisibility(); UpdateReadingTheme(); }
    private Task ToggleInspectorAsync(string kind) { if (inspectorKind == kind) { HideInspector(); return Task.CompletedTask; } return ShowInspectorAsync(kind); }
    private async Task ShowInspectorAsync(string kind)
    {
        if (selected is null) return;
        inspectorKind = kind; assistantPanel.Visibility = kind == "assistant" ? Visibility.Visible : Visibility.Collapsed; annotationsPanel.Visibility = kind == "notes" ? Visibility.Visible : Visibility.Collapsed;
        UpdateReadingVisibility(); UpdateReadingTheme();
        if (kind == "assistant") await assistantPanel.BindAsync(Text(selected, "id"), Text(selected, "name"));
        else await annotationsPanel.BindAsync(Text(selected, "id"), Text(selected, "translated_path") != "", ActiveVariant, (int)page.Value - 1);
        UpdateSelectionTools();
    }
    private void UpdateReadingTheme()
    {
        pageNavigation.Background = Dark ? Brush(38, 38, 38) : Brush(243, 243, 243); pageNavigation.BorderBrush = LineBrush;
        readingInspector.Background = BarBrush; readingInspector.BorderBrush = LineBrush;
        if (readingInspector.Children.Count > 0 && readingInspector.Children[0] is Grid header) header.BorderBrush = LineBrush;
        for (int i = 0; i < navigationTabs.Count; i++) navigationTabs[i].Background = i == navigationTab ? (Dark ? Brush(67, 67, 67) : Brush(255, 255, 255)) : Clear;
        for (int i = 0; i < inspectorTabs.Count; i++) inspectorTabs[i].Background = inspectorKind == (i == 0 ? "assistant" : "notes") ? (Dark ? Brush(67, 67, 67) : Brush(232, 232, 232)) : Clear;
        if (notesTool is not null) notesTool.Background = inspectorKind == "notes" ? new SolidColorBrush(global::Windows.UI.Color.FromArgb(35, 10, 132, 255)) : Clear;
        if (assistantTool is not null) assistantTool.Background = inspectorKind == "assistant" ? new SolidColorBrush(global::Windows.UI.Color.FromArgb(35, 10, 132, 255)) : Clear;
    }
    private void UpdateNavigationDisplay()
    {
        pageThumbnails.Visibility = navigationTab == 0 ? Visibility.Visible : Visibility.Collapsed;
        outlineList.Visibility = navigationTab == 1 ? Visibility.Visible : Visibility.Collapsed;
        bookmarkList.Visibility = navigationTab == 2 ? Visibility.Visible : Visibility.Collapsed;
        navigationEmpty.Text = navigationTab == 1 ? "这份 PDF 没有内置目录" : "点击阅读工具栏的书签，标记重要页面。";
        navigationEmpty.Visibility = (navigationTab == 1 && outlineList.Items.Count == 0) || (navigationTab == 2 && bookmarkList.Items.Count == 0) ? Visibility.Visible : Visibility.Collapsed;
        UpdateReadingTheme(); UpdateNavigationPage();
    }

    private async Task BuildPageNavigationAsync()
    {
        if (selected is null) return;
        var doc = selected; string id = Text(doc, "id"), variant = ActiveVariant;
        string key = id + "/" + variant + "/" + Text(doc, variant == "original" ? "input_path" : "translated_path");
        if (navigationIdentity == key) { UpdateNavigationPage(); return; }
        navigationIdentity = key; int token = ++navigationGeneration;
        updatingNavigation = true;
        try
        {
            pageThumbnails.Items.Clear(); outlineList.Items.Clear(); bookmarkList.Items.Clear();
            int count = doc["page_count"]!.GetValue<int>();
            for (int i = 0; i < count; i++)
            {
                int index = i; var image = new Image { Width = 112, Height = 145, Stretch = Stretch.Uniform };
                image.ImageOpened += (_, _) => { if (image.Source is BitmapImage bitmap && bitmap.PixelWidth > 0) image.Height = 112d * bitmap.PixelHeight / bitmap.PixelWidth; };
                image.Loaded += async (_, _) => await LoadThumbnailAsync(image, doc, index, 240, variant);
                var border = new Border { Child = image, BorderThickness = new Thickness(3), CornerRadius = new CornerRadius(4), BorderBrush = Clear };
                var stack = new StackPanel { Spacing = 5 }; stack.Children.Add(border); stack.Children.Add(new TextBlock { Text = (i + 1).ToString(), FontSize = 11, HorizontalAlignment = HorizontalAlignment.Center });
                var item = new ListViewItem { Content = stack, Tag = i, Padding = new Thickness(4), Margin = new Thickness(0, 5, 0, 5), HorizontalContentAlignment = HorizontalAlignment.Center };
                foreach (var name in new[] { "ListViewItemBackgroundSelected", "ListViewItemBackgroundSelectedPointerOver", "ListViewItemSelectionIndicatorBrush", "ListViewItemSelectionIndicatorBrushPointerOver" }) item.Resources[name] = Clear;
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(item, "跳转到第 " + (i + 1) + " 页"); pageThumbnails.Items.Add(item);
            }
        }
        finally { updatingNavigation = false; }
        UpdateNavigationDisplay();
        var data = await backend.CallAsync("document_navigation", new JsonObject { ["document_id"] = id, ["variant"] = variant });
        if (token != navigationGeneration || navigationIdentity != key) return;
        foreach (var entry in data?["entries"]?.AsArray() ?? [])
        {
            int number = entry?["page"]?.GetValue<int>() ?? 0, depth = entry?["depth"]?.GetValue<int>() ?? 0;
            var stack = new StackPanel { Spacing = 4, Margin = new Thickness(Math.Min(3, depth) * 8, 0, 0, 0) };
            stack.Children.Add(new TextBlock { Text = Text(entry, "title"), FontSize = 11, TextWrapping = TextWrapping.Wrap, MaxLines = 3 });
            stack.Children.Add(new TextBlock { Text = "第 " + (number + 1) + " 页", FontSize = 10, Opacity = .6 });
            outlineList.Items.Add(new ListViewItem { Content = stack, Tag = number, Padding = new Thickness(9, 8, 8, 8), HorizontalContentAlignment = HorizontalAlignment.Stretch });
        }
        RefreshBookmarks(); UpdateNavigationDisplay();
    }

    private void UpdateNavigationPage()
    {
        if (pageThumbnails.Items.Count == 0) return;
        updatingNavigation = true;
        try
        {
            int current = Math.Max(0, (int)page.Value - 1);
            foreach (var item in pageThumbnails.Items.OfType<ListViewItem>())
                if (item.Content is StackPanel stack && stack.Children[0] is Border border) border.BorderBrush = (int)item.Tag == current ? Blue : Clear;
            if (current < pageThumbnails.Items.Count)
            {
                pageThumbnails.SelectedIndex = current;
                if (pageNavigation.Visibility == Visibility.Visible && navigationTab == 0) pageThumbnails.ScrollIntoView(pageThumbnails.Items[current]);
            }
            bookmarkList.SelectedItem = bookmarkList.Items.OfType<ListViewItem>().FirstOrDefault(i => (int)i.Tag == current);
        }
        finally { updatingNavigation = false; }
        annotationsPanel?.SetCurrentPage(Math.Max(0, (int)page.Value - 1)); assistantPanel?.RefreshContext();
    }

    private void RefreshBookmarks()
    {
        updatingNavigation = true;
        try
        {
            bookmarkList.Items.Clear();
            foreach (var item in (annotationsByVariant.GetValueOrDefault(ActiveVariant) ?? []).Where(i => Text(i, "kind") == "bookmark").OrderBy(i => i?["page"]?.GetValue<int>() ?? 0))
            {
                int number = item?["page"]?.GetValue<int>() ?? 0;
                bookmarkList.Items.Add(new ListViewItem { Content = Row(ReaderIcons.Make("bookmark", 14), new TextBlock { Text = "第 " + (number + 1) + " 页", FontSize = 12 }), Tag = number, Padding = new Thickness(11) });
            }
        }
        finally { updatingNavigation = false; }
        UpdateNavigationDisplay();
    }
    private void AcceptAnnotations(string variant, string hash, JsonArray items)
    {
        annotationHashes[variant] = hash; annotationsByVariant[variant] = (JsonArray)items.DeepClone();
        (variant == "original" ? originalPages : translatedPages).SetAnnotations(items); RefreshBookmarks();
    }
    private async Task LoadReadingAsync()
    {
        if (selected is null) return;
        string id = Text(selected, "id"); int generation = selectionGeneration;
        foreach (var variant in Text(selected, "translated_path") == "" ? new[] { "original" } : new[] { "original", "translated" })
        {
            var result = await backend.CallAsync("annotations", new JsonObject { ["document_id"] = id, ["variant"] = variant });
            if (generation != selectionGeneration || id != Text(selected, "id")) return;
            AcceptAnnotations(variant, Text(result, "source_sha256"), result?["annotations"]?.AsArray() ?? []);
        }
        await annotationsPanel.BindAsync(id, Text(selected, "translated_path") != "", ActiveVariant, (int)page.Value - 1);
        if (generation != selectionGeneration) return;
        await BuildPageNavigationAsync();
        if (generation != selectionGeneration) return;
        if (inspectorKind == "assistant") await assistantPanel.BindAsync(id, Text(selected, "name"));
        UpdateSelectionTools();
    }
    private void ReaderSelectionChanged(ContinuousPdfPane pane)
    {
        if (!ReferenceEquals(pane, activePages)) return;
        UpdateSelectionTools(); assistantPanel?.RefreshContext();
    }
    private void UpdateSelectionTools()
    {
        if (highlightTool is null) return;
        bool selectedText = activePages?.Selection is { Text.Length: > 0 };
        highlightTool.IsEnabled = selectedText; underlineTool.IsEnabled = selectedText;
        selectionCaption.Text = selectedText ? "已选 " + activePages!.Selection!.Text.Length + " 字" : "";
        annotationsPanel?.SetSelectionAvailable(selectedText);
    }
    private void ActivateVariant(string variant)
    {
        bool wasStructured = structuredActive;
        structuredActive = false;
        if (ShowingReading && variant == "translated") variant = "original";
        if (variant is not ("original" or "translated") || (variant == "translated" && Text(selected, "translated_path") == "")) return;
        var next = variant == "original" ? originalPages : translatedPages;
        if (wasStructured || !ReferenceEquals(activePages, next))
        {
            activePages = next; OtherPane(next).ClearSelection();
            // With scroll sync off each pane keeps its own page. Switching
            // focus must change the annotation target without scrolling it.
            if (!bindingPages && selected is not null)
            {
                bool wasLoading = loading; loading = true;
                page.Value = Math.Clamp(next.CurrentPage, 1, page.Maximum); loading = wasLoading;
                RememberReading(Text(selected, "id"), (int)page.Value);
            }
        }
        if (mode.SelectedIndex == 0 && variant == "translated") mode.SelectedIndex = 2;
        else if (mode.SelectedIndex == 2 && variant == "original" && !ShowingReading) mode.SelectedIndex = 0;
        if (!bindingPages && selected is not null) _ = Attempt(async () => { switchingVariant = true; try { await annotationsPanel.SetVariantAsync(variant); } finally { switchingVariant = false; } await BuildPageNavigationAsync(); });
        UpdateSelectionTools(); UpdateNavigationPage();
    }
    private void NavigateReading(string variant, int number)
    {
        ActivateVariant(variant); page.Value = Math.Clamp(number + 1, 1, page.Maximum); JumpToPage((int)page.Value); UpdateNavigationPage();
    }
    private JsonObject NoteDraft(string? note = null)
    {
        var selection = activePages.Selection;
        return new JsonObject { ["kind"] = "note", ["page"] = selection?.Page ?? Math.Max(0, (int)page.Value - 1), ["regions"] = selection?.Regions ?? [], ["selected_text"] = selection?.Text ?? "", ["note"] = note ?? "" };
    }
    private async Task AddNoteAsync()
    {
        if (selected is null) return;
        var draft = NoteDraft(); string variant = ActiveVariant; int generation = selectionGeneration;
        await ShowInspectorAsync("notes"); if (generation == selectionGeneration) await annotationsPanel.CreateNoteAsync(draft, variant);
    }
    private async Task SaveAssistantNoteAsync(string text)
    {
        if (selected is null) return;
        string variant = ActiveVariant, id = Text(selected, "id"); int generation = selectionGeneration;
        var draft = NoteDraft(text);
        await ShowInspectorAsync("notes");
        if (generation == selectionGeneration && id == Text(selected, "id")) await annotationsPanel.CreateNoteAsync(draft, variant);
    }
    private async Task AddMarkupAsync(string kind)
    {
        if (selected is null || activePages.Selection is not { } selection) { status.Text = "请先在 PDF 中拖动选择文字，再添加高亮或下划线。"; return; }
        string id = Text(selected, "id"); int generation = selectionGeneration;
        var response = await backend.CallAsync("save_annotation", new JsonObject { ["document_id"] = id, ["variant"] = selection.Variant, ["source_sha256"] = selection.SourceHash, ["annotation"] = new JsonObject { ["kind"] = kind, ["page"] = selection.Page, ["regions"] = selection.Regions, ["selected_text"] = selection.Text, ["note"] = "" } });
        if (generation != selectionGeneration || id != Text(selected, "id")) return;
        AcceptAnnotations(selection.Variant, Text(response, "source_sha256"), response?["annotations"]?.AsArray() ?? []);
        var sourcePane = selection.Variant == "original" ? originalPages : translatedPages;
        if (ReferenceEquals(sourcePane.Selection, selection)) sourcePane.ClearSelection();
        await annotationsPanel.RefreshAsync();
        status.Text = kind == "highlight" ? "已添加高亮" : "已添加下划线";
    }
    private async Task ToggleBookmarkAsync()
    {
        if (selected is null) return;
        string id = Text(selected, "id"), variant = ActiveVariant; int number = Math.Max(0, (int)page.Value - 1), generation = selectionGeneration;
        var existing = (annotationsByVariant.GetValueOrDefault(variant) ?? []).FirstOrDefault(a => Text(a, "kind") == "bookmark" && a?["page"]?.GetValue<int>() == number);
        var args = new JsonObject { ["document_id"] = id, ["variant"] = variant, ["source_sha256"] = AnnotationHash(variant) };
        if (existing is not null) args["annotation_id"] = Text(existing, "id"); else args["annotation"] = new JsonObject { ["kind"] = "bookmark", ["page"] = number, ["regions"] = new JsonArray(), ["note"] = "", ["selected_text"] = "" };
        var response = await backend.CallAsync(existing is null ? "save_annotation" : "delete_annotation", args);
        if (generation != selectionGeneration || id != Text(selected, "id")) return;
        AcceptAnnotations(variant, Text(response, "source_sha256"), response?["annotations"]?.AsArray() ?? []); await annotationsPanel.RefreshAsync();
        status.Text = existing is null ? "已添加当前页书签" : "已移除当前页书签";
    }
    private async Task ExportReadingAsync(string kind, string variant, string hash)
    {
        if (selected is null) return;
        string id = Text(selected, "id");
        var picker = new FileSavePicker { SuggestedFileName = Path.GetFileNameWithoutExtension(Text(selected, "name")) + (kind == "pdf" ? "-批注" : "-阅读笔记") };
        picker.FileTypeChoices.Add(kind == "pdf" ? "PDF 文档" : "Markdown 笔记", new List<string> { kind == "pdf" ? ".pdf" : ".md" }); InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        var file = await picker.PickSaveFileAsync(); if (file is null) return;
        await backend.CallAsync(kind == "pdf" ? "export_annotations" : "export_notes", new JsonObject { ["document_id"] = id, ["variant"] = variant, ["source_sha256"] = hash, ["destination"] = file.Path }); status.Text = "已导出：" + file.Path;
    }
}
