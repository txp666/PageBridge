using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media.Imaging;

namespace PageBridge.Windows;

/// <summary>Continuous PDF pages with lightweight geometry and a bounded visible-page image set.</summary>
internal sealed partial class ContinuousPdfPane
{
    internal readonly record struct Anchor(int Page, double Fraction);
    private sealed record PageSize(double Width, double Height, int Rotation);
    private sealed class PageView
    {
        internal required Grid Paper;
        internal required Image Image;
        internal required TextBlock Placeholder;
        internal readonly Canvas Markup = new();
        internal readonly Canvas SelectionLayer = new() { IsHitTestVisible = false };
        internal readonly Canvas SearchLayer = new() { IsHitTestVisible = false };
        internal JsonArray Links = [];
        internal List<TextToken>? Tokens;
        internal Task? TextLoad;
        internal string SourceHash = "";
        internal int RenderWidth;
    }
    private readonly BackendClient backend;
    private readonly StackPanel stack = new() { Spacing = 16, HorizontalAlignment = HorizontalAlignment.Center };
    private readonly List<PageSize> sizes = [];
    private readonly List<PageView> views = [];
    private readonly Dictionary<(int Page, int Width), string> cache = [];
    private readonly HashSet<(int Page, int Width)> failed = [];
    private double[] offsets = [];
    private string documentId = "", variant = "", identity = "";
    private int generation;
    private bool pumping, arranging, active = true;
    private double zoom = 1, paperWidth = 500;
    private Anchor? pending;
    internal ScrollViewer Scroll { get; }
    internal int CurrentPage => views.Count > 0 && Scroll.ScrollableHeight > 0 && Scroll.VerticalOffset >= Scroll.ScrollableHeight - 1 ? views.Count : Position.Page + 1;
    internal event Action<ContinuousPdfPane>? PositionChanged;
    internal event Action<ContinuousPdfPane>? Activated;
    internal event Action<string>? PreviewError;
    internal Anchor Position
    {
        get
        {
            if (offsets.Length == 0) return new Anchor(0, 0);
            double offset = Math.Max(0, Scroll.VerticalOffset);
            int index = Array.BinarySearch(offsets, offset);
            if (index < 0) index = Math.Max(0, ~index - 1);
            index = Math.Min(index, sizes.Count - 1);
            return new Anchor(index, Math.Clamp((offset - offsets[index]) / Math.Max(1, views[index].Paper.Height), 0, 1));
        }
    }

