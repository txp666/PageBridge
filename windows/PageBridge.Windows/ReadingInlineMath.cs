using System.Text.Json.Nodes;

namespace PageBridge.Windows;

internal static class ReadingInlineMath
{
    internal readonly record struct Fragment(string Text, JsonNode? Math);

    internal static (double Left, double Top, double Width, double Height) Crop(IReadOnlyList<double> bbox, double pageWidth, double pageHeight)
    {
        double left = Math.Max(0, bbox[0] - .75), top = Math.Max(0, bbox[1] - .75);
        return (left, top, Math.Min(pageWidth, bbox[2] + .75) - left, Math.Min(pageHeight, bbox[3] + .75) - top);
    }

    // Tokens are supplied by the parser, never inferred from document prose.
    internal static IEnumerable<Fragment> Fragments(string template, JsonArray spans)
    {
        int cursor = 0;
        while (cursor < template.Length)
        {
            int position = template.Length; string token = ""; JsonNode? selected = null;
            foreach (var span in spans)
            {
                string candidate = span?["token"]?.GetValue<string>() ?? "";
                if (candidate.Length == 0) continue;
                int found = template.IndexOf(candidate, cursor, StringComparison.Ordinal);
                if (found >= 0 && found < position) { position = found; token = candidate; selected = span; }
            }
            if (position > cursor) yield return new Fragment(template[cursor..position], null);
            if (selected is null) yield break;
            yield return new Fragment("", selected);
            cursor = position + token.Length;
        }
    }
}
