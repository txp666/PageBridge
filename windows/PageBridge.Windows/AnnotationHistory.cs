using System.Text.Json.Nodes;

namespace PageBridge.Windows;

/// <summary>One saved annotation change. Before/After are complete annotations, null where none existed.</summary>
internal sealed record AnnotationStep(JsonObject? Before, JsonObject? After)
{
    internal string Label => Before is null ? "添加" + Kind(After) : After is null ? "删除" + Kind(Before) : "编辑笔记";
    private static string Kind(JsonObject? item) => item?["kind"]?.GetValue<string>() switch { "highlight" => "高亮", "underline" => "下划线", "bookmark" => "书签", _ => "笔记" };

    /// <summary>The bridge call that turns <paramref name="from"/> into <paramref name="to"/>.</summary>
    internal static (string Action, JsonObject Args) Request(string documentId, string hash, JsonObject? from, JsonObject? to)
    {
        var args = new JsonObject { ["document_id"] = documentId, ["source_sha256"] = hash };
        if (to is null) { args["annotation_id"] = from?["id"]?.GetValue<string>() ?? ""; return ("delete_annotation", args); }
        // A deleted annotation comes back with its own id and timestamps.
        args["annotation"] = to.DeepClone(); if (from is null) args["restore"] = true;
        return ("save_annotation", args);
    }
}

/// <summary>Undo and redo of annotation changes for one document and PDF revision.</summary>
internal sealed class AnnotationHistory
{
    private const int Limit = 100;
    private readonly List<AnnotationStep> undo = [], redo = [];
    private string documentId = "", sourceHash = "";
    private int version;

    /// <summary>Steps survive a reload of the same revision only.</summary>
    internal void Bind(string id, string hash)
    {
        if (id == documentId && hash == sourceHash) return;
        documentId = id; sourceHash = hash; Clear();
    }

    internal void Clear() { undo.Clear(); redo.Clear(); version++; }

    internal void Record(string id, string hash, AnnotationStep step)
    {
        Bind(id, hash); undo.Add(step); redo.Clear(); version++;
        if (undo.Count > Limit) undo.RemoveAt(0);
    }

    /// <summary>Removes the next step; <see cref="Complete"/> moves it to the other stack once the bridge applied it.</summary>
    internal (AnnotationStep Step, int Version)? Take(bool redoing, string id, string hash)
    {
        var stack = redoing ? redo : undo;
        if (id != documentId || hash != sourceHash || stack.Count == 0) return null;
        var step = stack[^1]; stack.RemoveAt(stack.Count - 1);
        return (step, ++version);
    }

    internal void Complete(bool redoing, AnnotationStep step, int taken)
    {
        // A change recorded meanwhile, or a document switch, makes the step stale.
        if (taken == version) (redoing ? undo : redo).Add(step);
    }
}