    internal ContinuousPdfPane(BackendClient backend)
    {
        this.backend = backend;
        Scroll = new ScrollViewer { Content = stack, Padding = new Thickness(16), HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollMode = ScrollMode.Enabled, VerticalScrollMode = ScrollMode.Enabled, HorizontalContentAlignment = HorizontalAlignment.Center };
        Scroll.SizeChanged += (_, _) => ArrangePages();
        Scroll.ViewChanged += (_, _) => { if (!arranging) { PositionChanged?.Invoke(this); RefreshVisible(); } };
        Scroll.AddHandler(UIElement.PointerWheelChangedEvent, new PointerEventHandler((_, _) => Activated?.Invoke(this)), true);
        Scroll.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler((_, _) => Activated?.Invoke(this)), true);
        Scroll.GotFocus += (_, _) => Activated?.Invoke(this);
        Scroll.Loaded += (_, _) => { ArrangePages(); RefreshVisible(); };
        Scroll.IsTabStop = true;
        AttachSelectionKeyboard();
    }

    internal async Task BindAsync(string id, string kind, string key, int page)
    {
        if (identity == key && sizes.Count > 0) { Jump(page); return; }
        Clear(); identity = key; documentId = id; variant = kind; pending = new Anchor(Math.Max(0, page - 1), 0);
        int current = generation;
        var data = await backend.CallAsync("page_info", new JsonObject { ["document_id"] = id, ["variant"] = kind });
        if (current != generation) return;
        foreach (var entry in data?["pages"]?.AsArray() ?? [])
        {
            double width = entry?["width"]?.GetValue<double>() ?? 0, height = entry?["height"]?.GetValue<double>() ?? 0;
            if (width <= 0 || height <= 0) throw new InvalidOperationException("PDF 页面的尺寸无效。");
            sizes.Add(new PageSize(width, height, entry?["rotation"]?.GetValue<int>() ?? 0));
            var image = new Image { Stretch = Stretch.Uniform, HorizontalAlignment = HorizontalAlignment.Stretch, VerticalAlignment = VerticalAlignment.Stretch };
            var placeholder = new TextBlock { Text = $"第 {sizes.Count} 页", Foreground = new SolidColorBrush(Colors.Gray), FontSize = 12, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
            var paper = new Grid { Background = new SolidColorBrush(Colors.White), Width = paperWidth, Height = paperWidth * height / width };
            paper.Children.Add(placeholder); paper.Children.Add(image);
            var view = new PageView { Paper = paper, Image = image, Placeholder = placeholder };
            paper.Children.Add(view.SearchLayer); paper.Children.Add(view.Markup); paper.Children.Add(view.SelectionLayer);
            views.Add(view); AttachSelection(view, views.Count - 1); stack.Children.Add(paper);
        }
        ArrangePages();
    }

    internal void Clear()
    {
        generation++; identity = documentId = variant = ""; pending = null;
        ClearSelection(); annotationItems = []; searchHits = []; selectingPage = -1;
        foreach (var view in views) view.Image.Source = null;
        sizes.Clear(); views.Clear(); stack.Children.Clear(); offsets = []; cache.Clear(); failed.Clear();
    }
    internal void SetActive(bool value) { active = value; if (active) Refresh(); }
    internal void SetZoom(double value) { if (Math.Abs(zoom - value) < .001) return; zoom = value; ArrangePages(); }
    internal void Refresh() { failed.Clear(); ArrangePages(); RefreshVisible(); }
    internal void Jump(int page) { failed.Clear(); MoveTo(new Anchor(Math.Max(0, page - 1), 0)); }
    internal void MoveTo(Anchor anchor)
    {
        if (views.Count == 0 || offsets.Length != views.Count || Scroll.ActualWidth <= 40) { pending = anchor; return; }
        int index = Math.Clamp(anchor.Page, 0, views.Count - 1);
        double target = offsets[index] + views[index].Paper.Height * Math.Clamp(anchor.Fraction, 0, 1);
        if (Math.Abs(Scroll.VerticalOffset - target) > 1) Scroll.ChangeView(null, target, null, true);
        RefreshVisible();
    }
    private void ArrangePages()
    {
        if (arranging || views.Count == 0 || Scroll.ActualWidth <= 40) return;
        var anchor = pending ?? Position;
        arranging = true;
        try
        {
            paperWidth = Math.Max(120, Scroll.ActualWidth - 36) * zoom;
            offsets = new double[views.Count]; double top = 0;
            for (int i = 0; i < views.Count; i++)
            {
                offsets[i] = top; views[i].Paper.Width = paperWidth; views[i].Paper.Height = paperWidth * sizes[i].Height / sizes[i].Width;
                DrawSearch(i); DrawMarkup(i); DrawSelection(i);
                top += views[i].Paper.Height + stack.Spacing;
            }
            Scroll.UpdateLayout(); pending = null;
            int index = Math.Clamp(anchor.Page, 0, views.Count - 1);
            Scroll.ChangeView(null, offsets[index] + views[index].Paper.Height * anchor.Fraction, null, true);
        }
        finally { arranging = false; }
        RefreshVisible();
    }
    private (int First, int Last) VisibleRange()
    {
        if (views.Count == 0 || offsets.Length == 0) return (0, -1);
        int first = Position.Page;
        double bottom = Scroll.VerticalOffset + Math.Max(100, Scroll.ViewportHeight);
        int last = first; while (last + 1 < views.Count && offsets[last + 1] < bottom) last++;
        return (Math.Max(0, first - 1), Math.Min(views.Count - 1, last + 1));
    }
    private int RenderWidth => Math.Clamp((int)Math.Ceiling(paperWidth * (Scroll.XamlRoot?.RasterizationScale ?? 1)), 140, 1800);
    private void RefreshVisible()
    {
        var range = VisibleRange();
        for (int i = 0; i < views.Count; i++) if ((i < range.First || i > range.Last) && (views[i].Image.Source is not null || views[i].RenderWidth != 0))
        {
            views[i].Image.Source = null; views[i].RenderWidth = 0; views[i].Placeholder.Visibility = Visibility.Visible;
        }
        if (active && !pumping && range.Last >= range.First && Scroll.Visibility == Visibility.Visible) _ = PumpAsync();
    }
    private async Task PumpAsync()
    {
        pumping = true;
        try
        {
            while (active && views.Count > 0 && Scroll.ActualWidth > 40)
            {
                var range = VisibleRange(); int width = RenderWidth;
                var candidates = Enumerable.Range(range.First, Math.Max(0, range.Last - range.First + 1)).OrderBy(i => Math.Abs(i - Position.Page));
                int index = candidates.FirstOrDefault(i => views[i].RenderWidth != width && !failed.Contains((i, width)), -1);
                if (index < 0) return;
                int current = generation; var view = views[index]; var key = (index, width);
                try
                {
                    if (!cache.TryGetValue(key, out string? path) || !File.Exists(path))
                    {
                        var reply = await backend.CallAsync("render", new JsonObject { ["document_id"] = documentId, ["variant"] = variant, ["page"] = index, ["width"] = width });
                        if (current != generation) continue;
                        path = reply?["path"]?.GetValue<string>() ?? throw new InvalidOperationException("预览路径无效。"); cache[key] = path;
                    }
                    if (current != generation) continue;
                    range = VisibleRange();
                    if (index < range.First || index > range.Last) continue;
                    if (!active || width != RenderWidth) continue;
                    var bitmap = new BitmapImage();
                    bitmap.ImageOpened += (_, _) => { if (current == generation && ReferenceEquals(view.Image.Source, bitmap)) view.Placeholder.Visibility = Visibility.Collapsed; };
                    bitmap.ImageFailed += (_, _) =>
                    {
                        if (current != generation || !ReferenceEquals(view.Image.Source, bitmap)) return;
                        cache.Remove(key); failed.Add(key); view.RenderWidth = 0; view.Image.Source = null;
                        view.Placeholder.Text = $"第 {index + 1} 页 · 请重新定位此页以重试"; view.Placeholder.Visibility = Visibility.Visible;
                    };
                    view.Placeholder.Visibility = Visibility.Visible; view.Image.Source = bitmap; view.RenderWidth = width;
                    bitmap.UriSource = new Uri(path);
                    _ = LoadTextAsync(index, view, current);
                }
                catch (Exception error)
                {
                    if (current != generation) continue;
                    failed.Add(key); view.Placeholder.Text = $"第 {index + 1} 页 · 预览暂不可用";
                    PreviewError?.Invoke(error.Message);
                }
            }
        }
        finally { pumping = false; }
    }
}
