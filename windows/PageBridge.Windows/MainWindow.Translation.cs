using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private sealed record TranslationChoice(string Source, string Target, string? Pages);

    private async Task<TranslationChoice?> TranslationDialogAsync(string name, JsonNode? services)
    {
        ComboBox LanguagesPicker(string initial)
        {
            var picker = new ComboBox { Width = 175, HorizontalAlignment = HorizontalAlignment.Right };
            foreach (var item in Languages) picker.Items.Add(new ComboBoxItem { Content = item.Name, Tag = item.Code });
            picker.SelectedItem = picker.Items.OfType<ComboBoxItem>().FirstOrDefault(x => (string)x.Tag == initial) ?? picker.Items[0];
            return picker;
        }
        var source = LanguagesPicker(Text(selected, "source_language"));
        var target = LanguagesPicker(Text(selected, "target_language"));
        var range = new ComboBox { Width = 175, HorizontalAlignment = HorizontalAlignment.Right };
        range.Items.Add("全部页面"); range.Items.Add("指定页面"); range.SelectedIndex = 0;
        var pages = new TextBox { PlaceholderText = "例如 1-5, 8, 12-16", Width = 235 };
        var panel = new StackPanel { Spacing = 18, Width = 440 };
        panel.Children.Add(new TextBlock { Text = name, FontSize = 13, Opacity = .65, TextTrimming = TextTrimming.CharacterEllipsis });
        Grid Field(string caption, FrameworkElement control)
        {
            var row = new Grid(); row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) }); row.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); row.Children.Add(new TextBlock { Text = caption, FontSize = 13, VerticalAlignment = VerticalAlignment.Center }); Grid.SetColumn(control, 1); row.Children.Add(control); return row;
        }
        panel.Children.Add(Field("源语言", source)); panel.Children.Add(Field("目标语言", target)); panel.Children.Add(Field("翻译范围", range));
        var pageRow = Field("页码", pages); pageRow.Visibility = Visibility.Collapsed; panel.Children.Add(pageRow);
        range.SelectionChanged += (_, _) => pageRow.Visibility = range.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
        var profile = services?["profiles"]?.AsArray().FirstOrDefault(p => Text(p, "id") == Text(services, "active_id"));
        var service = new StackPanel { Spacing = 9 };
        service.Children.Add(Row(ReaderIcons.Make("globe"), new TextBlock { Text = "翻译服务", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, FontSize = 13 }));
        service.Children.Add(new TextBlock { Text = Text(profile, "name"), FontSize = 13 });
        service.Children.Add(new TextBlock { Text = Text(profile, "model"), FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        service.Children.Add(new TextBlock { Text = "PDF 在本机处理。使用在线 API 时，待翻译文本会发送至所选服务；本机服务需在本机运行。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(new Border { Child = service, Padding = new Thickness(14), CornerRadius = new CornerRadius(10), Background = Dark ? Brush(45, 45, 45) : Brush(238, 238, 238) });
        var error = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.OrangeRed) }; panel.Children.Add(error);
        panel.Children.Add(new TextBlock { Text = Text(selected, "translated_path") == "" ? "输出译文与双语 PDF" : "本次翻译成功后，将更新此文档的译文。原 PDF 会保留。", FontSize = 11, Opacity = .65 });
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
            args.Cancel = error.Text != "";
        };
        if (await ShowDialogAsync(dialog) != ContentDialogResult.Primary) return null;
        return new TranslationChoice((string)((ComboBoxItem)source.SelectedItem).Tag, (string)((ComboBoxItem)target.SelectedItem).Tag, range.SelectedIndex == 1 ? pages.Text.Trim() : null);
    }
}
