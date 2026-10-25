using System.Text;
using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using global::Windows.ApplicationModel.DataTransfer;
using global::Windows.Foundation;

namespace PageBridge.Windows;

internal sealed partial class ContinuousPdfPane
{
    private sealed record TextToken(string Text, PdfRect Rect, int Line);
    private JsonArray annotationItems = [];
    private int selectingPage = -1, selectionEpoch;
    private Point selectionStart, selectionEnd;
    private bool selectionDragged;
    internal string Variant => variant;
    internal PdfSelection? Selection { get; private set; }
    internal event Action<ContinuousPdfPane>? SelectionChanged;
    internal event Action<ContinuousPdfPane, string>? AnnotationInvoked;
    internal event Action<ContinuousPdfPane, string>? SelectionAction;
    internal event Action<ContinuousPdfPane, JsonNode>? LinkInvoked;

    // Explicit isolated QA launches only; no document text or identifiers.
    private static readonly bool TraceSelection = Environment.GetCommandLineArgs().Contains("--reader-validation");
    private static void SelectionTrace(string kind, int page, int count = 0)
    {
        if (!TraceSelection) return;
        try { File.AppendAllText(System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PageBridge-WindowsReaderValidation", $"qa-selection-{Environment.ProcessId}.ndjson"), System.Text.Json.JsonSerializer.Serialize(new { kind, page, count }) + "\n"); } catch { }
    }

    internal void ClearSelection()
    {
        selectionEpoch++; Selection = null;
        foreach (var view in views) view.SelectionLayer.Children.Clear();
        SelectionChanged?.Invoke(this);
    }

    internal void SetAnnotations(JsonArray items)
    {
        annotationItems = (JsonArray)items.DeepClone();
        for (int i = 0; i < views.Count; i++) DrawMarkup(i);
    }

    private void AttachSelection(PageView view, int index)
    {
        var menu = new MenuFlyout();
        foreach (var (label, action) in new[] { ("复制选中文字", "copy"), ("高亮", "highlight"), ("下划线", "underline"), ("添加笔记", "note"), ("询问助手", "assistant") })
        {
            var item = new MenuFlyoutItem { Text = label };
            item.Click += (_, _) => { Activated?.Invoke(this); if (action == "copy") CopySelection(); else SelectionAction?.Invoke(this, action); };
            menu.Items.Add(item);
        }
        menu.Opening += (_, _) => { foreach (var item in menu.Items.OfType<MenuFlyoutItem>()) item.IsEnabled = Selection is not null || item.Text == "添加笔记"; };
        view.Paper.ContextFlyout = menu;
        view.Paper.PointerPressed += async (_, e) =>
        {
            var point = e.GetCurrentPoint(view.Paper);
            SelectionTrace("pressed", index, point.Properties.IsLeftButtonPressed ? 1 : 0);
            if (!point.Properties.IsLeftButtonPressed || e.Pointer.PointerDeviceType == global::Microsoft.UI.Input.PointerDeviceType.Touch) return;
            Activated?.Invoke(this); Scroll.Focus(FocusState.Pointer); ClearSelection();
            selectingPage = index; selectionStart = selectionEnd = point.Position; selectionDragged = false;
            int epoch = selectionEpoch; SelectionTrace("capture", index, view.Paper.CapturePointer(e.Pointer) ? 1 : 0); e.Handled = true;
            await LoadTextAsync(index, view, generation);
            if (epoch == selectionEpoch && selectionDragged) SelectRange(index, view);
        };
        view.Paper.PointerMoved += (_, e) =>
        {
            var hover = e.GetCurrentPoint(view.Paper).Position;
            var link = LinkAt(index, view, hover);
            ToolTipService.SetToolTip(view.Paper, link is null ? null : link["kind"]?.GetValue<string>() == "page" ? "跳转到第 " + ((link["page"]?.GetValue<int>() ?? 0) + 1) + " 页" : link["url"]?.GetValue<string>());
            if (selectingPage != index) return;
            selectionEnd = e.GetCurrentPoint(view.Paper).Position;
            selectionDragged |= Math.Abs(selectionStart.X - selectionEnd.X) + Math.Abs(selectionStart.Y - selectionEnd.Y) > 3;
            if (selectionDragged) { SelectionTrace("drag", index, view.Tokens?.Count ?? -1); SelectRange(index, view); }
            e.Handled = true;
        };
        view.Paper.PointerReleased += (_, e) =>
        {
            SelectionTrace("released", index);
            if (selectingPage != index) return;
            selectionEnd = e.GetCurrentPoint(view.Paper).Position;
            if (selectionDragged) SelectRange(index, view);
            else if (LinkAt(index, view, selectionEnd) is { } link) LinkInvoked?.Invoke(this, link);
            selectingPage = -1; view.Paper.ReleasePointerCapture(e.Pointer); e.Handled = true;
        };
        view.Paper.PointerCanceled += (_, _) => { SelectionTrace("canceled", index); selectingPage = -1; ClearSelection(); };
        view.Paper.DoubleTapped += (_, e) =>
        {
            var point = e.GetPosition(view.Paper); double scale = view.Paper.Width / sizes[index].Width;
            foreach (var item in annotationItems)
                if (item?["regions"] is JsonArray regions && regions.Any(r => r?["page"]?.GetValue<int>() == index && PdfRect.Read(r?["rect"]).Contains(point.X / scale, point.Y / scale)))
                { AnnotationInvoked?.Invoke(this, item?["id"]?.GetValue<string>() ?? ""); e.Handled = true; return; }
            if (view.Tokens is not { Count: > 0 } tokens) return;
            Activated?.Invoke(this); int first = Nearest(tokens, point.X / scale, point.Y / scale), last = first;
            while (first > 0 && tokens[first - 1].Line == tokens[first].Line && !string.IsNullOrWhiteSpace(tokens[first - 1].Text)) first--;
            while (last + 1 < tokens.Count && tokens[last + 1].Line == tokens[last].Line && !string.IsNullOrWhiteSpace(tokens[last + 1].Text)) last++;
            CommitSelection(index, view, first, last); e.Handled = true;
        };
    }

    private void AttachSelectionKeyboard()
    {
        Scroll.KeyDown += (_, e) =>
        {
            if (e.Key == global::Windows.System.VirtualKey.C && (global::Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(global::Windows.System.VirtualKey.Control) & global::Windows.UI.Core.CoreVirtualKeyStates.Down) != 0)
            { CopySelection(); e.Handled = Selection is not null; }
        };
    }

    private Task LoadTextAsync(int index, PageView view, int current)
    {
        if (view.Tokens is not null) return Task.CompletedTask;
        return view.TextLoad ??= Load();
        async Task Load()
        {
            try
            {
                var data = await backend.CallAsync("page_text", new JsonObject { ["document_id"] = documentId, ["variant"] = variant, ["page"] = index });
                if (generation != current) return;
                view.SourceHash = data?["source_sha256"]?.GetValue<string>() ?? "";
                view.Links = data?["links"]?.DeepClone() as JsonArray ?? [];
                view.Tokens = (data?["tokens"]?.AsArray() ?? []).Select(t => new TextToken(t?["text"]?.GetValue<string>() ?? "", PdfRect.Read(t?["rect"]), t?["line"]?.GetValue<int>() ?? 0)).ToList();
                SelectionTrace("text-ready", index, view.Tokens.Count);
            }
            catch (Exception error) { if (generation == current) PreviewError?.Invoke("文字层暂不可用：" + error.Message); }
            finally { view.TextLoad = null; }
        }
    }

    private JsonNode? LinkAt(int index, PageView view, Point point)
    {
        double scale = view.Paper.Width / sizes[index].Width;
        return view.Links.FirstOrDefault(link => PdfRect.Read(link?["rect"]).Contains(point.X / scale, point.Y / scale));
    }

    private void SelectRange(int index, PageView view)
    {
        if (view.Tokens is not { Count: > 0 } tokens) return;
        double scale = view.Paper.Width / sizes[index].Width;
        int first = Nearest(tokens, selectionStart.X / scale, selectionStart.Y / scale), last = Nearest(tokens, selectionEnd.X / scale, selectionEnd.Y / scale);
        CommitSelection(index, view, Math.Min(first, last), Math.Max(first, last));
    }

    private static int Nearest(List<TextToken> tokens, double x, double y)
    {
        int best = 0; double distance = double.MaxValue;
        for (int i = 0; i < tokens.Count; i++) { double d = tokens[i].Rect.Distance(x, y); if (d < distance) { distance = d; best = i; if (d == 0) break; } }
        return best;
    }

    private void CommitSelection(int index, PageView view, int first, int last)
    {
        var tokens = view.Tokens!; var text = new StringBuilder(); var rects = new List<PdfRect>();
        int line = -1;
        for (int i = first; i <= last; i++)
        {
            var token = tokens[i];
            if (token.Line != line) { if (text.Length > 0) text.AppendLine(); rects.Add(token.Rect); line = token.Line; }
            else rects[^1] = rects[^1].Union(token.Rect);
            text.Append(token.Text);
        }
        Selection = string.IsNullOrWhiteSpace(text.ToString()) ? null : new(index, variant, view.SourceHash, text.ToString().Trim(), rects);
        for (int i = 0; i < views.Count; i++) DrawSelection(i);
        SelectionChanged?.Invoke(this);
    }

    internal void CopySelection()
    {
        if (Selection is null) return;
        var data = new DataPackage(); data.SetText(Selection.Text); Clipboard.SetContent(data);
    }

    private void DrawSelection(int index)
    {
        var canvas = views[index].SelectionLayer; canvas.Children.Clear();
        if (Selection is not { } selection || selection.Page != index) return;
        foreach (var rect in selection.Rects) AddRect(canvas, rect, index, new SolidColorBrush(global::Windows.UI.Color.FromArgb(85, 10, 132, 255)), false);
    }

    private void AddRect(Canvas canvas, PdfRect rect, int page, Brush fill, bool underline)
    {
        double scale = views[page].Paper.Width / sizes[page].Width;
        double x = rect.X * scale, y = rect.Y * scale, width = Math.Max(1, rect.Width * scale), height = Math.Max(1, rect.Height * scale);
        if (underline)
        {
            switch (sizes[page].Rotation)
            {
                case 90: width = 1.5; break;
                case 180: height = 1.5; break;
                case 270: x += width; width = 1.5; break;
                default: y += height; height = 1.5; break;
            }
        }
        var mark = new Rectangle { Width = width, Height = height, Fill = fill, IsHitTestVisible = false };
        Canvas.SetLeft(mark, x); Canvas.SetTop(mark, y); canvas.Children.Add(mark);
    }

    private void DrawMarkup(int index)
    {
        var canvas = views[index].Markup; canvas.Children.Clear();
        foreach (var item in annotationItems)
        {
            string kind = item?["kind"]?.GetValue<string>() ?? "", id = item?["id"]?.GetValue<string>() ?? "";
            var regions = item?["regions"] as JsonArray ?? [];
            if (kind is "highlight" or "underline")
                foreach (var region in regions.Where(r => r?["page"]?.GetValue<int>() == index))
                    AddRect(canvas, PdfRect.Read(region?["rect"]), index, new SolidColorBrush(kind == "underline" ? Colors.OrangeRed : global::Windows.UI.Color.FromArgb(95, 255, 214, 10)), kind == "underline");
            if (kind == "note" && item?["page"]?.GetValue<int>() == index)
            {
                var first = regions.FirstOrDefault(r => r?["page"]?.GetValue<int>() == index);
                var rect = first is null ? new PdfRect(18, 18, 20, 20) : PdfRect.Read(first["rect"]);
                double scale = views[index].Paper.Width / sizes[index].Width;
                var button = new Button { Content = "▤", Width = 22, Height = 22, MinHeight = 0, MinWidth = 0, Padding = new Thickness(0), FontSize = 14, Foreground = new SolidColorBrush(Colors.Black), Background = new SolidColorBrush(Colors.Gold), BorderThickness = new Thickness(0), CornerRadius = new CornerRadius(4) };
                ToolTipService.SetToolTip(button, item?["note"]?.GetValue<string>() ?? "编辑笔记");
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, "编辑第 " + (index + 1) + " 页笔记");
                button.Click += (_, _) => { Activated?.Invoke(this); AnnotationInvoked?.Invoke(this, id); };
                Canvas.SetLeft(button, Math.Clamp(rect.X * scale, 0, Math.Max(0, views[index].Paper.Width - 22)));
                Canvas.SetTop(button, Math.Clamp(rect.Y * scale, 0, Math.Max(0, views[index].Paper.Height - 22))); canvas.Children.Add(button);
            }
        }
    }
}
