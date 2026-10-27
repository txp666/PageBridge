using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using global::Windows.Foundation;

namespace PageBridge.Windows;

/// <summary>Original PDF page with translations confined to the parser's source rectangles.</summary>
internal sealed partial class StructuredReadingPane
{
    private readonly BackendClient backend;
    private readonly ContinuousPdfPane pages;
    private readonly Dictionary<int, JsonNode> metadata = [];
    private string identity = "", documentId = "";
    private int generation;
    internal Grid View { get; } = new();
    internal ScrollViewer Scroll => pages.Scroll;
    internal ContinuousPdfPane.Anchor Position => pages.Position;
    internal int CurrentPage => pages.CurrentPage;
    internal event Action? PositionChanged;
    internal event Action? Activated;
    internal event Action<string>? PreviewError;
    internal event Action<int, JsonArray?>? LocateRequested;
    internal event Func<string, Task>? RetryRequested;

    internal StructuredReadingPane(BackendClient backend)
    {
        this.backend = backend;
        pages = new ContinuousPdfPane(backend, allowSelection: false);
        // Use exactly the same viewport and padding as the original pane.
        // Normal reading has no translation-only toolbar above the page.
        View.Children.Add(Scroll);
        AutomationProperties.SetName(Scroll, "保持原页版式的译文");
        AutomationProperties.SetAutomationId(Scroll, "StructuredReading");
        pages.PageOverlayRequested = BuildOverlayAsync;
        pages.PositionChanged += _ => PositionChanged?.Invoke();
        pages.Activated += _ => Activated?.Invoke();
        pages.PreviewError += message => PreviewError?.Invoke(message);
    }

    internal void Clear()
    {
        generation++; identity = documentId = ""; metadata.Clear(); pages.Clear();
    }
    internal void SetZoom(double value) => pages.SetZoom(value);
    internal void SetActive(bool value) => pages.SetActive(value);
    internal void MoveTo(ContinuousPdfPane.Anchor anchor) => pages.MoveTo(anchor);
    internal void Jump(int page) => pages.Jump(page);

    private static string Text(JsonNode? value, string key) => value?[key]?.GetValue<string>() ?? "";
    private static TextBlock Prose(string text, double size = 15, bool monospace = false)
    {
        var block = new TextBlock { Text = text, FontSize = size, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, LineHeight = size * 1.5 };
        if (monospace) block.FontFamily = new FontFamily("Consolas");
        return block;
    }

    internal async Task BindAsync(string id, string revision, int page)
    {
        string key = id + "/" + revision;
        if (identity == key) return;
        Clear(); identity = key; documentId = id;
        try { await pages.BindAsync(id, "original", key, page); }
        catch { identity = ""; throw; }
    }

    private async Task<UIElement?> BuildOverlayAsync(int pageNumber, double pageWidth, double pageHeight, ImageSource source)
    {
        int current = generation;
        if (!metadata.TryGetValue(pageNumber, out var result)
            || (Text(result, "decorationsImage") is { Length: > 0 } cached && !File.Exists(cached))
            || (Text(result, "backgroundImage") is { Length: > 0 } cachedBackground && !File.Exists(cachedBackground)))
        {
            result = await backend.CallAsync("reading_page", new JsonObject { ["document_id"] = documentId, ["page"] = pageNumber });
            if (current != generation || result is null) return null;
            metadata[pageNumber] = result;
        }
        var overlay = new Canvas { Width = pageWidth, Height = pageHeight, Clip = new RectangleGeometry { Rect = new Rect(0, 0, pageWidth, pageHeight) } };
        var blocks = result["blocks"]?.AsArray() ?? [];
        bool hasBackground = Text(result, "backgroundImage") is { Length: > 0 };
        var positioned = (result["backgroundBlocks"]?.AsArray() ?? []).Select(value => value!.GetValue<string>()).ToHashSet();
        if (hasBackground)
            overlay.Children.Add(new Image { Source = new BitmapImage(new Uri(Text(result, "backgroundImage"))), Width = pageWidth,
                Height = pageHeight, Stretch = Stretch.Fill, IsHitTestVisible = false });
        void AddRegions(bool sourceRegions)
        {
            foreach (var block in blocks)
            {
                if (block?["bbox"] is not JsonArray { Count: 4 } bbox || IsSourceRegion(Text(block, "kind")) != sourceRegions) continue;
                AddBlock(overlay, block, bbox, current, pageNumber, pageWidth, pageHeight, source,
                    hasBackground, !hasBackground || positioned.Contains(Text(block, "id")));
            }
        }
        if (hasBackground) AddRegions(true);
        AddRegions(false);
        // This trusted local image contains original pixels only on PDF strokes.
        if (!hasBackground && Text(result, "decorationsImage") is { Length: > 0 } path)
            overlay.Children.Add(new Image { Source = new BitmapImage(new Uri(path)), Width = pageWidth, Height = pageHeight, Stretch = Stretch.Fill, IsHitTestVisible = false });
        // Source-region pixels and hit targets remain above text and strokes.
        if (!hasBackground) AddRegions(true);
        AddPageNotices(overlay, result, pageNumber, pageWidth, current);
        return overlay;
    }

