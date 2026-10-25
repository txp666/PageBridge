using System.Text.Json.Nodes;
using Microsoft.UI.Xaml.Controls;

namespace PageBridge.Windows;

public sealed partial class MainWindow
{
    private MenuFlyout FolderMenu(JsonObject folder)
    {
        string id = Text(folder, "id"), name = Text(folder, "name");
        var menu = new MenuFlyout();
        menu.Items.Add(MenuItem("重命名文件夹", async () =>
        {
            if (await PromptAsync("重命名文件夹", name) is not { Length: > 0 } updated) return;
            await backend.CallAsync("rename_folder", new JsonObject { ["folder_id"] = id, ["name"] = updated });
            await ReloadAsync();
        }));
        menu.Items.Add(MenuItem("删除文件夹…", async () =>
        {
            var dialog = new ContentDialog
            {
                XamlRoot = root.XamlRoot,
                Title = "删除文件夹？",
                Content = $"文件夹“{name}”内的文档会回到“未分类”，原文、译文和批注均会保留。",
                PrimaryButtonText = "删除文件夹", CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Close
            };
            if (await ShowDialogAsync(dialog) != ContentDialogResult.Primary) return;
            bool wasSelected = (filter.SelectedItem as ComboBoxItem)?.Tag as string == id;
            await backend.CallAsync("delete_folder", new JsonObject { ["folder_id"] = id });
            // Change the selected metadata filter before reloading so the
            // existing selection is reconciled against the unfiled documents.
            if (wasSelected)
            {
                loading = true;
                try { filter.SelectedItem = filter.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag as string == "unfiled"); }
                finally { loading = false; }
            }
            await ReloadAsync();
            await ReconcileSelectionAsync();
        }));
        return menu;
    }
}
