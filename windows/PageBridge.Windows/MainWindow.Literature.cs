using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private bool literatureOpening;
    private async Task LiteratureSearchAsync()
    {
        if (literatureOpening) return;
        literatureOpening = true;
        string target = (filter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        string? importedID = null;
        LiteratureSearchPanel? panel = null;
        var dialog = new ContentDialog { XamlRoot = root.XamlRoot, Padding = new Thickness(0) };
        dialog.Resources["ContentDialogMaxWidth"] = 840d;
        dialog.Resources["ContentDialogPadding"] = new Thickness(0);
        try
        {
            panel = new LiteratureSearchPanel(backend, id => { importedID = id; dialog.Hide(); return Task.CompletedTask; }, () => dialog.Hide())
            { Width = Math.Min(800, Math.Max(500, root.ActualWidth - 90)), Height = Math.Min(650, Math.Max(430, root.ActualHeight - 90)) };
            dialog.Content = panel;
            dialog.Opened += async (_, _) => await panel.InitializeAsync();
            dialog.Closing += (_, _) => panel.Cancel();
            await ShowDialogAsync(dialog);
        }
        finally { panel?.Dispose(); literatureOpening = false; }
        if (importedID is not null && !windowClosed) await FinishImportAsync(importedID, target);
    }

    private async Task ImportPathsAsync(IEnumerable<string> paths)
    {
        string target = (filter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        string? id = null;
        foreach (var path in paths)
        {
            status.Text = "正在导入…";
            id = Text(await backend.CallAsync("import", new JsonObject { ["path"] = path }), "id");
            await PlaceImportedAsync(id, target);
        }
        if (id is not null) await FinishImportAsync(id, target, alreadyPlaced: true);
    }
    private async Task PlaceImportedAsync(string id, string target)
    {
        // Deduplication reopens the existing record. Its folder, translations
        // and reading notes must not be changed by another import location.
        if (documents.Any(document => Text(document, "id") == id)) return;
        if (folders.Any(folder => Text(folder, "id") == target))
            await backend.CallAsync("move", new JsonObject { ["document_id"] = id, ["folder_id"] = target });
    }
    private async Task FinishImportAsync(string id, string target, bool alreadyPlaced = false)
    {
        bool existing = documents.Any(document => Text(document, "id") == id);
        if (existing) target = "all";
        if (!alreadyPlaced) await PlaceImportedAsync(id, target);
        if (target is "favorites" or "trash") target = "unfiled";
        loading = true;
        try { search.Text = ""; filter.SelectedItem = filter.Items.OfType<ComboBoxItem>().FirstOrDefault(i => (string)i.Tag == target) ?? filter.Items[0]; }
        finally { loading = false; }
        await ReloadAsync(id); status.Text = existing ? "已打开文档库中已有的文档" : "已导入文档库";
    }
}