    private void AddPageNotices(Canvas overlay, JsonNode result, int pageNumber, double pageWidth, int current)
    {
        string message = Text(result, "page_status") switch
        {
            "not_selected" => "本页未生成译文，显示原页。",
            "source_fallback" => Text(result, "error") is { Length: > 0 } error ? error : "本页解析未完成，保留原页。",
            _ => ""
        };
        if (Text(result, "decorations_error") is { Length: > 0 } decorationError) message += " " + decorationError;
        var blocks = (result["blocks"]?.AsArray() ?? []).Where(block => block is not null && block["bbox"] is not JsonArray { Count: 4 }).ToArray();
        if (message.Length == 0 && blocks.Length == 0) return;
        var panel = new StackPanel { Spacing = 4, MaxWidth = Math.Max(1, pageWidth - 16) };
        if (message.Length > 0)
            panel.Children.Add(new Border { Background = new SolidColorBrush(Colors.LightYellow), Padding = new Thickness(5, 3, 5, 3),
                Child = new TextBlock { Text = message.Trim(), FontSize = 10, Foreground = new SolidColorBrush(Colors.Black), TextWrapping = TextWrapping.Wrap } });
        if (blocks.Length > 0)
        {
            var unlocated = new Button { Content = "未定位段落（" + blocks.Length + "）", FontSize = 10, Padding = new Thickness(5, 3, 5, 3), HorizontalAlignment = HorizontalAlignment.Left };
            var details = new StackPanel { Spacing = 20 }; var flyout = DetailsFlyout(details);
            foreach (var block in blocks) details.Children.Add(BlockDetails(block!, null, current, pageNumber, flyout));
            unlocated.Flyout = flyout; panel.Children.Add(unlocated);
        }
        Canvas.SetLeft(panel, 8); Canvas.SetTop(panel, 8); overlay.Children.Add(panel);
    }

    private void AddBlock(Canvas overlay, JsonNode block, JsonArray bbox, int current, int pageNumber, double pageWidth, double pageHeight,
        ImageSource source, bool hasBackground, bool positioned)
    {
        double x = bbox[0]!.GetValue<double>(), y = bbox[1]!.GetValue<double>();
        double width = Math.Min(pageWidth, bbox[2]!.GetValue<double>()) - x, height = Math.Min(pageHeight, bbox[3]!.GetValue<double>()) - y;
        if (width <= 0 || height <= 0) return;
        string kind = Text(block, "kind"), status = Text(block, "status"), translated = Text(block, "translatedText");
        var area = new Grid { Width = width, Height = height, Background = new SolidColorBrush(Colors.Transparent), Clip = new RectangleGeometry { Rect = new Rect(0, 0, width, height) } };
        bool overflow = false;
        if (IsSourceRegion(kind) && !hasBackground)
        {
            var original = new Image { Width = pageWidth, Height = pageHeight, Stretch = Stretch.Fill, IsHitTestVisible = false, Source = source };
            var crop = new Canvas { Width = width, Height = height, IsHitTestVisible = false, Clip = new RectangleGeometry { Rect = new Rect(0, 0, width, height) } };
            Canvas.SetLeft(original, -x); Canvas.SetTop(original, -y); crop.Children.Add(original);
            area.Children.Add(crop);
        }
        if (kind is "text" or "heading" or "list" && status == "translated" && !string.IsNullOrWhiteSpace(translated) && positioned)
        {
            var color = BackgroundColor(block);
            if (!hasBackground) area.Background = new SolidColorBrush(color);
            var translatedContent = TranslatedContent(block, width, height, pageWidth, pageHeight, source, color);
            overflow = translatedContent.Overflow;
            area.Children.Add(translatedContent.Content);
        }
        if (overflow || status == "source_fallback")
        {
            var badge = new Border { Background = new SolidColorBrush(Colors.DarkOrange), HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom,
                MaxWidth = width, MaxHeight = height, Child = new TextBlock { Text = overflow ? " … " : " ! ", FontSize = 9, Foreground = new SolidColorBrush(Colors.White) } };
            area.Children.Add(badge);
        }
        var target = new Button { Content = area, Width = width, Height = height, MinWidth = 0, MinHeight = 0, Padding = new Thickness(0), BorderThickness = new Thickness(0),
            CornerRadius = new CornerRadius(0), Background = new SolidColorBrush(Colors.Transparent), HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch,
            Clip = new RectangleGeometry { Rect = new Rect(0, 0, width, height) } };
        string hint = overflow ? "此段译文超出原区域，点击查看全文" : status == "source_fallback" ? "此段未译，点击查看原因或重试" : "点击查看译文与原文";
        AutomationProperties.SetName(target, KindLabel(kind) + "：" + hint); ToolTipService.SetToolTip(target, hint);
        var flyout = DetailsFlyout(); flyout.Content = DetailsScroll(BlockDetails(block, bbox, current, pageNumber, flyout)); target.Flyout = flyout;
        Canvas.SetLeft(target, x); Canvas.SetTop(target, y); overlay.Children.Add(target);
    }

