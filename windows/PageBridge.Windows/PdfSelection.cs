using System.Text.Json.Nodes;

namespace PageBridge.Windows;

internal readonly record struct PdfRect(double X, double Y, double Width, double Height)
{
    internal static PdfRect Read(JsonNode? value) => new(
        value?["x"]?.GetValue<double>() ?? 0, value?["y"]?.GetValue<double>() ?? 0,
        value?["width"]?.GetValue<double>() ?? 0, value?["height"]?.GetValue<double>() ?? 0);
    internal JsonObject ToJson() => new() { ["x"] = X, ["y"] = Y, ["width"] = Width, ["height"] = Height };
    internal bool Contains(double x, double y) => x >= X && x <= X + Width && y >= Y && y <= Y + Height;
    internal double Distance(double x, double y) => Math.Pow(Math.Max(0, Math.Max(X - x, x - X - Width)), 2) + Math.Pow(Math.Max(0, Math.Max(Y - y, y - Y - Height)), 2);
    internal PdfRect Union(PdfRect other) => new(Math.Min(X, other.X), Math.Min(Y, other.Y), Math.Max(X + Width, other.X + other.Width) - Math.Min(X, other.X), Math.Max(Y + Height, other.Y + other.Height) - Math.Min(Y, other.Y));
}

internal sealed record PdfSelection(int Page, string Variant, string SourceHash, string Text, IReadOnlyList<PdfRect> Rects)
{
    internal JsonArray Regions => new(Rects.Select(r => (JsonNode)new JsonObject { ["page"] = Page, ["rect"] = r.ToJson() }).ToArray());
}
