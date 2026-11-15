using System.Text.Json;
using System.Text.Json.Nodes;

namespace PageBridge.Windows;

internal static class UpdatePreferences
{
    private static string PathFor(string root) => Path.Combine(root, "update-preferences.json");

    internal static bool AutomaticallyCheck(string root)
    {
        try { return JsonNode.Parse(File.ReadAllText(PathFor(root)))?["automaticChecks"]?.GetValue<bool>() ?? true; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException)
        { return true; }
    }

    internal static void SetAutomaticallyCheck(string root, bool enabled)
    {
        Directory.CreateDirectory(root);
        string path = PathFor(root), temporary = path + ".tmp";
        File.WriteAllText(temporary, new JsonObject { ["automaticChecks"] = enabled }.ToJsonString());
        File.Move(temporary, path, overwrite: true);
    }
}
