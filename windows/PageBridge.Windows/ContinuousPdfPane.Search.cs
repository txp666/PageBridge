using System.Text.Json.Nodes;
using Microsoft.UI.Xaml.Media;

namespace PageBridge.Windows;

internal sealed partial class ContinuousPdfPane
{
    private JsonArray searchHits = [];
    internal void SetSearchHits(JsonArray hits)
    {
        searchHits = (JsonArray)hits.DeepClone();
        for (int i = 0; i < views.Count; i++) DrawSearch(i);
    }
    private void DrawSearch(int index)
    {
        var layer = views[index].SearchLayer; layer.Children.Clear();
        foreach (var hit in searchHits.Where(h => h?["page"]?.GetValue<int>() == index))
            foreach (var rect in hit?["rects"]?.AsArray() ?? [])
                AddRect(layer, PdfRect.Read(rect), index, new SolidColorBrush(global::Windows.UI.Color.FromArgb(105, 255, 194, 0)), false);
    }
    internal void RevealSearchHit(JsonNode hit)
    {
        int page = hit["page"]?.GetValue<int>() ?? 0;
        if (page < 0 || page >= sizes.Count) return;
        var rect = PdfRect.Read(hit["rects"]?.AsArray().FirstOrDefault());
        MoveTo(new Anchor(page, Math.Max(0, rect.Y / sizes[page].Height - .12)));
    }
}
