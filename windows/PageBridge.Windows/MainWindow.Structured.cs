using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private StructuredReadingPane structuredReading = null!;
    private readonly TextBlock readingPageNoticeText = new() { FontSize = 10.5, TextWrapping = TextWrapping.NoWrap, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 };
    private readonly Button readingPageNotice = new() { MinWidth = 0, MinHeight = 0, Height = 25, Padding = new Thickness(6, 0, 0, 0), BorderThickness = new Thickness(0),
        Background = Clear, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Right,
        VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed };
    private bool structuredActive;
    private bool HasReading => Text(selected, "reading_path") != "";

    private void InitializeStructuredReading()
    {
        structuredReading = new StructuredReadingPane(backend);
        Grid.SetRow(structuredReading.View, 1); rightPane.Children.Add(structuredReading.View);
        structuredReading.Activated += () =>
        {
            if (!HasReading) return;
            structuredActive = true; originalPages.ClearSelection();
            UpdateSelectionTools(); AcceptReadingPosition(structuredReading.CurrentPage);
        };
        structuredReading.PositionChanged += () =>
        {
            UpdatePDFPaneLabels();
            if (bindingPages || selected is null || !HasReading || !structuredActive) return;
            if (sync.IsOn && mode.SelectedIndex == 1) originalPages.MoveTo(structuredReading.Position);
            AcceptReadingPosition(structuredReading.CurrentPage);
        };
        structuredReading.PageMetadataChanged += UpdatePDFPaneLabels;
        structuredReading.PreviewError += message => status.Text = "预览加载失败：" + message;
        var header = (Grid)rightPane.Children[0];
        header.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetColumn(translatedLanguage, 1); translatedLanguage.VerticalAlignment = VerticalAlignment.Center;
        translatedLanguage.TextTrimming = TextTrimming.CharacterEllipsis;
        readingPageNotice.Content = readingPageNoticeText;
        AutomationProperties.SetAutomationId(readingPageNotice, "StructuredReadingPageNotice");
        readingPageNotice.Click += (_, _) => structuredReading.CurrentPageDetails()?.ShowAt(readingPageNotice);
        Grid.SetColumn(readingPageNotice, 1); header.Children.Add(readingPageNotice);
        // The page notice button is taller than the original's plain title.
        // Equal header heights keep both viewports on the same baseline.
        var originalHeader = (FrameworkElement)leftPane.Children[0];
        header.SizeChanged += (_, _) => { if (header.ActualHeight > 0) originalHeader.Height = header.ActualHeight; };
        structuredReading.LocateRequested += (number, bbox) =>
        {
            if (selected is null) return;
            if (mode.SelectedIndex == 2) mode.SelectedIndex = 1;
            ActivateOriginal(); page.Value = number + 1; originalPages.Jump(number + 1);
            if (bbox?.Count == 4)
            {
                double x = bbox[0]!.GetValue<double>(), y = bbox[1]!.GetValue<double>();
                var rect = new PdfRect(x, y, bbox[2]!.GetValue<double>() - x, bbox[3]!.GetValue<double>() - y);
                var hit = new JsonObject { ["page"] = number, ["rects"] = new JsonArray(rect.ToJson()) };
                originalPages.SetSearchHits(new JsonArray(hit)); originalPages.RevealSearchHit(hit);
            }
        };
        structuredReading.RetryRequested += id => Attempt(() => RunTranslationAsync(id));
        structuredReading.AddTermRequested += context => _ = Attempt(() => AddTermDialogAsync("", context));
        ApplyStructuredVisibility();
    }

    private void ResetStructuredReading()
    {
        structuredActive = HasReading && mode.SelectedIndex == 2;
        structuredReading.Clear(); ApplyStructuredVisibility();
    }

    private void ApplyStructuredVisibility()
    {
        structuredReading.View.Visibility = HasReading ? Visibility.Visible : Visibility.Collapsed;
        structuredReading.SetActive(HasReading && mode.SelectedIndex != 0);
        emptyTranslation.Visibility = HasReading ? Visibility.Collapsed : Visibility.Visible;
        UpdatePDFPaneLabels();
    }

    private Task RefreshStructuredPageAsync() => selected is not null && HasReading
        ? structuredReading.BindAsync(Text(selected, "id"), Text(selected, "reading_path"), Math.Clamp((int)page.Value, 1, (int)page.Maximum))
        : Task.CompletedTask;

    private ContinuousPdfPane.Anchor ActiveReadingPosition => HasReading && structuredActive ? structuredReading.Position : originalPages.Position;

    private void SynchronizeReaders()
    {
        if (!sync.IsOn || mode.SelectedIndex != 1 || !HasReading) return;
        if (structuredActive) originalPages.MoveTo(structuredReading.Position);
        else structuredReading.MoveTo(originalPages.Position);
    }

    private void AcceptReadingPosition(int number)
    {
        if (bindingPages || selected is null) return;
        int value = Math.Clamp(number, 1, (int)page.Maximum);
        if ((int)page.Value != value)
        {
            bool wasLoading = loading; loading = true; page.Value = value; loading = wasLoading;
            RememberReading(Text(selected, "id"), value); UpdateNavigationPage();
        }
        UpdatePDFPaneLabels();
    }

    private void UpdatePDFPaneLabels()
    {
        foreach (var (label, translated) in new[] { (originalLanguage, false), (translatedLanguage, true) })
        {
            label.Text = selected is null ? "" : LanguageName(Text(selected, HasReading
                ? (translated ? "reading_target_language" : "reading_source_language")
                : (translated ? "target_language" : "source_language")));
            if (selected is not null && translated && mode.SelectedIndex == 1 && !sync.IsOn)
                label.Text += $" · 第 {structuredReading.CurrentPage} 页";
            if (translated)
            {
                var notice = HasReading ? structuredReading.CurrentPageNotice : null;
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
