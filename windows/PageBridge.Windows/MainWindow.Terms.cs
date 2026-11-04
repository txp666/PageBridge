using System.Text.Json.Nodes;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private const string NewGlossaryTag = "new";

    /// Own glossaries that suit the open draft's languages, plus "new glossary for this document".
    private async Task<ComboBox> WritableGlossaryPickerAsync(string? preferred)
    {
        var snapshot = await backend.CallAsync("glossaries");
        string sourceLanguage = Text(selected, "reading_source_language") is { Length: > 0 } s ? s : Text(selected, "source_language");
        string targetLanguage = Text(selected, "reading_target_language") is { Length: > 0 } t ? t : Text(selected, "target_language");
        var picker = new ComboBox { Width = 300 };
        var linked = selected?["glossary_ids"]?.AsArray().Select(n => n?.GetValue<string>() ?? "").ToList() ?? new();
        foreach (var item in snapshot?["glossaries"]?.AsArray().OfType<JsonObject>() ?? Enumerable.Empty<JsonObject>())
        {
            if (Flag(item, "builtin")) continue;
            if (Text(item, "source_language") is { Length: > 0 } a && a != sourceLanguage) continue;
            if (Text(item, "target_language") is { Length: > 0 } b && b != targetLanguage) continue;
            picker.Items.Add(new ComboBoxItem { Content = Text(item, "name"), Tag = Text(item, "id") });
        }
        picker.Items.Add(new ComboBoxItem { Content = "新建：" + Path.GetFileNameWithoutExtension(Text(selected, "name")) + " 术语", Tag = NewGlossaryTag });
        var items = picker.Items.OfType<ComboBoxItem>().ToList();
        picker.SelectedItem = items.FirstOrDefault(i => (string)i.Tag == preferred) ?? items.FirstOrDefault(i => linked.Contains((string)i.Tag)) ?? items[^1];
        return picker;
    }

    private static string? PickedGlossary(ComboBox picker) => (picker.SelectedItem as ComboBoxItem)?.Tag as string is { } tag && tag != NewGlossaryTag ? tag : null;

    /// Adds one term while reading and can re-translate the paragraphs that contain it.
    private async Task AddTermDialogAsync(string source, string context)
    {
        if (selected is null) return;
        string documentId = Text(selected, "id");
        var panel = new StackPanel { Spacing = 12, Width = 460 };
        if (context.Length > 0)
        {
            panel.Children.Add(new TextBlock { Text = "所在段落（可选中复制要固定的词）", FontSize = 11, Opacity = .65 });
            panel.Children.Add(new Border
            {
                Child = new ScrollViewer { Content = new TextBlock { Text = context, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, FontSize = 13 }, MaxHeight = 110 },
                Padding = new Thickness(8), CornerRadius = new CornerRadius(6), Background = Dark ? Brush(45, 45, 45) : Brush(238, 238, 238)
            });
        }
        var sourceBox = new TextBox { Header = "原文", Text = source, PlaceholderText = "例如 servo drive", MaxLength = 200 };
        var targetBox = new TextBox { Header = "译文", PlaceholderText = "留空表示保留原文不译", MaxLength = 200 };
        var picker = await WritableGlossaryPickerAsync(null);
        picker.Header = "术语表";
        bool canRetranslate = HasReading && translationCancellation is null && !translationPreparing;
        var retranslate = new CheckBox { Content = "立即重译阅读稿中包含这个术语的段落", IsChecked = canRetranslate, IsEnabled = canRetranslate };
        var error = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed) };
        panel.Children.Add(sourceBox); panel.Children.Add(targetBox); panel.Children.Add(picker); panel.Children.Add(retranslate);
        panel.Children.Add(new TextBlock { Text = "这个术语表会关联到当前文档，之后翻译和单段重试都会带上。英文按整词匹配（含常见词形变化），大写缩写区分大小写。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(error);
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "加入术语表", Content = panel, PrimaryButtonText = "加入", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try
            {
                if (sourceBox.Text.Trim().Length == 0) { error.Text = "请填写原文。"; args.Cancel = true; return; }
                var terms = new JsonArray(new JsonObject { ["source"] = sourceBox.Text.Trim(), ["target"] = targetBox.Text.Trim() });
                await backend.CallAsync("glossary_add_terms", new JsonObject { ["document_id"] = documentId, ["glossary_id"] = PickedGlossary(picker), ["terms"] = terms });
            }
            catch (Exception failure) { error.Text = failure.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        if (await ShowDialogAsync(dialog) != ContentDialogResult.Primary) return;
        await ReloadAsync(documentId);
        if (retranslate.IsChecked == true && Text(selected, "id") == documentId) await RunTranslationAsync(null, new[] { sourceBox.Text.Trim() });
        else status.Text = "已加入术语表";
    }

    /// Reviews terms the engine proposed for the current document; returns the glossary they went into.
    private async Task<string?> TermSuggestionsDialogAsync(string? preferredGlossary)
    {
        if (selected is null) return null;
        string documentId = Text(selected, "id");
        var services = await backend.CallAsync("profiles");
        string serviceId = Text(services, "active_id");
        if (serviceId == "") { status.Text = "请先配置翻译服务。"; return null; }
        var picker = await WritableGlossaryPickerAsync(preferredGlossary);
        var rows = new StackPanel { Spacing = 4 };
        var busy = new StackPanel { Spacing = 10, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 60, 0, 60) };
        busy.Children.Add(new ProgressRing { IsActive = true, Width = 32, Height = 32 });
        var busyText = new TextBlock { Text = "正在提取术语，长文档可能需要一两分钟…", Opacity = .7 };
        busy.Children.Add(busyText);
        var list = new ScrollViewer { Content = rows, Height = 330, Visibility = Visibility.Collapsed };
        var error = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed) };
        var panel = new StackPanel { Spacing = 10, Width = 640 };
        panel.Children.Add(new TextBlock { Text = "候选词在本机统计，再请所选模型挑出领域术语并给出常用译法。请逐条核对：取消勾选不需要的，修改不准确的译法；译文留空表示保留原文。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(busy); panel.Children.Add(list);
        picker.Header = "加入到";
        panel.Children.Add(picker); panel.Children.Add(error);
        var entries = new List<(CheckBox Box, string Source, TextBox Target)>();
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "从文档提取术语", Content = panel, PrimaryButtonText = "加入所选术语", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Primary, IsPrimaryButtonEnabled = false };
        dialog.Resources["ContentDialogMaxWidth"] = 720d;
        using var cancellation = new CancellationTokenSource();
        dialog.Opened += async (_, _) =>
        {
            try
            {
                var terms = await backend.SuggestTermsAsync(documentId, serviceId, preferredGlossary, evt => DispatcherQueue.TryEnqueue(() =>
                {
                    if (Text(evt, "message") is { Length: > 0 } message) busyText.Text = message;
                }), cancellation.Token);
                foreach (var term in terms.OfType<JsonObject>())
                {
                    var row = new Grid { ColumnSpacing = 10 };
                    row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
                    row.ColumnDefinitions.Add(new() { Width = new GridLength(220) });
                    row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
                    row.ColumnDefinitions.Add(new() { Width = new GridLength(56) });
                    var box = new CheckBox { IsChecked = true, MinWidth = 0 };
                    var label = new TextBlock { Text = Text(term, "source"), VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
                    var target = new TextBox { Text = Text(term, "target"), PlaceholderText = "保留原文" };
                    var count = new TextBlock { Text = $"{term["count"]?.GetValue<int>() ?? 0} 次", Opacity = .6, FontSize = 11, VerticalAlignment = VerticalAlignment.Center };
                    Grid.SetColumn(label, 1); Grid.SetColumn(target, 2); Grid.SetColumn(count, 3);
                    row.Children.Add(box); row.Children.Add(label); row.Children.Add(target); row.Children.Add(count);
                    rows.Children.Add(row); entries.Add((box, Text(term, "source"), target));
                }
                busy.Visibility = Visibility.Collapsed; list.Visibility = Visibility.Visible;
                if (entries.Count == 0) { list.Visibility = Visibility.Collapsed; busy.Visibility = Visibility.Visible; busy.Children[0].Visibility = Visibility.Collapsed; busyText.Text = "没有找到新的候选术语。"; }
                dialog.IsPrimaryButtonEnabled = entries.Count > 0;
            }
            catch (OperationCanceledException) { }
            catch (Exception failure) { busy.Visibility = Visibility.Collapsed; error.Text = failure.Message; }
        };
        string? addedTo = null;
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try
            {
                var chosen = new JsonArray(entries.Where(e => e.Box.IsChecked == true).Select(e => (JsonNode?)new JsonObject { ["source"] = e.Source, ["target"] = e.Target.Text.Trim() }).ToArray());
                if (chosen.Count == 0) { error.Text = "请至少勾选一个术语。"; args.Cancel = true; return; }
                var result = await backend.CallAsync("glossary_add_terms", new JsonObject { ["document_id"] = documentId, ["glossary_id"] = PickedGlossary(picker), ["terms"] = chosen });
                addedTo = Text(result, "glossary_id");
            }
            catch (Exception failure) { error.Text = failure.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        await ShowDialogAsync(dialog);
        cancellation.Cancel();
        if (addedTo is not null) await ReloadAsync(documentId);
        return addedTo;
    }

    /// Finds text in the originals and translations of every document in the library.
    private async Task LibrarySearchAsync()
    {
        var query = new TextBox { PlaceholderText = "在全部文档的原文和译文中搜索，例如报警码、参数名或术语", FontSize = 15 };
        var results = new ListView { SelectionMode = ListViewSelectionMode.None, IsItemClickEnabled = true, Height = 400 };
        var note = new TextBlock { FontSize = 11, Opacity = .65, Text = "输入至少 2 个字符后按回车。首次搜索会读取每份 PDF 的文字，之后会快很多。" };
        var go = new Button { Content = "搜索" };
        var bar = new Grid { ColumnSpacing = 8 };
        bar.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); bar.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        Grid.SetColumn(go, 1); bar.Children.Add(query); bar.Children.Add(go);
        var panel = new StackPanel { Spacing = 10, Width = 680 };
        panel.Children.Add(bar); panel.Children.Add(results); panel.Children.Add(note);
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "搜索全部文档", Content = panel, CloseButtonText = "关闭" };
        dialog.Resources["ContentDialogMaxWidth"] = 760d;
        JsonObject? target = null;
        int generation = 0;
        async Task Run()
        {
            string needle = query.Text.Trim();
            if (needle.Length < 2) return;
            int current = ++generation; note.Text = "正在搜索…"; results.Items.Clear();
            try
            {
                var found = await backend.CallAsync("library_search", new JsonObject { ["query"] = needle });
                if (current != generation) return;
                var hits = found?["hits"]?.AsArray().OfType<JsonObject>().ToList() ?? new();
                foreach (var hit in hits)
                {
                    var item = new StackPanel { Spacing = 3, Padding = new Thickness(0, 6, 0, 6) };
                    item.Children.Add(new TextBlock { Text = $"{Path.GetFileNameWithoutExtension(Text(hit, "document_name"))} · 第 {hit["page"]?.GetValue<int>()} 页 · {(Flag(hit, "in_translation") ? "译文" : "原文")}", FontSize = 12, FontWeight = FontWeights.SemiBold });
                    string snippet = Text(hit, "snippet");
                    int start = Math.Clamp(hit["match_start"]?.GetValue<int>() ?? 0, 0, snippet.Length);
                    int length = Math.Clamp(hit["match_length"]?.GetValue<int>() ?? 0, 0, snippet.Length - start);
                    var paragraph = new Paragraph();
                    paragraph.Inlines.Add(new Run { Text = snippet[..start] });
                    paragraph.Inlines.Add(new Run { Text = snippet.Substring(start, length), FontWeight = FontWeights.Bold });
                    paragraph.Inlines.Add(new Run { Text = snippet[(start + length)..] });
                    // Selectable text would take the click away from the list item.
                    var text = new RichTextBlock { FontSize = 13, TextWrapping = TextWrapping.Wrap, MaxLines = 3, IsTextSelectionEnabled = false, IsHitTestVisible = false };
                    text.Blocks.Add(paragraph); item.Children.Add(text);
                    results.Items.Add(new ListViewItem { Content = item, Tag = hit });
                }
                note.Text = hits.Count == 0 ? $"没有找到“{needle}”。" : Flag(found, "limited") ? $"只显示前 {hits.Count} 处，请换更具体的词。" : $"共 {hits.Count} 处，点击打开。";
            }
            catch (Exception failure) { note.Text = failure.Message; }
        }
        query.KeyDown += async (_, e) => { if (e.Key == global::Windows.System.VirtualKey.Enter) { e.Handled = true; await Run(); } };
        go.Click += async (_, _) => await Run();
        results.ItemClick += (_, e) =>
        {
            // ItemClick reports the container or its content, depending on how items were added.
            var item = e.ClickedItem as ListViewItem ?? results.Items.OfType<ListViewItem>().FirstOrDefault(i => ReferenceEquals(i.Content, e.ClickedItem));
            if (item?.Tag is JsonObject hit) { target = hit; dialog.Hide(); }
        };
        await ShowDialogAsync(dialog);
        if (target is null) return;
        if (documents.OfType<JsonObject>().FirstOrDefault(d => Text(d, "id") == Text(target, "document_id")) is not { } doc) return;
        if (Text(selected, "id") != Text(doc, "id")) await OpenAsync(doc);
        int number = Math.Clamp(target["page"]?.GetValue<int>() ?? 1, 1, (int)page.Maximum);
        if (Flag(target, "in_translation") && HasReading) mode.SelectedIndex = 1;
        page.Value = number; JumpToPage(number); UpdateNavigationPage();
    }
}
