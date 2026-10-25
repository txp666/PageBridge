using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace PageBridge.Windows;

/// <summary>Document-bound notes and annotations; PDF geometry stays with the reader.</summary>
internal sealed class AnnotationsPanel : Grid, IDisposable
{
    private readonly BackendClient backend;
    private readonly Func<ContentDialog, Task<ContentDialogResult>> showDialog;
    private readonly Action<string, int> navigate;
    private readonly Func<string, Task> createMarkup;
    private readonly Func<Task> addNote, toggleBookmark;
    private readonly Func<string, string, string, Task> export;
    private readonly ListView list = new() { SelectionMode = ListViewSelectionMode.None, Padding = new Thickness(0), BorderThickness = new Thickness(0), Background = new SolidColorBrush(Colors.Transparent) };
    private readonly Grid content = new();
    private readonly StackPanel empty = new() { Spacing = 9, Margin = new Thickness(22), VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock emptyTitle = new() { Text = "还没有批注", FontSize = 16, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextAlignment = TextAlignment.Center };
    private readonly TextBlock emptyDescription = new() { Text = "在 PDF 中选择文字以高亮或划线，也可以为当前页写笔记、添加书签。", FontSize = 12, Opacity = .65, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center };
    private readonly TextBlock status = new() { FontSize = 11, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
    private readonly Button originalButton, translatedButton, newNoteButton, highlightButton, underlineButton, bookmarkButton, pdfButton, notesButton;
    private readonly SolidColorBrush lineBrush = new(Colors.Gray);
    private readonly SolidColorBrush selectionBrush = new(Colors.Gray);
    private readonly List<Button> rowActions = [];
    private JsonArray annotations = [];
    private string documentId = "", variant = "original", sourceSHA = "";
    private int currentPage, generation;
    private bool translatedAvailable, selectionAvailable, disposed, busy, editing;

    internal string SelectedVariant => variant;
    internal string SourceSHA => sourceSHA;
    internal event Action<string>? VariantChanged;
    internal event Action<string, string, JsonArray>? AnnotationsChanged;

    internal AnnotationsPanel(BackendClient backend,
        Func<ContentDialog, Task<ContentDialogResult>> showDialog,
        Action<string, int> navigate, Func<string, Task> createMarkup,
        Func<Task> addNote, Func<Task> toggleBookmark,
        Func<string, string, string, Task> export)
    {
        this.backend = backend; this.showDialog = showDialog; this.navigate = navigate;
        this.createMarkup = createMarkup; this.addNote = addNote;
        this.toggleBookmark = toggleBookmark; this.export = export;
        HorizontalAlignment = HorizontalAlignment.Stretch; VerticalAlignment = VerticalAlignment.Stretch;
        MinWidth = 260;
        AutomationProperties.SetAutomationId(this, "AnnotationsPanel");
        RowDefinitions.Add(new() { Height = GridLength.Auto });
        RowDefinitions.Add(new() { Height = new GridLength(1, GridUnitType.Star) });
        RowDefinitions.Add(new() { Height = GridLength.Auto });

        var header = new StackPanel { Spacing = 12, Padding = new Thickness(16, 14, 16, 10), BorderThickness = new Thickness(0, 0, 0, 1), BorderBrush = lineBrush };
        var titleRow = new Grid(); titleRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); titleRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        titleRow.Children.Add(new TextBlock { Text = "批注与笔记", FontSize = 14, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        newNoteButton = IconButton("note", "在选区或当前页添加笔记", "AnnotationAddNote", () => this.addNote());
        Grid.SetColumn(newNoteButton, 1); titleRow.Children.Add(newNoteButton); header.Children.Add(titleRow);

        var sourceRow = new Grid { ColumnSpacing = 8 };
        sourceRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); sourceRow.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        sourceRow.Children.Add(new TextBlock { Text = "批注来源", FontSize = 12, Opacity = .7, VerticalAlignment = VerticalAlignment.Center });
        var sourceChoices = new Grid { ColumnSpacing = 2, Padding = new Thickness(2), Background = selectionBrush, CornerRadius = new CornerRadius(5) };
        sourceChoices.ColumnDefinitions.Add(new()); sourceChoices.ColumnDefinitions.Add(new());
        originalButton = SourceButton("原文", "AnnotationSourceOriginal", "original");
        translatedButton = SourceButton("译文", "AnnotationSourceTranslated", "translated");
        sourceChoices.Children.Add(originalButton); Grid.SetColumn(translatedButton, 1); sourceChoices.Children.Add(translatedButton);
        Grid.SetColumn(sourceChoices, 1); sourceRow.Children.Add(sourceChoices); header.Children.Add(sourceRow);

        var tools = new Grid(); tools.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); tools.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var marks = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        highlightButton = IconButton("highlight", "高亮选中文字", "AnnotationHighlight", () => this.createMarkup("highlight"));
        underlineButton = IconButton("underline", "为选中文字添加下划线", "AnnotationUnderline", () => this.createMarkup("underline"));
        marks.Children.Add(highlightButton); marks.Children.Add(underlineButton); tools.Children.Add(marks);
        bookmarkButton = IconButton("bookmark", "切换当前页书签", "AnnotationBookmark", () => this.toggleBookmark());
        Grid.SetColumn(bookmarkButton, 1); tools.Children.Add(bookmarkButton); header.Children.Add(tools);
        AutomationProperties.SetAutomationId(status, "AnnotationStatus"); header.Children.Add(status);
        Children.Add(header);

        AutomationProperties.SetAutomationId(list, "AnnotationList");
        ScrollViewer.SetHorizontalScrollBarVisibility(list, ScrollBarVisibility.Disabled);
        ScrollViewer.SetVerticalScrollBarVisibility(list, ScrollBarVisibility.Auto);
        content.Children.Add(list);
        var bubble = ReaderIcons.Make("notes", 35); bubble.Opacity = .45; bubble.HorizontalAlignment = HorizontalAlignment.Center;
        empty.Children.Add(bubble); empty.Children.Add(emptyTitle); empty.Children.Add(emptyDescription);
        content.Children.Add(empty); Grid.SetRow(content, 1); Children.Add(content);

        var footer = new Grid { Padding = new Thickness(12, 12, 12, 12), ColumnSpacing = 8, BorderThickness = new Thickness(0, 1, 0, 0), BorderBrush = lineBrush };
        footer.ColumnDefinitions.Add(new()); footer.ColumnDefinitions.Add(new());
        pdfButton = TextButton("导出批注 PDF", "AnnotationExportPDF", () => ExportAsync("pdf"));
        notesButton = TextButton("导出笔记", "AnnotationExportMarkdown", () => ExportAsync("markdown"));
        footer.Children.Add(pdfButton); Grid.SetColumn(notesButton, 1); footer.Children.Add(notesButton);
        Grid.SetRow(footer, 2); Children.Add(footer);
        Loaded += (_, _) => ApplyTheme(); ActualThemeChanged += (_, _) => ApplyTheme();
        ApplyTheme(); UpdateEnabled();
    }

    internal async Task BindAsync(string documentId, bool hasTranslated, string variant = "original", int page = 0)
    {
        if (disposed) return;
        generation++;
        this.documentId = documentId; translatedAvailable = hasTranslated;
        this.variant = variant == "translated" && hasTranslated ? "translated" : "original";
        currentPage = Math.Max(0, page); selectionAvailable = false; sourceSHA = ""; annotations = [];
        SetStatus(""); Render();
        if (documentId.Length > 0) await RefreshAsync();
    }

    internal async Task SetVariantAsync(string value)
    {
        value = value == "translated" && translatedAvailable ? "translated" : "original";
        if (disposed || variant == value) return;
        generation++; variant = value; sourceSHA = ""; selectionAvailable = false; annotations = [];
        SetStatus(""); Render(); VariantChanged?.Invoke(variant);
        await RefreshAsync();
    }

    internal void SetCurrentPage(int page) { currentPage = Math.Max(0, page); UpdateEnabled(); }
    internal void SetSelectionAvailable(bool available) { selectionAvailable = available; UpdateEnabled(); }

    internal async Task RefreshAsync()
    {
        if (disposed || documentId.Length == 0) return;
        int version = generation; string id = documentId, sourceVariant = variant;
        var result = await backend.CallAsync("annotations", new JsonObject { ["document_id"] = id, ["variant"] = sourceVariant });
        if (!Current(version, id, sourceVariant)) return;
        ApplyEnvelope(result);
    }

    /// <summary>Root supplies the captured page, regions and quote before the editor takes focus.</summary>
    internal async Task CreateNoteAsync(JsonObject draft, string? sourceVariant = null)
    {
        if (disposed || editing || documentId.Length == 0) return;
        string id = documentId;
        if (sourceVariant is not null && sourceVariant != variant) await SetVariantAsync(sourceVariant);
        if (documentId != id || sourceSHA.Length == 0) return;
        var note = (JsonObject)draft.DeepClone(); note["kind"] = "note";
        if (note["page"] is null) note["page"] = currentPage;
        if (note["regions"] is null) note["regions"] = new JsonArray();
        await EditAsync(note, true);
    }

    internal async Task EditAnnotationAsync(string id)
    {
        var annotation = annotations.OfType<JsonObject>().FirstOrDefault(item => Text(item, "id") == id);
        if (annotation is not null) await EditAsync((JsonObject)annotation.DeepClone(), false);
    }

    private async Task EditAsync(JsonObject annotation, bool isNew)
    {
        if (disposed || editing || sourceSHA.Length == 0 || XamlRoot is null) return;
        editing = true;
        int version = generation; string id = documentId, sourceVariant = variant, hash = sourceSHA;
        try
        {
            var editor = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 170, MaxHeight = 260, MaxLength = 8000, HorizontalAlignment = HorizontalAlignment.Stretch, PlaceholderText = "记录想法、问题或阅读要点…" };
            // WinUI must enter multiline mode before assigning a saved note.
            editor.Text = Text(annotation, "note");
            AutomationProperties.SetAutomationId(editor, "AnnotationNoteEditor"); AutomationProperties.SetName(editor, "笔记内容");
            ScrollViewer.SetVerticalScrollBarVisibility(editor, ScrollBarVisibility.Auto);
            var body = new StackPanel { Spacing = 12, Width = Math.Max(220, Math.Min(460, XamlRoot.Size.Width - 110)) };
            string quote = Text(annotation, "selected_text");
            if (quote.Length > 0)
            {
                body.Children.Add(new TextBlock { Text = "选中文字", FontSize = 12, Opacity = .65 });
                body.Children.Add(new TextBlock { Text = quote.Length > 600 ? quote[..600] + "…" : quote, FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxLines = 4, TextTrimming = TextTrimming.CharacterEllipsis, Opacity = .75, IsTextSelectionEnabled = true });
            }
            body.Children.Add(editor);
            body.Children.Add(new TextBlock { Text = "保存在本机，可随批注 PDF 或 Markdown 笔记导出。", FontSize = 11, Opacity = .6, TextWrapping = TextWrapping.Wrap });
            var editorError = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed, Foreground = new SolidColorBrush(Colors.IndianRed) };
            AutomationProperties.SetAutomationId(editorError, "AnnotationEditorError"); body.Children.Add(editorError);
            var dialog = new ContentDialog { XamlRoot = XamlRoot, RequestedTheme = ActualTheme, Title = $"第 {Page(annotation) + 1} 页 · {(sourceVariant == "original" ? "原文" : "译文")} · {KindTitle(Text(annotation, "kind"))}", Content = body, PrimaryButtonText = "保存", CloseButtonText = "取消", DefaultButton = ContentDialogButton.None };
            AutomationProperties.SetAutomationId(dialog, "AnnotationEditorDialog");
            dialog.IsPrimaryButtonEnabled = !isNew || !string.IsNullOrWhiteSpace(editor.Text);
            editor.TextChanged += (_, _) => dialog.IsPrimaryButtonEnabled = !isNew || !string.IsNullOrWhiteSpace(editor.Text);
            dialog.Opened += (_, _) => editor.Focus(FocusState.Programmatic);
            JsonNode? saved = null;
            dialog.PrimaryButtonClick += async (_, args) =>
            {
                var deferral = args.GetDeferral();
                try
                {
                    if (!Current(version, id, sourceVariant) || sourceSHA != hash)
                        throw new InvalidOperationException("文档已切换或重新翻译，请复制笔记内容后关闭并重新打开编辑器。");
                    string value = editor.Text.Trim();
                    if (isNew && value.Length == 0) { args.Cancel = true; return; }
                    var update = (JsonObject)annotation.DeepClone(); update["note"] = value;
                    saved = await backend.CallAsync("save_annotation", new JsonObject { ["document_id"] = id, ["variant"] = sourceVariant, ["source_sha256"] = hash, ["annotation"] = update });
                }
                catch (Exception error)
                {
                    // Keep the editor and the user's draft open when saving fails.
                    args.Cancel = true; editorError.Text = error.Message; editorError.Visibility = Visibility.Visible;
                }
                finally { deferral.Complete(); }
            };
            if (await showDialog(dialog) == ContentDialogResult.Primary && saved is not null && Current(version, id, sourceVariant))
            {
                ApplyEnvelope(saved); SetStatus("笔记已保存");
            }
        }
        finally { editing = false; UpdateEnabled(); }
    }

    private async Task DeleteAsync(JsonObject annotation)
    {
        int version = generation; string id = documentId, sourceVariant = variant;
        var result = await backend.CallAsync("delete_annotation", new JsonObject { ["document_id"] = id, ["variant"] = sourceVariant, ["source_sha256"] = sourceSHA, ["annotation_id"] = Text(annotation, "id") });
        if (Current(version, id, sourceVariant)) { ApplyEnvelope(result); SetStatus("批注已删除"); }
    }

    private Task ExportAsync(string kind) => sourceSHA.Length > 0 ? export(kind, variant, sourceSHA) : Task.CompletedTask;
    private bool Current(int version, string id, string sourceVariant) => !disposed && version == generation && documentId == id && variant == sourceVariant;

    private void ApplyEnvelope(JsonNode? result)
    {
        sourceSHA = Text(result, "source_sha256");
        annotations = result?["annotations"]?.DeepClone() as JsonArray ?? [];
        Render(); AnnotationsChanged?.Invoke(variant, sourceSHA, (JsonArray)annotations.DeepClone());
    }

    private void Render()
    {
        list.Items.Clear(); rowActions.Clear();
        foreach (var item in annotations.OfType<JsonObject>().OrderBy(Page).ThenBy(item => Number(item, "created_at")))
        {
            var annotation = (JsonObject)item.DeepClone(); string id = Text(annotation, "id"), kind = Text(annotation, "kind");
            int rowVersion = generation; string rowDocument = documentId, rowVariant = variant, rowHash = sourceSHA;
            bool SameBinding() => Current(rowVersion, rowDocument, rowVariant) && sourceSHA == rowHash;
            var card = new StackPanel { Spacing = 7, Margin = new Thickness(12, 8, 12, 8), HorizontalAlignment = HorizontalAlignment.Stretch };
            var heading = new Grid { ColumnSpacing = 4 };
            heading.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); heading.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); heading.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            var location = new Button { Content = $"第 {Page(annotation) + 1} 页 · {KindTitle(kind)}", FontSize = 12, Padding = new Thickness(0), MinHeight = 24, BorderThickness = new Thickness(0), Background = new SolidColorBrush(Colors.Transparent), Foreground = new SolidColorBrush(Colors.DodgerBlue), HorizontalContentAlignment = HorizontalAlignment.Left, HorizontalAlignment = HorizontalAlignment.Stretch };
            AutomationProperties.SetAutomationId(location, "AnnotationNavigate_" + id);
            location.Click += (_, _) => { if (SameBinding()) navigate(rowVariant, Page(annotation)); }; heading.Children.Add(location); rowActions.Add(location);
            var edit = IconButton("note", "编辑笔记", "AnnotationEdit_" + id, () => SameBinding() ? EditAsync((JsonObject)annotation.DeepClone(), false) : Task.CompletedTask);
            var delete = IconButton("trash", "删除批注", "AnnotationDelete_" + id, () => SameBinding() ? DeleteAsync(annotation) : Task.CompletedTask);
            Grid.SetColumn(edit, 1); Grid.SetColumn(delete, 2); heading.Children.Add(edit); heading.Children.Add(delete); rowActions.Add(edit); rowActions.Add(delete); card.Children.Add(heading);
            string quote = Text(annotation, "selected_text"), note = Text(annotation, "note");
            if (quote.Length > 0) card.Children.Add(new TextBlock { Text = quote, FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxLines = 4, TextTrimming = TextTrimming.CharacterEllipsis, Opacity = .65, IsTextSelectionEnabled = true });
            if (note.Length > 0) card.Children.Add(new TextBlock { Text = note, FontSize = 12, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
            var row = new ListViewItem { Content = card, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(0) };
            AutomationProperties.SetAutomationId(row, "AnnotationItem_" + id); list.Items.Add(row);
        }
        bool hasItems = annotations.Count > 0;
        list.Visibility = hasItems ? Visibility.Visible : Visibility.Collapsed; empty.Visibility = hasItems ? Visibility.Collapsed : Visibility.Visible;
        emptyTitle.Text = documentId.Length > 0 ? "还没有批注" : "打开文档后开始记录";
        emptyDescription.Text = documentId.Length > 0 ? "在 PDF 中选择文字以高亮或划线，也可以为当前页写笔记、添加书签。" : "导入或选择一份 PDF，即可添加高亮、笔记和书签。";
        ApplyTheme(); UpdateEnabled();
    }

    private void UpdateEnabled()
    {
        bool active = !disposed && documentId.Length > 0 && sourceSHA.Length > 0 && !busy && !editing;
        originalButton.IsEnabled = !disposed && documentId.Length > 0 && !busy && !editing;
        translatedButton.IsEnabled = originalButton.IsEnabled && translatedAvailable;
        newNoteButton.IsEnabled = bookmarkButton.IsEnabled = pdfButton.IsEnabled = notesButton.IsEnabled = active;
        highlightButton.IsEnabled = underlineButton.IsEnabled = active && selectionAvailable;
        foreach (var action in rowActions) action.IsEnabled = active;
        bool bookmarked = annotations.OfType<JsonObject>().Any(item => Text(item, "kind") == "bookmark" && Page(item) == currentPage);
        bookmarkButton.Background = bookmarked ? new SolidColorBrush(global::Windows.UI.Color.FromArgb(45, 10, 132, 255)) : new SolidColorBrush(Colors.Transparent);
        string help = bookmarked ? $"移除第 {currentPage + 1} 页书签" : $"为第 {currentPage + 1} 页添加书签";
        ToolTipService.SetToolTip(bookmarkButton, help); AutomationProperties.SetName(bookmarkButton, help);
    }

    private void ApplyTheme()
    {
        bool dark = ActualTheme == ElementTheme.Dark;
        lineBrush.Color = dark ? global::Windows.UI.Color.FromArgb(255, 60, 60, 60) : global::Windows.UI.Color.FromArgb(255, 220, 220, 220);
        selectionBrush.Color = dark ? global::Windows.UI.Color.FromArgb(255, 38, 38, 38) : global::Windows.UI.Color.FromArgb(255, 232, 232, 232);
        originalButton.Background = variant == "original" ? new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 79, 79, 79) : Colors.White) : new SolidColorBrush(Colors.Transparent);
        translatedButton.Background = variant == "translated" ? new SolidColorBrush(dark ? global::Windows.UI.Color.FromArgb(255, 79, 79, 79) : Colors.White) : new SolidColorBrush(Colors.Transparent);
    }

    private Button SourceButton(string title, string id, string sourceVariant)
    {
        var button = new Button { Content = title, FontSize = 12, Height = 25, MinHeight = 0, Padding = new Thickness(6, 0, 6, 0), BorderThickness = new Thickness(0), HorizontalAlignment = HorizontalAlignment.Stretch, CornerRadius = new CornerRadius(4) };
        AutomationProperties.SetAutomationId(button, id); button.Click += async (_, _) => await RunAsync(() => SetVariantAsync(sourceVariant)); return button;
    }

    private Button IconButton(string icon, string help, string id, Func<Task> action)
    {
        var button = new Button { Content = ReaderIcons.Make(icon), Padding = new Thickness(5, 3, 5, 3), MinHeight = 0, Height = 27, BorderThickness = new Thickness(0), Background = new SolidColorBrush(Colors.Transparent), CornerRadius = new CornerRadius(4) };
        AutomationProperties.SetAutomationId(button, id); AutomationProperties.SetName(button, help); ToolTipService.SetToolTip(button, help);
        button.Click += async (_, _) => await RunAsync(action); return button;
    }

    private Button TextButton(string text, string id, Func<Task> action)
    {
        var button = new Button { Content = text, FontSize = 11, Padding = new Thickness(7, 5, 7, 5), MinHeight = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetAutomationId(button, id); button.Click += async (_, _) => await RunAsync(action); return button;
    }

    private async Task RunAsync(Func<Task> action)
    {
        if (disposed || busy) return;
        string expectedDocument = documentId;
        busy = true; SetStatus(""); UpdateEnabled();
        try { await action(); }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (!disposed && documentId == expectedDocument) SetStatus(error.Message); }
        finally { busy = false; UpdateEnabled(); }
    }

    private void SetStatus(string text) { status.Text = text; status.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed; }
    private static string Text(JsonNode? item, string key) => item?[key]?.GetValue<string>() ?? "";
    private static int Page(JsonNode? item) => item?["page"]?.GetValue<int>() ?? 0;
    private static double Number(JsonNode? item, string key) => item?[key] is JsonValue value && value.TryGetValue<double>(out double number) ? number : 0;
    private static string KindTitle(string kind) => kind switch { "highlight" => "高亮", "underline" => "下划线", "bookmark" => "书签", _ => "笔记" };
    public void Dispose() { disposed = true; generation++; }
}
