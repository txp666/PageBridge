using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private sealed record TranslationChoice(string Source, string Target, string? Pages, IReadOnlyList<string>? GlossaryIds = null,
        bool Append = false, bool TranslateTables = false);
    private readonly Button exportCancel = new() { Content = "取消导出", FontSize = 11, MinHeight = 0, Height = 24, Padding = new Thickness(8, 1, 8, 1), Visibility = Visibility.Collapsed };
    private CancellationTokenSource? exportCancellation;
    private bool ReadingExportAvailable => selected is not null && HasReading && translationCancellation is null && !translationPreparing && exportCancellation is null;

    private async Task<TranslationChoice?> TranslationDialogAsync(string name, JsonNode? services, TranslationChoice? initial = null)
    {
        ComboBox LanguagesPicker(string initial)
        {
            var picker = new ComboBox { Width = 175, HorizontalAlignment = HorizontalAlignment.Right };
            foreach (var item in Languages) picker.Items.Add(new ComboBoxItem { Content = item.Name, Tag = item.Code });
            picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == initial) ?? picker.Items[0];
            return picker;
        }
        var source = LanguagesPicker(initial?.Source ?? Text(selected, "source_language"));
        var target = LanguagesPicker(initial?.Target ?? Text(selected, "target_language"));
        var range = new ComboBox { Width = 175, HorizontalAlignment = HorizontalAlignment.Right };
        range.Items.Add("全部页面"); range.Items.Add("指定页面"); range.SelectedIndex = initial?.Pages is null ? 0 : 1;
        var pages = new TextBox { PlaceholderText = "例如 1-5, 8, 12-16", Width = 235, Text = initial?.Pages ?? "" };
        var panel = new StackPanel { Spacing = 18, Width = 440 };
        panel.Children.Add(new TextBlock { Text = name, FontSize = 13, Opacity = .65, TextTrimming = TextTrimming.CharacterEllipsis });
        Grid Field(string caption, FrameworkElement control)
        {
            var row = new Grid(); row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); row.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); row.Children.Add(new TextBlock { Text = caption, FontSize = 13, VerticalAlignment = VerticalAlignment.Center }); Grid.SetColumn(control, 1); row.Children.Add(control); return row;
        }
        panel.Children.Add(Field("源语言", source)); panel.Children.Add(Field("目标语言", target)); panel.Children.Add(Field("翻译范围", range));
        var pageRow = Field("页码", pages); pageRow.Visibility = range.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed; panel.Children.Add(pageRow);
        range.SelectionChanged += (_, _) => pageRow.Visibility = range.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
        // Appending keeps the draft and its languages; only pages not yet translated are added.
        var draftPages = selected?["reading_pages"]?.AsArray().Select(n => n?.GetValue<int>() ?? 0).ToHashSet() ?? new HashSet<int>();
        var append = new CheckBox { Content = $"追加到现有阅读稿（保留已译的 {draftPages.Count} 页）", IsChecked = initial?.Append ?? true, FontSize = 13 };
        var appendRow = new StackPanel { Children = { append } };
        bool draftTables = selected?["reading_translate_tables"]?.GetValue<bool>() ?? true;
        bool chosenTables = initial?.TranslateTables ?? false;
        var tables = new CheckBox { Content = "自动翻译表格", IsChecked = chosenTables, FontSize = 13 };
        tables.Click += (_, _) => chosenTables = tables.IsChecked == true;
        void UpdateAppend()
        {
            bool canAppend = HasReading && range.SelectedIndex == 1;
            appendRow.Visibility = canAppend ? Visibility.Visible : Visibility.Collapsed;
            bool appending = canAppend && append.IsChecked == true;
            source.IsEnabled = target.IsEnabled = !appending;
            tables.IsEnabled = !appending;
            tables.IsChecked = appending ? draftTables : chosenTables;
            if (appending)
            {
                source.SelectedItem = source.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == Text(selected, "reading_source_language")) ?? source.SelectedItem;
                target.SelectedItem = target.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == Text(selected, "reading_target_language")) ?? target.SelectedItem;
            }
        }
        panel.Children.Add(appendRow);
        panel.Children.Add(tables);
        range.SelectionChanged += (_, _) => UpdateAppend(); append.Click += (_, _) => UpdateAppend();

        // Several glossaries can be combined; only those whose language pair fits are listed.
        var glossaries = await backend.CallAsync("glossaries");
        var all = glossaries?["glossaries"]?.AsArray().OfType<JsonObject>().ToList() ?? new();
        var chosenIds = new List<string>(initial?.GlossaryIds ?? (selected?["glossary_ids"] is JsonArray linked ? linked.Select(n => n?.GetValue<string>() ?? "") : glossaries?["last_used"]?.AsArray().Select(n => n?.GetValue<string>() ?? "") ?? Enumerable.Empty<string>()));
        string Code(ComboBox picker) => (string)((ComboBoxItem)picker.SelectedItem).Tag;
        bool Fits(JsonObject g) => (Text(g, "source_language") is not { Length: > 0 } s || s == Code(source)) && (Text(g, "target_language") is not { Length: > 0 } t || t == Code(target));
        var glossaryButton = new DropDownButton { Width = 235, HorizontalContentAlignment = HorizontalAlignment.Left };
        var glossaryList = new StackPanel { Spacing = 2, Padding = new Thickness(4) };
        var glossaryFlyout = new Flyout { Content = new ScrollViewer { Content = glossaryList, MaxHeight = 360, Width = 300 } };
        glossaryButton.Flyout = glossaryFlyout;
        var glossaryNote = new TextBlock { FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap };
        void RefreshGlossaries()
        {
            var fitting = all.Where(Fits).ToList();
            chosenIds.RemoveAll(id => !fitting.Any(g => Text(g, "id") == id));
            glossaryList.Children.Clear();
            foreach (var (title, builtin) in new[] { ("我的术语表", false), ("内置", true) })
            {
                var group = fitting.Where(g => Flag(g, "builtin") == builtin).ToList();
                if (group.Count == 0) continue;
                glossaryList.Children.Add(new TextBlock { Text = title, FontSize = 11, Opacity = .6, Margin = new Thickness(4, 6, 0, 2) });
                foreach (var item in group)
                {
                    string id = Text(item, "id");
                    var box = new CheckBox { Content = $"{Text(item, "name")}（{item["entries"]?.GetValue<int>() ?? 0} 条）", IsChecked = chosenIds.Contains(id), FontSize = 13 };
                    box.Click += (_, _) => { chosenIds.Remove(id); if (box.IsChecked == true) chosenIds.Add(id); Summarize(); };
                    glossaryList.Children.Add(box);
                }
            }
            int hidden = all.Count - fitting.Count;
            glossaryNote.Text = "可选多个，同一术语以“我的术语表”为准。" + (hidden > 0 ? $"{hidden} 个术语表的语言对不适用于当前语言，已隐藏。" : "");
            Summarize();
        }
        void Summarize()
        {
            var names = chosenIds.Select(id => Text(all.FirstOrDefault(g => Text(g, "id") == id), "name")).Where(n => n != "").ToList();
            glossaryButton.Content = names.Count == 0 ? "不使用术语表" : names.Count == 1 ? names[0] : $"{names[0]} 等 {names.Count} 个";
        }
        source.SelectionChanged += (_, _) => RefreshGlossaries(); target.SelectionChanged += (_, _) => RefreshGlossaries();
        UpdateAppend(); RefreshGlossaries();
        var manage = new HyperlinkButton { Content = "管理…", Padding = new Thickness(6, 2, 6, 2) };
        panel.Children.Add(Field("术语表", Row(glossaryButton, manage)));
        panel.Children.Add(glossaryNote);
        var profile = services?["profiles"]?.AsArray().FirstOrDefault(p => Text(p, "id") == Text(services, "active_id"));
        var service = new StackPanel { Spacing = 9 };
        service.Children.Add(Row(ReaderIcons.Make("globe"), new TextBlock { Text = "翻译服务", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 13 }));
        service.Children.Add(new TextBlock { Text = Text(profile, "name"), FontSize = 13 });
        service.Children.Add(new TextBlock { Text = Text(profile, "model"), FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        service.Children.Add(new TextBlock { Text = "PDF 在本机处理。使用在线 API 时，待翻译文本会发送至所选服务；本机服务需在本机运行。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(new Border { Child = service, Padding = new Thickness(14), CornerRadius = new CornerRadius(10), Background = Dark ? Brush(45, 45, 45) : Brush(238, 238, 238) });
        var error = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed) }; panel.Children.Add(error);
        panel.Children.Add(new TextBlock { Text = "阅读稿保留原 PDF，表格和公式可回看原文。首次使用会下载本地识别组件，请预留约 4 GB 磁盘空间；未译段落可单独重试，完成后可导出译文 PDF 或双语 PDF。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "翻译 PDF", Content = panel, PrimaryButtonText = "开始翻译", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary };
        dialog.Resources["ContentDialogMaxWidth"] = 520d;
        dialog.PrimaryButtonClick += (_, args) =>
        {
            error.Text = "";
            if ((string)((ComboBoxItem)source.SelectedItem).Tag == (string)((ComboBoxItem)target.SelectedItem).Tag) error.Text = "源语言和目标语言需要不同。";
            if (range.SelectedIndex == 1)
            {
                foreach (var part in pages.Text.Split(','))
                {
                    var bounds = part.Trim().Split('-');
                    if (bounds.Length is < 1 or > 2 || !int.TryParse(bounds[0], out int first) || !int.TryParse(bounds[^1], out int last) || first < 1 || last < first || last > page.Maximum) { error.Text = $"请输入 1 至 {(int)page.Maximum} 之间的页码，例如 1-3, 5。"; break; }
                }
            }
            bool appending = HasReading && range.SelectedIndex == 1 && append.IsChecked == true;
            if (error.Text == "" && appending)
            {
                var wanted = pages.Text.Split(',').SelectMany(part => part.Trim().Split('-') is var b && int.TryParse(b[0], out int a) && int.TryParse(b[^1], out int z) ? Enumerable.Range(a, Math.Max(0, z - a + 1)) : Enumerable.Empty<int>());
                if (!wanted.Any(n => !draftPages.Contains(n))) error.Text = "这些页已在阅读稿中，请选择尚未翻译的页。";
            }
            args.Cancel = error.Text != "";
        };
        TranslationChoice Current() => new(Code(source), Code(target), range.SelectedIndex == 1 ? pages.Text.Trim() : null,
            chosenIds.ToList(), HasReading && range.SelectedIndex == 1 && append.IsChecked == true, chosenTables);
        // Dialogs cannot stack: managing closes this one, then reopens it with the same choices.
        bool managing = false;
        manage.Click += (_, _) => { managing = true; dialog.Hide(); };
        var result = await ShowDialogAsync(dialog);
        if (managing)
        {
            var state = Current();
            await GlossaryManagerAsync(state.GlossaryIds?.FirstOrDefault());
            return await TranslationDialogAsync(name, services, state);
        }
        return result == ContentDialogResult.Primary ? Current() : null;
    }

    // The engine rebuilds the PDF from the current reading draft; the save dialog comes first.
    private async Task ExportTranslationAsync(string kind)
    {
        if (!ReadingExportAvailable) return;
        string id = Text(selected, "id"), name = Path.GetFileNameWithoutExtension(Text(selected, "name")) + (kind == "bilingual" ? "-双语" : "-译文");
        if (await PickSavePathAsync(name, "PDF 文档", ".pdf") is not { } path || !ReadingExportAvailable) return;
        using var cancellation = new CancellationTokenSource();
        exportCancellation = cancellation; UpdateDocumentState();
        exportCancel.Visibility = Visibility.Visible; progress.Visibility = Visibility.Visible; progress.IsIndeterminate = true;
        status.Text = "正在准备导出…";
        try
        {
            await backend.ExportReadingAsync(id, kind, path, evt => DispatcherQueue.TryEnqueue(() =>
            {
                if (!ReferenceEquals(exportCancellation, cancellation) || cancellation.IsCancellationRequested) return;
                if (evt["progress"] is JsonValue value && value.TryGetValue<double>(out var amount)) { progress.IsIndeterminate = false; progress.Value = amount * 100; }
                var message = Text(evt, "message"); if (message != "") status.Text = message;
            }), cancellation.Token);
            status.Text = (kind == "bilingual" ? "双语" : "译文") + " PDF 已导出：" + path;
        }
        catch (OperationCanceledException) { status.Text = "导出已取消。"; }
        finally
        {
            exportCancellation = null; exportCancel.Visibility = Visibility.Collapsed; progress.Visibility = Visibility.Collapsed;
            UpdateDocumentState();
        }
    }
}
