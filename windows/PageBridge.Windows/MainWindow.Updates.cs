using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private bool updatesDialogOpen, updateInstallPreparing;
    private CancellationTokenSource? updatesPolling;

    // The updater itself is one Attempt; another active operation could still
    // be importing, saving or displaying a native picker behind this dialog.
    private bool UpdateInstallBusy => translationCancellation is not null || translationPreparing
        || assistantPanel.IsBusy || annotationsPanel.IsBusy || loading || bindingPages
        || settingsOpening || literatureOpening || activeUiOperations > 1
        || settingsOverlay is ServiceSettingsView { IsBusy: true };

    private async Task ShowUpdatesAsync()
    {
        if (updatesDialogOpen || updateInstallPreparing || windowClosed) return;
        updatesDialogOpen = true;
        using var polling = new CancellationTokenSource(); updatesPolling = polling;
        var current = new TextBlock { Text = $"当前版本 {AppVersion.Display}（{AppVersion.BuildNumber}）", FontSize = 12, Opacity = .65 };
        var heading = new TextBlock { Text = "正在检查更新…", FontSize = 16, TextWrapping = TextWrapping.Wrap };
        var details = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        var bar = new ProgressBar { Minimum = 0, Maximum = 100, IsIndeterminate = true, Visibility = Visibility.Collapsed };
        var notes = new HyperlinkButton { Content = "查看更新说明", Padding = new Thickness(0), Visibility = Visibility.Collapsed };
        var body = new StackPanel { Spacing = 12, Width = Math.Max(250, Math.Min(440, root.ActualWidth - 100)) };
        body.Children.Add(current); body.Children.Add(heading); body.Children.Add(bar); body.Children.Add(details); body.Children.Add(notes);
        body.Children.Add(new TextBlock { Text = "下载和安装均由你确认。更新包会校验发布签名与 SHA-256；安装器目前没有 Windows 发行者签名。", FontSize = 11, Opacity = .65, TextWrapping = TextWrapping.Wrap });
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Title = "PageBridge 更新", Content = body, CloseButtonText = "关闭", DefaultButton = ContentDialogButton.None };
        AutomationProperties.SetAutomationId(dialog, "UpdatesDialog");
        AutomationProperties.SetAutomationId(heading, "UpdateState"); AutomationProperties.SetAutomationId(details, "UpdateDetails");
        AutomationProperties.SetAutomationId(bar, "UpdateProgress"); AutomationProperties.SetAutomationId(notes, "UpdateReleaseNotes");
        JsonNode? snapshot = null;
        string state = "checking";
        bool requestPending = false, cancelPending = false, installRequested = false;
        string? commandError = null;
        Task? pollTask = null;
        bool Active() => state is "checking" or "downloading";
        void Render()
        {
            if (windowClosed || polling.IsCancellationRequested) return;
            state = Text(snapshot, "state");
            if (state.Length == 0) state = "checking";
            long received = snapshot?["received"]?.GetValue<long>() ?? 0, total = snapshot?["total"]?.GetValue<long>() ?? 0;
            string version = Text(snapshot?["update"], "version");
            int build = snapshot?["update"]?["build"]?.GetValue<int>() ?? 0;
            string size = total > 0 ? $"{received / 1048576d:N1} / {total / 1048576d:N1} MB" : $"{received / 1048576d:N1} MB";
            heading.Text = state switch
            {
                "checking" => "正在检查更新…", "available" => $"发现新版本 {version}（{build}）",
                "downloading" => cancelPending ? "正在取消下载…" : "正在下载更新…", "ready" => $"{version}（{build}）已下载",
                "current" => "当前已是最新版本", "cancelled" => "已取消", "unsupported" => "需要更新 Windows",
                "error" => "暂时无法获取更新", _ => "检查 PageBridge 更新"
            };
            details.Text = !Active() && commandError is not null ? commandError : state switch
            {
                "checking" => cancelPending ? "正在取消检查，请稍候。" : "从官方发布页获取更新信息。",
                "available" => $"安装包大小 {((snapshot?["update"]?["bytes"]?.GetValue<long>() ?? 0) / 1048576d):N1} MB。点击下载后开始传输。",
                "downloading" => size, "current" => $"当前版本 {AppVersion.Display}（{AppVersion.BuildNumber}）。",
                "ready" => UpdateInstallBusy ? "请先结束翻译、助手回答或其他正在处理的操作，再安装更新。" : "安装前将再次校验文件。确认后退出 PageBridge，并打开安装向导；文档与设置会保留。",
                "cancelled" => "当前应用未作更改，可重新检查。",
                "unsupported" => $"此版本需要 Windows build {snapshot?["update"]?["minWindowsBuild"]} 或更高版本。请先更新系统。",
                "error" => Text(snapshot, "error") is { Length: > 0 } error ? error : "请检查网络后重试。", _ => "检查是否有适用于 Windows 的新版本。"
            };
            bar.Visibility = Active() ? Visibility.Visible : Visibility.Collapsed;
            bar.IsIndeterminate = state == "checking" || total <= 0;
            if (total > 0) bar.Value = Math.Clamp(received * 100d / total, 0, 100);
            dialog.PrimaryButtonText = state switch { "available" => "下载更新", "ready" => "安装并退出…", "checking" or "downloading" => "", _ => "重新检查" };
            dialog.IsPrimaryButtonEnabled = !requestPending && !Active() && (state != "ready" || !UpdateInstallBusy);
            dialog.SecondaryButtonText = Active() ? (state == "downloading" ? "取消下载" : "取消检查") : "";
            dialog.IsSecondaryButtonEnabled = Active() && !requestPending && !cancelPending;
            notes.Visibility = Visibility.Collapsed;
            if (Uri.TryCreate(Text(snapshot?["update"], "releaseNotesURL"), UriKind.Absolute, out var url)
                && url.Scheme == "https" && url.Host == "github.com" && url.AbsolutePath.StartsWith("/txp666/PageBridge/releases/", StringComparison.Ordinal))
            { notes.NavigateUri = url; notes.Visibility = Visibility.Visible; }
        }
        void ReportError(Exception error)
        {
            // A failed call leaves the backend state unchanged, so the next poll
            // must not erase the message. Keep it until the next command or
            // until a poll shows a new operation under way.
            commandError = error.Message;
            snapshot ??= new JsonObject { ["state"] = "error" };
            cancelPending = false; Render();
        }
        async Task CommandAsync(string action)
        {
            if (requestPending || polling.IsCancellationRequested) return;
            requestPending = true; commandError = null; Render();
            try { snapshot = await backend.CallAsync(action); }
            catch (Exception error) { if (!windowClosed) ReportError(error); }
            finally { requestPending = false; Render(); }
        }
        async Task PollAsync()
        {
            try
            {
                while (!polling.IsCancellationRequested)
                {
                    await Task.Delay(500, polling.Token);
                    if (!requestPending)
                    {
                        try
                        {
                            snapshot = await backend.CallAsync("update_status");
                            if (Text(snapshot, "state") is "checking" or "downloading") commandError = null;
                            else cancelPending = false;
                            Render();
                        }
                        catch (Exception error) { if (!windowClosed && !polling.IsCancellationRequested) ReportError(error); }
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception error) { if (!windowClosed && !polling.IsCancellationRequested) ReportError(error); }
        }
        dialog.Opened += async (_, _) =>
        {
            await CommandAsync("update_status");
            if (state is "idle" or "error" or "cancelled" or "current") await CommandAsync("update_check");
            pollTask = PollAsync();
        };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            if (state == "ready") { args.Cancel = UpdateInstallBusy || requestPending; installRequested = !args.Cancel; return; }
            args.Cancel = true; var deferral = args.GetDeferral();
            try { cancelPending = false; await CommandAsync(state == "available" ? "update_download" : "update_check"); }
            finally { deferral.Complete(); }
        };
        dialog.SecondaryButtonClick += async (_, args) =>
        {
            args.Cancel = true; var deferral = args.GetDeferral();
            try { cancelPending = true; await CommandAsync("update_cancel"); }
            finally { deferral.Complete(); }
        };
        try { await ShowDialogAsync(dialog); }
        finally
        {
            polling.Cancel();
            if (pollTask is not null) await pollTask;
            // Closing a progress dialog is also a cancellation request. Do not
            // kill its persistent reader process or silently keep downloading.
            // A command may still be queued while its previous snapshot is
            // idle/available. Always cancel; idle and completed states are no-op.
            if (!windowClosed) { try { await backend.CallAsync("update_cancel"); } catch { } }
            if (ReferenceEquals(updatesPolling, polling)) updatesPolling = null;
            updatesDialogOpen = false;
        }
        if (installRequested && !windowClosed) await InstallUpdateAsync();
    }

    private async Task InstallUpdateAsync()
    {
        if (UpdateInstallBusy) throw new InvalidOperationException("请先结束翻译、助手回答或其他正在处理的操作，再安装更新。");
        if (!await CloseSettingsAsync()) return; // Existing unsaved-settings confirmation.
        string message = "将退出 PageBridge 并打开新版安装向导。文档、批注与设置会保留。";
        if (assistantPanel.HasDraft) message += "\n\n助手输入框还有未发送的问题；继续会放弃这份草稿。";
        var confirm = new ContentDialog { XamlRoot = root.XamlRoot, Title = "安装更新？", Content = message, PrimaryButtonText = "安装并退出", CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
        AutomationProperties.SetAutomationId(confirm, "UpdateInstallConfirmation");
        if (await ShowDialogAsync(confirm) != ContentDialogResult.Primary) return;
        if (UpdateInstallBusy || windowClosed) throw new InvalidOperationException("当前仍有任务在处理，请稍后重试安装。");
        updateInstallPreparing = true; interactionHost.IsEnabled = false; readingTimer?.Stop();
        status.Text = "正在保存阅读位置并校验更新包…";
        try
        {
            await PersistReadingAsync();
            if (pendingReading.Count != 0 || savingReading) throw new InvalidOperationException("阅读位置未保存成功，已暂停安装。请稍后重试。");
            var prepared = await backend.CallAsync("update_prepare_install");
            long expectedBytes = prepared?["bytes"]?.GetValue<long>() ?? 0;
            string expectedHash = Text(prepared, "sha256");
            if (expectedBytes <= 0 || expectedBytes > 2L * 1024 * 1024 * 1024 || expectedHash.Length != 64 || !expectedHash.All(Uri.IsHexDigit)
                || (prepared?["build"]?.GetValue<int>() ?? 0) <= AppVersion.BuildNumber)
                throw new InvalidOperationException("更新包校验信息无效，已停止安装。");
            // Python reports a fully resolved path, but the data root may sit
            // behind a junction, subst or mapped drive. Accept only the expected
            // file name and rebuild the path under the configured root.
            string cache = Path.Combine(backend.DataRoot, "updates");
            string name = Path.GetFileName(Text(prepared, "path"));
            if (!string.Equals(name, $"PageBridge-{Text(prepared, "version")}-{expectedHash[..16]}-setup.exe", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("更新安装包路径无效。");
            string path = Path.Combine(cache, name);
            // Reject junctions/symlinks inside the cache. Opening without write/delete
            // sharing also refuses pre-existing writable handles to the EXE.
            foreach (string candidate in new[] { cache, path })
                if ((File.GetAttributes(candidate) & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("更新缓存包含链接，请重新下载。");
            using var locked = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 131072, FileOptions.Asynchronous | FileOptions.SequentialScan);
            if (locked.Length != expectedBytes) throw new InvalidOperationException("更新包长度已变化，请重新下载。");
            byte[] digest = await SHA256.HashDataAsync(locked);
            if (!CryptographicOperations.FixedTimeEquals(digest, Convert.FromHexString(expectedHash)))
                throw new InvalidOperationException("更新包内容已变化，请重新下载。");
            if (windowClosed || UpdateInstallBusy) throw new InvalidOperationException("检测到新的任务，已暂停安装。");
            // Do not launch through Python: its Windows Job would terminate the
            // installer when PageBridge exits. No silent installation switches.
            using var installer = Process.Start(new ProcessStartInfo(path) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(path)! });
            if (installer is null) throw new InvalidOperationException("安装器未能启动，当前应用保持打开。");
            allowClose = true; Close();
        }
        finally
        {
            updateInstallPreparing = false;
            if (!windowClosed) { interactionHost.IsEnabled = true; if (pendingReading.Count > 0) readingTimer?.Start(); }
        }
    }

    private void DisposeUpdates() => updatesPolling?.Cancel();
}
