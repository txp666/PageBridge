using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private void UpdatePDFPaneLabels()
    {
        foreach (var (pane, translated) in new[] { (leftPane, false), (rightPane, true) })
        {
            if (pane?.Children.FirstOrDefault() is not Grid header) continue;
            var label = header.Children.OfType<TextBlock>().FirstOrDefault(item => item.Tag as string == "paneLanguage");
            if (label is null) continue;
            label.Text = selected is null ? "" : LanguageName(Text(selected, ShowingReading
                ? (translated ? "reading_target_language" : "reading_source_language")
                : (translated ? "target_language" : "source_language")));
            if (selected is not null && translated && mode.SelectedIndex == 1 && !sync.IsOn)
                label.Text += $" · 第 {(ShowingReading ? structuredReading.CurrentPage : translatedPages.CurrentPage)} 页";
        }
    }
}
