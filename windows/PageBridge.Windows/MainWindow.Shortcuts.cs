using System.Diagnostics;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml.Input;
using global::Windows.System;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private bool shortcutsInitialized, updatingReaderZoom;

    private void InitializeShortcuts()
    {
        if (shortcutsInitialized) return;
        shortcutsInitialized = true;
        root.KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;
        AddShortcut(VirtualKey.O, VirtualKeyModifiers.Control, ImportAsync,
            () => translationCancellation is null && !translationPreparing);
        AddShortcut(VirtualKey.E, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift,
            () => ExportAsync("translated"), () => selected is not null && Text(selected, "translated_path") != "");
        AddShortcut((VirtualKey)188, VirtualKeyModifiers.Control, SettingsAsync);
        AddShortcut(VirtualKey.F, VirtualKeyModifiers.Control,
            () => { ShowDocumentSearch(); return Task.CompletedTask; }, () => selected is not null && settingsOverlay is null);

        bool ReaderAvailable() => selected is not null && settingsOverlay is null;
        Task ZoomBy(double change) { SetReaderZoom(magnification + change); return Task.CompletedTask; }
        AddShortcut((VirtualKey)187, VirtualKeyModifiers.Control, () => ZoomBy(.25), ReaderAvailable);
        AddShortcut((VirtualKey)187, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift, () => ZoomBy(.25), ReaderAvailable);
        AddShortcut(VirtualKey.Add, VirtualKeyModifiers.Control, () => ZoomBy(.25), ReaderAvailable);
        AddShortcut((VirtualKey)189, VirtualKeyModifiers.Control, () => ZoomBy(-.25), ReaderAvailable);
        AddShortcut(VirtualKey.Subtract, VirtualKeyModifiers.Control, () => ZoomBy(-.25), ReaderAvailable);
    }

    private void AddShortcut(VirtualKey key, VirtualKeyModifiers modifiers, Func<Task> action, Func<bool>? available = null)
    {
        // Native accelerators match the complete modifier chord. Plain typing,
        // text selection shortcuts and editor Return/Escape remain with controls.
        var shortcut = new KeyboardAccelerator { Key = key, Modifiers = modifiers };
        shortcut.Invoked += async (_, args) =>
        {
            if (windowClosed || dialogs.CurrentCount == 0 || available?.Invoke() == false) return;
            args.Handled = true;
            await Attempt(action);
        };
        root.KeyboardAccelerators.Add(shortcut);
    }

    private void SetReaderZoom(double scale)
    {
        if (updatingReaderZoom || !double.IsFinite(scale)) return;
        scale = Math.Clamp(scale, .5, 3);
        if (Math.Abs(magnification - scale) < .001) return;
        updatingReaderZoom = true;
        try
        {
            magnification = scale;
            originalPages.SetZoom(scale); translatedPages.SetZoom(scale);
            if (scale == 1 && zoom.SelectedItem?.ToString() == "适合宽度") return;
            string label = Math.Round(scale * 100).ToString(CultureInfo.InvariantCulture) + "%";
            int index = -1;
            for (int i = 0; i < zoom.Items.Count; i++)
                if (string.Equals(zoom.Items[i]?.ToString(), label, StringComparison.Ordinal)) { index = i; break; }
            if (index < 0) { zoom.Items.Add(label); index = zoom.Items.Count - 1; }
            zoom.SelectedIndex = index;
        }
        finally { updatingReaderZoom = false; }
    }

    private Task RevealDocumentAsync() => RevealDocumentAsync(selected);
    private Task RevealDocumentAsync(JsonObject? document) => RevealPDFAsync(Text(document, "input_path"));
    private Task RevealOutputAsync()
    {
        string path = Text(selected, "translated_path");
        if (path.Length == 0) path = Text(selected, "bilingual_path");
        return RevealPDFAsync(path);
    }

    private static Task RevealPDFAsync(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path) || !File.Exists(path))
            throw new InvalidOperationException("文件不存在，请确认文档仍在本机，或先完成翻译。");
        string fullPath = Path.GetFullPath(path);
        // Windows file paths cannot contain a quote. Reject one explicitly so
        // the Explorer command always receives exactly one existing file path.
        if (fullPath.Contains('"')) throw new InvalidOperationException("文件路径无效。");
        Process.Start(new ProcessStartInfo("explorer.exe")
        {
            Arguments = "/select,\"" + fullPath + "\"",
            UseShellExecute = true
        });
        return Task.CompletedTask;
    }
}