    private static bool FitText(TextBlock text, double width, double height, double initial, double minimum = 8)
        => FitContent(text, width, height, initial, size => { text.FontSize = size; text.LineHeight = size * 1.15; }, minimum);

    private static bool FitContent(FrameworkElement text, double width, double height, double initial, Action<double> setSize, double minimum = 8)
    {
        bool Fits(double size)
        {
            setSize(size);
            text.Measure(new Size(width, double.PositiveInfinity));
            return text.DesiredSize.Height <= height + .01 && text.DesiredSize.Width <= width + .01;
        }
        if (Fits(initial)) return true;
        if (!Fits(minimum)) return false;
        double low = minimum, high = initial;
        for (int i = 0; i < 6; i++) { double middle = (low + high) / 2; if (Fits(middle)) low = middle; else high = middle; }
        Fits(low); return true;
    }

    private static global::Windows.UI.Color BackgroundColor(JsonNode block)
        => ReadColor(block["backgroundColor"], Colors.White);

    private static global::Windows.UI.Color ReadColor(JsonNode? node, global::Windows.UI.Color fallback)
    {
        if (node is JsonArray { Count: 3 } rgb)
        {
            var channels = new byte[3];
            for (int i = 0; i < 3; i++)
            {
                if (rgb[i] is not JsonValue value) return fallback;
                double channel;
                if (value.TryGetValue<double>(out var number)) channel = number;
                else if (value.TryGetValue<int>(out var integer)) channel = integer;
                else return fallback;
                if (!double.IsFinite(channel) || channel < 0 || channel > 255) return fallback;
                channels[i] = (byte)Math.Round(channel);
            }
            return global::Windows.UI.Color.FromArgb(255, channels[0], channels[1], channels[2]);
        }
        return fallback;
    }

    private static string KindLabel(string kind) => kind switch { "heading" => "标题", "list" => "列表", "table" => "表格", "formula" => "公式", "figure" => "图片", "code" => "代码", _ => "段落" };
    private static bool IsSourceRegion(string kind) => kind is "table" or "formula" or "figure" or "code";
    private static ScrollViewer DetailsScroll(UIElement details) => new() { Content = details, MaxWidth = 580, MaxHeight = 560, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private static Flyout DetailsFlyout(UIElement? details = null) => new() { Content = details is null ? null : DetailsScroll(details) };

    private StackPanel BlockDetails(JsonNode block, JsonArray? bbox, int current, int pageNumber, Flyout flyout)
    {
        string kind = Text(block, "kind"), status = Text(block, "status"), translated = Text(block, "translatedText"), source = Text(block, "sourceText");
        var details = new StackPanel { Spacing = 10, MaxWidth = 540 };
        details.Children.Add(new TextBlock { Text = KindLabel(kind) + (status == "source_fallback" ? " · 未译，原文保留" : status == "preserved" ? " · 原文保留" : ""), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        if (status == "source_fallback" && Text(block, "error") is { Length: > 0 } error) details.Children.Add(Prose(error, 12));
        if (status == "translated" && !string.IsNullOrWhiteSpace(translated))
        {
            details.Children.Add(new TextBlock { Text = "完整译文", FontSize = 12 });
            details.Children.Add(Prose(translated));
        }
        if (source.Length > 0)
        {
            details.Children.Add(new TextBlock { Text = kind == "code" ? "代码原文" : "原文", FontSize = 12 });
            details.Children.Add(Prose(source, kind == "code" ? 13 : 14, kind == "code"));
        }
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var locate = new Button { Content = "定位原文", FontSize = 12 };
        locate.Click += (_, _) => { if (current != generation) return; flyout.Hide(); LocateRequested?.Invoke(pageNumber, bbox?.DeepClone() as JsonArray); };
        actions.Children.Add(locate);
        if (status == "source_fallback")
        {
            var retry = new Button { Content = "重试此段", FontSize = 12 };
            retry.Click += async (_, _) => { if (current != generation) return; retry.IsEnabled = false; flyout.Hide(); try { if (RetryRequested is not null) await RetryRequested(Text(block, "id")); } finally { if (current == generation) retry.IsEnabled = true; } };
            actions.Children.Add(retry);
        }
        details.Children.Add(actions); return details;
    }
}
