using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
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
                label.Text += $" · 第 {(ShowingReading && structuredReading is { } reading ? reading.CurrentPage : translatedPages.CurrentPage)} 页";
            if (translated)
            {
                var notice = selected is not null && ShowingReading ? structuredReading?.CurrentPageNotice : null;
                label.Visibility = notice is null ? Visibility.Visible : Visibility.Collapsed;
                readingPageNotice.Visibility = notice is null ? Visibility.Collapsed : Visibility.Visible;
                readingPageNoticeText.Text = notice is { } current ? label.Text + " · " + current.Label : "";
                string description = notice?.Message ?? "";
                ToolTipService.SetToolTip(readingPageNotice, description.Length > 0 ? description : null);
                AutomationProperties.SetName(readingPageNotice, description);
                AutomationProperties.SetHelpText(readingPageNotice, description);
            }
        }
    }
}
