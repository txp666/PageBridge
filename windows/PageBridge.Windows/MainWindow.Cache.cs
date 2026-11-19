using System.Diagnostics;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private static readonly (string Key, string Title, string Detail, string? Action)[] CacheRows =
    [
        ("currentReadings", "当前阅读稿", "文档库正在使用的已保存译稿。", null),
        ("readingHistory", "历史阅读稿", "追加或重译时留下的旧版本。", "移到回收站"),
        ("taskFiles", "任务临时文件", "解析和排版过程中写入的临时文件。", "清理"),
        ("pendingReadings", "未提交的阅读稿", "已生成、尚待保存到文档库的译稿所在目录。", null),
        ("searchIndex", "全文搜索缓存", "用于加快下次搜索的原文提取结果。", "清理"),
        ("pagePreviews", "页面预览缓存", "阅读时生成的页面和缩略图。", "清理"),
        ("minerU", "MinerU 本地组件", "本机共用的识别模型和 Python 运行环境。", null),
        ("minerUDownloads", "MinerU 下载缓存", "安装组件时缓存的依赖包和模型副本。", "清理")
    ];

    private bool CacheCleanupAvailable => translationCancellation is null && !translationPreparing && exportCancellation is null;

    private async Task ShowCacheSettingsAsync()
    {
        string summary = "";
        while (!windowClosed)
        {
            var usage = await backend.CallAsync("cache_usage");
            string? requested = null;
            var panel = new StackPanel { Spacing = 10, Width = 480 };
            var notice = new TextBlock { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
            var dialog = new ContentDialog
            {
                XamlRoot = root.XamlRoot, Title = "缓存与存储",
                PrimaryButtonText = "刷新", CloseButtonText = "完成",
                Content = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto }
            };
            dialog.Resources["ContentDialogMaxWidth"] = 580d;
            var folder = new HyperlinkButton { Content = "打开存储目录", Padding = new Thickness(0) };
            folder.Click += async (_, _) => await OpenLocation(backend.DataRoot);
            panel.Children.Add(folder);
            foreach (var item in CacheRows)
            {
                JsonNode? size = usage?[item.Key];
                long bytes = size?["bytes"]?.GetValue<long>() ?? 0;
                string path = Text(size, "location");
                var row = new Grid { ColumnSpacing = 12 };
                row.ColumnDefinitions.Add(new() { Width = new GridLength(1, GridUnitType.Star) });
                row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
                row.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
                row.Children.Add(new StackPanel { Spacing = 3, Children =
                {
                    new TextBlock { Text = item.Title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold },
                    new TextBlock { Text = item.Detail, TextWrapping = TextWrapping.Wrap, FontSize = 11, Opacity = 0.7 }
                } });
                var count = new TextBlock { Text = item.Key == "minerU" && size is null ? "未安装" : CacheSize(bytes), VerticalAlignment = VerticalAlignment.Center };
                Grid.SetColumn(count, 1); row.Children.Add(count);
                var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, VerticalAlignment = VerticalAlignment.Center };
                var reveal = new Button { Content = "查看", IsEnabled = path != "" };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(reveal, $"查看{item.Title}");
                reveal.Click += async (_, _) => await OpenLocation(path);
                controls.Children.Add(reveal);
                if (item.Action is { } action)
                {
                    var clear = new Button { Content = action, IsEnabled = path != "" && CacheCleanupAvailable };
                    Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(clear, $"{action}{item.Title}");
                    clear.Click += (_, _) =>
                    {
                        if (!CacheCleanupAvailable) { notice.Text = "当前任务结束后可清理缓存。"; return; }
                        requested = item.Key;
                        dialog.Hide();
                    };
                    controls.Children.Add(clear);
                }
                Grid.SetColumn(controls, 2); row.Children.Add(controls);
                panel.Children.Add(row);
            }
            notice.Text = summary != "" ? summary : CacheCleanupAvailable ? "" : "当前任务结束后可清理缓存。";
            panel.Children.Add(notice);
            var result = await ShowDialogAsync(dialog);
            if (requested is null)
            {
                if (result == ContentDialogResult.Primary) continue;
                return;
            }
            var chosen = CacheRows.First(item => item.Key == requested);
            bool history = chosen.Key == "readingHistory";
            var confirm = new ContentDialog
            {
                XamlRoot = root.XamlRoot,
                Title = history ? "将历史阅读稿移到回收站？" : $"清理{chosen.Title}？",
                Content = history ? "历史版本可在回收站恢复。" : chosen.Detail + "清理后可按需重新生成。",
                PrimaryButtonText = chosen.Action,
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Close
            };
            if (await ShowDialogAsync(confirm) != ContentDialogResult.Primary || !CacheCleanupAvailable) continue;
            var cleanup = await backend.CallAsync("cache_clear", new JsonObject { ["category"] = requested });
            int removed = cleanup?["removed"]?.GetValue<int>() ?? 0, failed = cleanup?["failed"]?.GetValue<int>() ?? 0;
            summary = history ? $"已将 {removed} 项移到回收站。" : $"已清理 {removed} 项。";
            if (failed > 0) summary += $"{failed} 项未能处理。";

            async Task OpenLocation(string path)
            {
                try
                {
                    if (Directory.Exists(path)) Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
                    else await RevealPDFAsync(path);
                }
                catch (Exception error) { notice.Text = error.Message; }
            }
        }
    }

    private static string CacheSize(long bytes) => bytes switch
    {
        >= 1_000_000_000 => $"{bytes / 1_000_000_000d:0.0} GB",
        >= 1_000_000 => $"{bytes / 1_000_000d:0.0} MB",
        >= 1_000 => $"{bytes / 1_000d:0.0} KB",
        _ => $"{bytes} B"
    };
}
