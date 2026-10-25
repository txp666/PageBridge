using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Automation;
using global::Windows.System;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private readonly Grid documentSearchBar = new() { Visibility = Visibility.Collapsed, Padding = new Thickness(19, 7, 19, 7), ColumnSpacing = 10 };
    private readonly TextBox documentQuery = new() { Width = 320, MaxLength = 200, Height = 30, MinHeight = 0, FontSize = 12, Padding = new Thickness(8, 4, 8, 4) };
    private readonly Button searchResultsButton = new() { Content = "结果 0", IsEnabled = false, MinHeight = 0, Height = 28, FontSize = 12 };
    private readonly TextBlock searchProgress = new() { FontSize = 11, Opacity = .65, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
    private readonly StackPanel searchResultItems = new() { Spacing = 1 };
    private readonly Flyout searchResultsFlyout = new();
    private CancellationTokenSource? documentSearchCancellation;
    private int documentSearchGeneration;

    private void InitializeDocumentSearch()
    {
        foreach (var child in workspace.Children.OfType<FrameworkElement>()) if (Grid.GetRow(child) >= 2) Grid.SetRow(child, Grid.GetRow(child) + 1);
        workspace.RowDefinitions.Insert(2, new() { Height = GridLength.Auto });
        foreach (var width in new[] { GridLength.Auto, GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star), GridLength.Auto }) documentSearchBar.ColumnDefinitions.Add(new() { Width = width });
        AutomationProperties.SetAutomationId(documentQuery, "DocumentSearchQuery");
        AutomationProperties.SetName(documentQuery, "文内搜索词"); documentSearchBar.Children.Add(documentQuery);
        var run = ActionButton("搜索", SearchDocumentAsync); Grid.SetColumn(run, 1); documentSearchBar.Children.Add(run);
        AutomationProperties.SetAutomationId(run, "DocumentSearchRun");
        searchResultsFlyout.Content = new ScrollViewer { Width = 360, MaxHeight = 340, Content = searchResultItems, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        searchResultsButton.Flyout = searchResultsFlyout; Grid.SetColumn(searchResultsButton, 2); documentSearchBar.Children.Add(searchResultsButton);
        AutomationProperties.SetAutomationId(searchResultsButton, "DocumentSearchResults");
        Grid.SetColumn(searchProgress, 3); documentSearchBar.Children.Add(searchProgress);
        var close = Tool("close", null, "关闭搜索", () => { ResetDocumentSearch(); documentSearchBar.Visibility = Visibility.Collapsed; return Task.CompletedTask; }); Grid.SetColumn(close, 4); documentSearchBar.Children.Add(close);
        Grid.SetRow(documentSearchBar, 2); workspace.Children.Add(documentSearchBar); themedBars.Add(documentSearchBar);
        documentQuery.KeyDown += async (_, e) => { if (e.Key == VirtualKey.Enter) { e.Handled = true; await Attempt(SearchDocumentAsync); } else if (e.Key == VirtualKey.Escape) { ResetDocumentSearch(); documentSearchBar.Visibility = Visibility.Collapsed; e.Handled = true; } };
        documentQuery.TextChanged += (_, _) => ResetDocumentSearch();
        foreach (var pane in new[] { originalPages, translatedPages }) pane.LinkInvoked += async (source, link) => await Attempt(async () =>
        {
            ActivateVariant(source.Variant);
            if (Text(link, "kind") == "page") { NavigateReading(source.Variant, link["page"]!.GetValue<int>()); return; }
            if (!Uri.TryCreate(Text(link, "url"), UriKind.Absolute, out var uri) || uri.Scheme is not ("https" or "http" or "mailto") || uri.UserInfo.Length > 0) return;
            if (!await Launcher.LaunchUriAsync(uri)) throw new InvalidOperationException("无法打开此链接，请检查默认浏览器。");
        });
    }

    private void ShowDocumentSearch()
    {
        if (selected is null) return;
        documentSearchBar.Visibility = Visibility.Visible;
        documentQuery.PlaceholderText = mode.SelectedIndex == 2 ? "搜索译文中的文字" : "搜索原文中的文字";
        documentQuery.Focus(FocusState.Programmatic); documentQuery.SelectAll();
    }

    private void ResetDocumentSearch()
    {
        documentSearchGeneration++; documentSearchCancellation?.Cancel(); documentSearchCancellation = null;
        searchResultsFlyout.Hide(); searchResultItems.Children.Clear(); searchResultsButton.Content = "结果 0"; searchResultsButton.IsEnabled = false; searchProgress.Text = "";
        originalPages?.SetSearchHits([]); translatedPages?.SetSearchHits([]);
        documentQuery.PlaceholderText = mode.SelectedIndex == 2 ? "搜索译文中的文字" : "搜索原文中的文字";
    }

    private async Task SearchDocumentAsync()
    {
        ResetDocumentSearch();
        string query = documentQuery.Text.Trim(), id = Text(selected, "id"), variant = mode.SelectedIndex == 2 ? "translated" : "original";
        if (id == "" || query == "") return;
        int generation = documentSearchGeneration, scanned = 0;
        using var cancellation = new CancellationTokenSource(); documentSearchCancellation = cancellation;
        var hits = new JsonArray(); var pane = variant == "translated" ? translatedPages : originalPages;
        string hash = ""; bool limited = false; int rectangleCount = 0;
        try
        {
            while (true)
            {
                searchProgress.Text = "正在搜索 · 已检查 " + scanned + " 页";
                var data = await backend.CallAsync("search_document", new JsonObject { ["document_id"] = id, ["variant"] = variant, ["query"] = query, ["start_page"] = scanned }, cancellation.Token);
                if (generation != documentSearchGeneration) return;
                var nextHash = Text(data, "source_sha256");
                if (hash != "" && hash != nextHash) throw new InvalidOperationException("PDF 内容已变化，请重新搜索。"); hash = nextHash;
                foreach (var hit in data?["hits"]?.AsArray() ?? [])
                {
                    int count = hit?["rects"]?.AsArray().Count ?? 0;
                    if (rectangleCount + count > 5000) { limited = true; break; }
                    hits.Add(hit?.DeepClone()); rectangleCount += count;
                }
                limited |= data?["truncated"]?.GetValue<bool>() ?? false;
                pane.SetSearchHits(hits);
                if (limited || hits.Count >= 2000) { limited = true; break; }
                if (data?["next_page"] is not JsonNode next) break;
                scanned = next.GetValue<int>();
            }
            foreach (var hit in hits)
            {
                if (hit is null) continue;
                var content = new StackPanel { Spacing = 5 };
                content.Children.Add(new TextBlock { Text = "第 " + (hit["page"]!.GetValue<int>() + 1) + " 页", FontSize = 12, Foreground = Blue });
                content.Children.Add(new TextBlock { Text = Text(hit, "text"), FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxLines = 3 });
                var button = new Button { Content = content, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(12), Background = Clear, BorderThickness = new Thickness(0) };
                AutomationProperties.SetName(button, "搜索结果第 " + (hit["page"]!.GetValue<int>() + 1) + " 页");
                button.Click += (_, _) => { NavigateReading(variant, hit["page"]!.GetValue<int>()); pane.RevealSearchHit(hit); searchResultsFlyout.Hide(); };
                searchResultItems.Children.Add(button);
            }
            searchResultsButton.Content = "结果 " + hits.Count; searchResultsButton.IsEnabled = hits.Count > 0;
            searchProgress.Text = hits.Count == 0 ? "未找到匹配文字" : limited ? "结果较多，已显示部分命中" : "共 " + hits.Count + " 页匹配";
            if (hits.Count > 0) searchResultsFlyout.ShowAt(searchResultsButton);
        }
        catch (OperationCanceledException) { }
        catch { if (generation != documentSearchGeneration || windowClosed) return; searchProgress.Text = "搜索失败，请重试"; throw; }
        finally { if (ReferenceEquals(documentSearchCancellation, cancellation)) documentSearchCancellation = null; }
    }
}
