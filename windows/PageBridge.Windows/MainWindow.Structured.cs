using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private StructuredReadingPane structuredReading = null!;
    private readonly ComboBox readingOutput = new() { Width = 130, MinHeight = 25, FontSize = 11, Padding = new Thickness(6, 1, 6, 1), HorizontalAlignment = HorizontalAlignment.Left };
    private readonly TextBlock readingPageNoticeText = new() { FontSize = 10.5, TextWrapping = TextWrapping.NoWrap, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 };
    private readonly Button readingPageNotice = new() { MinWidth = 0, MinHeight = 0, Height = 25, Padding = new Thickness(6, 0, 0, 0), BorderThickness = new Thickness(0),
        Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent), HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Right,
        VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed };
    private bool changingReadingOutput, showReading, structuredActive;
    private bool HasReading => Text(selected, "reading_path") != "";
    private bool HasTranslation => HasReading || Text(selected, "translated_path") != "";
    private bool ShowingReading => HasReading && showReading;

    private void InitializeStructuredReading()
    {
        structuredReading = new StructuredReadingPane(backend);
        Grid.SetRow(structuredReading.View, 1); rightPane.Children.Add(structuredReading.View);
        structuredReading.Activated += () =>
        {
            if (!ShowingReading) return;
            structuredActive = true; activePages = originalPages; originalPages.ClearSelection();
            UpdateSelectionTools(); AcceptReadingPosition(structuredReading.CurrentPage);
        };
        structuredReading.PositionChanged += () =>
        {
            UpdatePDFPaneLabels();
            if (bindingPages || selected is null || !ShowingReading || !structuredActive) return;
            if (sync.IsOn && mode.SelectedIndex == 1) originalPages.MoveTo(structuredReading.Position);
            AcceptReadingPosition(structuredReading.CurrentPage);
        };
        structuredReading.PageMetadataChanged += UpdatePDFPaneLabels;
        structuredReading.PreviewError += message => status.Text = "预览加载失败：" + message;
        if (rightPane.Children[0] is Grid header)
        {
            header.Children[0].Visibility = Visibility.Collapsed;
            header.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
            header.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
            var language = header.Children.OfType<TextBlock>().FirstOrDefault(item => item.Tag as string == "paneLanguage");
            if (language is not null)
            {
                Grid.SetColumn(language, 1); language.VerticalAlignment = VerticalAlignment.Center;
                language.TextTrimming = TextTrimming.CharacterEllipsis;
            }
            header.Children.Add(readingOutput);
            readingPageNotice.Content = readingPageNoticeText;
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(readingPageNotice, "StructuredReadingPageNotice");
            readingPageNotice.Click += (_, _) => structuredReading.CurrentPageDetails()?.ShowAt(readingPageNotice);
            Grid.SetColumn(readingPageNotice, 1); header.Children.Add(readingPageNotice);
            // The output selector is taller than the original's plain title.
            // Equal header heights keep both PDF viewports on the same baseline.
            if (leftPane.Children[0] is FrameworkElement originalHeader)
                header.SizeChanged += (_, _) => { if (header.ActualHeight > 0) originalHeader.Height = header.ActualHeight; };
        }
        readingOutput.Items.Add("对照阅读稿"); readingOutput.Items.Add("PDF 译文");
        readingOutput.SelectionChanged += async (_, _) =>
        {
            if (changingReadingOutput || selected is null) return;
            var anchor = ActiveReadingPosition;
            showReading = readingOutput.SelectedIndex == 0;
            ResetDocumentSearch(); ApplyStructuredVisibility(); ApplyMode();
            await Attempt(RefreshStructuredPageAsync);
            if (ShowingReading) structuredReading.MoveTo(anchor);
            else translatedPages.MoveTo(anchor);
        };
        structuredReading.LocateRequested += (number, bbox) =>
        {
            if (selected is null) return;
            if (mode.SelectedIndex == 2) mode.SelectedIndex = 1;
            ActivateVariant("original"); page.Value = number + 1; originalPages.Jump(number + 1);
            if (bbox?.Count == 4)
            {
                double x = bbox[0]!.GetValue<double>(), y = bbox[1]!.GetValue<double>();
                var rect = new PdfRect(x, y, bbox[2]!.GetValue<double>() - x, bbox[3]!.GetValue<double>() - y);
                var hit = new JsonObject { ["page"] = number, ["rects"] = new JsonArray(rect.ToJson()) };
                originalPages.SetSearchHits(new JsonArray(hit)); originalPages.RevealSearchHit(hit);
            }
        };
        structuredReading.RetryRequested += id => Attempt(() => RunTranslationAsync(id));
        ApplyStructuredVisibility();
    }

    private void SelectStructuredOutput(JsonObject document)
    {
        changingReadingOutput = true;
        try
        {
            bool hasReading = Text(document, "reading_path") != "", hasPdf = Text(document, "translated_path") != "";
            showReading = hasReading && (!hasPdf || Text(document, "preferred_artifact") == "reading");
            readingOutput.SelectedIndex = showReading ? 0 : 1;
            readingOutput.IsEnabled = hasReading && hasPdf;
        }
        finally { changingReadingOutput = false; }
        structuredActive = ShowingReading && mode.SelectedIndex == 2;
        activePages = mode.SelectedIndex == 2 && !ShowingReading ? translatedPages : originalPages;
        structuredReading.Clear(); ApplyStructuredVisibility();
    }

    private void ApplyStructuredVisibility()
    {
        if (structuredReading is null) return;
        structuredReading.View.Visibility = ShowingReading ? Visibility.Visible : Visibility.Collapsed;
        structuredReading.SetActive(ShowingReading && mode.SelectedIndex != 0);
        translatedPages.Scroll.Visibility = ShowingReading ? Visibility.Collapsed : Visibility.Visible;
        emptyTranslation.Visibility = HasTranslation ? Visibility.Collapsed : Visibility.Visible;
        UpdatePDFPaneLabels();
    }

    private Task RefreshStructuredPageAsync() => selected is not null && ShowingReading
        ? structuredReading.BindAsync(Text(selected, "id"), Text(selected, "reading_path"), Math.Clamp((int)page.Value, 1, (int)page.Maximum))
        : Task.CompletedTask;

    private ContinuousPdfPane.Anchor ActiveReadingPosition => ShowingReading && structuredActive ? structuredReading.Position : activePages.Position;

    private void SynchronizeReaders()
    {
        if (!sync.IsOn || mode.SelectedIndex != 1) return;
        if (ShowingReading)
        {
            if (structuredActive) originalPages.MoveTo(structuredReading.Position);
            else structuredReading.MoveTo(originalPages.Position);
        }
        else OtherPane(activePages).MoveTo(activePages.Position);
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
}
