using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using global::Windows.Storage.Pickers;
using WinRT.Interop;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private const string GlossaryHint = "每行一条，“原文 = 译文”固定译法；只写原文则保留不译。可从表格粘贴两列（Tab 分隔），# 开头为注释。翻译每段时只附上该段出现的术语，英文按整词匹配、不分大小写。";

    // WinUI text boxes store line breaks as \r; glossaries are saved with \n.
    private static string GlossaryText(string text) => text.Replace("\r\n", "\n").Replace('\r', '\n');
    private static int GlossaryEntries(string text) => text.Split('\r', '\n').Count(line => line.Trim() is { Length: > 0 } t && !t.StartsWith('#'));

    /// Lists built-in and own glossaries; own ones can be created, copied, edited, deleted, imported and exported.
    private async Task GlossaryManagerAsync(string? initialId)
    {
        var list = new ListView { SelectionMode = ListViewSelectionMode.Single, Height = 340 };
        var nameBox = new TextBox { PlaceholderText = "名称", MaxLength = 60, FontSize = 15 };
        var textBox = new TextBox
        {
            AcceptsReturn = true, TextWrapping = TextWrapping.NoWrap, Height = 270, MaxLength = 100_000,
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas"), FontSize = 12
        };
        ScrollViewer.SetHorizontalScrollBarVisibility(textBox, ScrollBarVisibility.Auto);
        ScrollViewer.SetVerticalScrollBarVisibility(textBox, ScrollBarVisibility.Auto);
        var count = new TextBlock { FontSize = 11, Opacity = .65 };
        var message = new TextBlock { FontSize = 11, TextWrapping = TextWrapping.Wrap };
        var create = new Button { Content = "新建" };
        var copy = new Button { Content = "复制" };
        var delete = new Button { Content = "删除" };
        var import = new Button { Content = "导入…" };
        var export = new Button { Content = "导出…" };
        var extract = new Button { Content = "从文档提取…", IsEnabled = selected is not null };
        ToolTipService.SetToolTip(extract, selected is null ? "先在文档库中选择一份文档" : $"从“{Text(selected, "name")}”提取候选术语");
        ComboBox LanguagePicker()
        {
            var picker = new ComboBox { Width = 120, FontSize = 12 };
            picker.Items.Add(new ComboBoxItem { Content = "任意", Tag = "" });
            foreach (var item in Languages) picker.Items.Add(new ComboBoxItem { Content = item.Name, Tag = item.Code });
            picker.SelectedIndex = 0;
            return picker;
        }
        var sourcePicker = LanguagePicker(); var targetPicker = LanguagePicker();
        var languages = new TextBlock { FontSize = 11, Opacity = .65, VerticalAlignment = VerticalAlignment.Center };
        var languageRow = Row(new TextBlock { Text = "适用语言", FontSize = 12, VerticalAlignment = VerticalAlignment.Center }, sourcePicker,
                              new TextBlock { Text = "→", VerticalAlignment = VerticalAlignment.Center }, targetPicker, languages);
        var conflicts = new TextBlock { FontSize = 11, TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.DarkOrange) };
        string Picked(ComboBox picker) => (picker.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
        void Pick(ComboBox picker, string code) => picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string)i.Tag == code) ?? picker.Items[0];

        string? loadedId = null; bool loadedBuiltin = false, suppress = false, extracting = false;
        string loadedName = "", loadedText = "", loadedSource = "", loadedTarget = "";

        void Show(string note) { message.Text = note; message.Opacity = .65; }
        void Fail(Exception error) { message.Text = error.Message; message.Opacity = 1; }
        void Describe()
        {
            count.Text = loadedBuiltin ? $"{GlossaryEntries(textBox.Text)} 条 · 内置术语表不能修改，点“复制”生成可编辑的副本" : $"{GlossaryEntries(textBox.Text)} 条 · 切换术语表或点“完成”时保存";
            var found = GlossaryConflicts(textBox.Text);
            conflicts.Text = found.Count == 0 ? "" : "同一原文写了不同译法，翻译时以最后一行为准：" + string.Join("；", found.Take(5)) + (found.Count > 5 ? $" 等 {found.Count} 处" : "");
        }

        async Task Refresh(string? selectId)
        {
            var snapshot = await backend.CallAsync("glossaries");
            suppress = true; list.Items.Clear();
            foreach (var item in snapshot?["glossaries"]?.AsArray() ?? new JsonArray())
            {
                var row = new StackPanel { Spacing = 1, Padding = new Thickness(0, 4, 0, 4) };
                row.Children.Add(new TextBlock { Text = Text(item, "name"), FontSize = 13, TextTrimming = TextTrimming.CharacterEllipsis });
                row.Children.Add(new TextBlock { Text = $"{item?["entries"]?.GetValue<int>() ?? 0} 条" + (Flag(item, "builtin") ? " · 内置" : ""), FontSize = 11, Opacity = .6 });
                list.Items.Add(new ListViewItem { Content = row, Tag = Text(item, "id") });
            }
            var target = list.Items.OfType<ListViewItem>().FirstOrDefault(i => (string)i.Tag == selectId) ?? list.Items.OfType<ListViewItem>().FirstOrDefault();
            list.SelectedItem = target; suppress = false;
            await Load(target?.Tag as string);
        }

        async Task Load(string? id)
        {
            loadedId = id;
            if (id is null) { nameBox.Text = textBox.Text = ""; loadedBuiltin = true; }
            else
            {
                var glossary = await backend.CallAsync("glossary_get", new JsonObject { ["glossary_id"] = id });
                loadedBuiltin = Flag(glossary, "builtin");
                nameBox.Text = Text(glossary, "name"); textBox.Text = Text(glossary, "text");
                Pick(sourcePicker, Text(glossary, "source_language")); Pick(targetPicker, Text(glossary, "target_language"));
            }
            loadedName = nameBox.Text; loadedText = textBox.Text; loadedSource = Picked(sourcePicker); loadedTarget = Picked(targetPicker);
            nameBox.IsReadOnly = textBox.IsReadOnly = loadedBuiltin;
            sourcePicker.IsEnabled = targetPicker.IsEnabled = !loadedBuiltin && id is not null;
            languages.Text = loadedBuiltin ? "" : "翻译时只列出语言对相符的术语表";
            delete.IsEnabled = id is not null && !loadedBuiltin; copy.IsEnabled = export.IsEnabled = id is not null;
            Describe(); Show("");
        }

        // Saves the open glossary if it changed; false keeps it open so the edit is not lost.
        async Task<bool> Commit()
        {
            if (loadedId is null || loadedBuiltin || (nameBox.Text == loadedName && textBox.Text == loadedText && Picked(sourcePicker) == loadedSource && Picked(targetPicker) == loadedTarget)) return true;
            try
            {
                var saved = await backend.CallAsync("glossary_update", new JsonObject
                {
                    ["glossary_id"] = loadedId, ["name"] = nameBox.Text, ["text"] = GlossaryText(textBox.Text),
                    ["source_language"] = Picked(sourcePicker), ["target_language"] = Picked(targetPicker)
                });
                nameBox.Text = Text(saved, "name"); loadedName = nameBox.Text; loadedText = textBox.Text; loadedSource = Picked(sourcePicker); loadedTarget = Picked(targetPicker);
                if (list.SelectedItem is ListViewItem { Content: StackPanel row } && row.Children[0] is TextBlock title) title.Text = loadedName;
                return true;
            }
            catch (Exception error) { Fail(error); return false; }
        }

        list.SelectionChanged += async (_, _) =>
        {
            if (suppress || list.SelectedItem is not ListViewItem item || (string)item.Tag == loadedId) return;
            if (await Commit()) { await Load((string)item.Tag); return; }
            suppress = true; list.SelectedItem = list.Items.OfType<ListViewItem>().FirstOrDefault(i => (string)i.Tag == loadedId); suppress = false;
        };
        textBox.TextChanged += (_, _) => Describe();
        create.Click += async (_, _) =>
        {
            if (!await Commit()) return;
            try { await Refresh(Text(await backend.CallAsync("glossary_create", new JsonObject { ["name"] = "新术语表" }), "id")); nameBox.Focus(FocusState.Programmatic); nameBox.SelectAll(); }
            catch (Exception error) { Fail(error); }
        };
        copy.Click += async (_, _) =>
        {
            if (loadedId is null || !await Commit()) return;
            try { await Refresh(Text(await backend.CallAsync("glossary_duplicate", new JsonObject { ["glossary_id"] = loadedId }), "id")); }
            catch (Exception error) { Fail(error); }
        };
        var confirmDelete = new Button { Content = "删除" };
        var deleteFlyout = new Flyout();
        var deletePanel = new StackPanel { Spacing = 10, MaxWidth = 280 };
        deletePanel.Children.Add(new TextBlock { Text = "删除这个术语表？已生成的阅读稿不受影响，但用它的阅读稿单段重试时将不再附带术语。", TextWrapping = TextWrapping.Wrap, FontSize = 12 });
        deletePanel.Children.Add(confirmDelete);
        deleteFlyout.Content = deletePanel; delete.Flyout = deleteFlyout;
        confirmDelete.Click += async (_, _) =>
        {
            deleteFlyout.Hide();
            if (loadedId is null) return;
            try { await backend.CallAsync("glossary_delete", new JsonObject { ["glossary_id"] = loadedId }); loadedId = null; await Refresh(null); }
            catch (Exception error) { Fail(error); }
        };
        import.Click += async (_, _) =>
        {
            if (!await Commit()) return;
            var picker = new FileOpenPicker();
            foreach (var extension in new[] { ".txt", ".csv", ".tsv" }) picker.FileTypeFilter.Add(extension);
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var files = await picker.PickMultipleFilesAsync();
            string? last = null; var failures = new List<string>();
            foreach (var file in files)
            {
                try { last = Text(await backend.CallAsync("glossary_import", new JsonObject { ["path"] = file.Path }), "id"); }
                catch (Exception error) { failures.Add(error.Message); }
            }
            if (last is not null) await Refresh(last);
            if (failures.Count > 0) { message.Text = string.Join(" ", failures); message.Opacity = 1; }
        };
        export.Click += async (_, _) =>
        {
            if (loadedId is null || !await Commit()) return;
            var picker = new FileSavePicker { SuggestedFileName = loadedName };
            picker.FileTypeChoices.Add("文本文件", new List<string> { ".txt" });
            picker.FileTypeChoices.Add("表格文件（CSV）", new List<string> { ".csv" });
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            if ((await picker.PickSaveFileAsync())?.Path is not { } path) return;
            try { await backend.CallAsync("glossary_export", new JsonObject { ["glossary_id"] = loadedId, ["destination"] = path }); Show("已导出：" + path); }
            catch (Exception error) { Fail(error); }
        };

        // The buttons sit above the list so a short window never pushes them out of view.
        var left = new StackPanel { Spacing = 8, Width = 250 };
        left.Children.Add(Row(create, copy, delete));
        left.Children.Add(Row(import, export));
        left.Children.Add(extract);
        left.Children.Add(list);
        var right = new StackPanel { Spacing = 10 };
        right.Children.Add(nameBox);
        right.Children.Add(languageRow);
        right.Children.Add(new TextBlock { Text = GlossaryHint, FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        right.Children.Add(textBox);
        right.Children.Add(count);
        right.Children.Add(conflicts);
        right.Children.Add(message);
        var layout = new Grid { ColumnSpacing = 18, Width = 820 };
        layout.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        layout.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        layout.Children.Add(left); Grid.SetColumn(right, 1); layout.Children.Add(right);

        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "术语表", Content = layout, CloseButtonText = "完成", DefaultButton = ContentDialogButton.Close };
        dialog.Resources["ContentDialogMaxWidth"] = 900d;
        dialog.Closing += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try { if (!await Commit()) args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        dialog.Opened += async (_, _) => { try { await Refresh(initialId); } catch (Exception error) { Fail(error); } };
        // Dialogs cannot stack: extraction closes the manager, then reopens it on the result.
        extract.Click += async (_, _) => { if (await Commit()) { extracting = true; dialog.Hide(); } };
        await ShowDialogAsync(dialog);
        if (extracting)
        {
            string? into = await TermSuggestionsDialogAsync(loadedBuiltin ? null : loadedId);
            await GlossaryManagerAsync(into ?? loadedId);
        }
    }

    /// Terms written more than once with different renderings, as "term（第 3、40 行）".
    private static List<string> GlossaryConflicts(string text)
    {
        var separator = new System.Text.RegularExpressions.Regex(@"\t|\s*=>\s*|\s*→\s*|\s*=\s*");
        var seen = new Dictionary<string, (string Source, List<int> Lines, HashSet<string> Targets)>();
        var order = new List<string>();
        var lines = GlossaryText(text).Split('\n');
        for (int index = 0; index < lines.Length; index++)
        {
            string line = lines[index].Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            var parts = separator.Split(line, 2);
            string source = parts[0].Trim(), target = parts.Length > 1 ? parts[1].Trim() : "", key = source.ToLowerInvariant();
            if (!seen.ContainsKey(key)) { seen[key] = (source, new List<int>(), new HashSet<string>()); order.Add(key); }
            seen[key].Lines.Add(index + 1); seen[key].Targets.Add(target);
        }
        return order.Where(k => seen[k].Targets.Count > 1).Select(k => $"{seen[k].Source}（第 {string.Join("、", seen[k].Lines)} 行）").ToList();
    }
}
