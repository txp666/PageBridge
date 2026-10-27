using System.Text.Json.Nodes;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using global::Windows.Foundation;

namespace PageBridge.Windows;

internal sealed partial class StructuredReadingPane
{
    private static (FrameworkElement Content, bool Overflow) TranslatedContent(JsonNode block, double width, double height,
        double pageWidth, double pageHeight, ImageSource source, global::Windows.UI.Color background)
    {
        bool heading = Text(block, "kind") == "heading";
        double reference = heading ? 18 : 12;
        if (block["sourceFontSize"] is JsonValue value && value.TryGetValue<double>(out double sourceSize)
            && double.IsFinite(sourceSize) && sourceSize > 0)
            reference = sourceSize;
        var color = (background.R * .299 + background.G * .587 + background.B * .114) < 128 ? Colors.White : Colors.Black;
        color = ReadColor(block["foregroundColor"], color);
        var foreground = new SolidColorBrush(color);
        double minimum = Text(block, "sourceType") == "table_cell" ? 6.5 : 8;
        double initial = Math.Max(minimum, reference);
        var weight = heading ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal;
        if (block["inlineMath"] is not JsonArray { Count: > 0 } spans || Text(block, "translatedTemplate") is not { Length: > 0 } template)
        {
            var plain = new TextBlock { Text = Text(block, "translatedText"), TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.CharacterEllipsis,
                FontFamily = new FontFamily("Microsoft YaHei UI"), FontWeight = weight, Foreground = foreground,
                LineStackingStrategy = LineStackingStrategy.BlockLineHeight, IsHitTestVisible = false };
            bool overflow = !FitText(plain, width, height, initial, minimum);
            plain.Width = width; plain.Height = height; return (plain, overflow);
        }
        var rich = new RichTextBlock { TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.CharacterEllipsis, FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontWeight = weight, Foreground = foreground, IsTextSelectionEnabled = false, IsHitTestVisible = false };
        var paragraph = new Paragraph { Margin = new Thickness(0) }; rich.Blocks.Add(paragraph);
        var pictures = new List<(Viewbox View, double Width, double Height)>();
        foreach (var fragment in ReadingInlineMath.Fragments(template, spans))
        {
            if (fragment.Math is null) { paragraph.Inlines.Add(new Run { Text = fragment.Text }); continue; }
            if (fragment.Math["bbox"] is not JsonArray { Count: 4 } box)
            {
                paragraph.Inlines.Add(new Run { Text = "$" + Text(fragment.Math, "latex") + "$" }); continue;
            }
            // Parser bounds can meet glyph antialiasing or a formula's hat.
            // Expand only the rendered crop; the declared formula bbox stays intact.
            var bounds = ReadingInlineMath.Crop(box.Select(value => value!.GetValue<double>()).ToArray(), pageWidth, pageHeight);
            double left = bounds.Left, top = bounds.Top, cropWidth = bounds.Width, cropHeight = bounds.Height;
            if (cropWidth <= 0 || cropHeight <= 0) { paragraph.Inlines.Add(new Run { Text = "$" + Text(fragment.Math, "latex") + "$" }); continue; }
            var canvas = new Canvas { Width = cropWidth, Height = cropHeight, Clip = new RectangleGeometry { Rect = new Rect(0, 0, cropWidth, cropHeight) } };
            var original = new Image { Width = pageWidth, Height = pageHeight, Source = source, Stretch = Stretch.Fill };
            Canvas.SetLeft(original, -left); Canvas.SetTop(original, -top); canvas.Children.Add(original);
            var picture = new Viewbox { Child = canvas, Stretch = Stretch.Uniform, Width = cropWidth, Height = cropHeight };
            pictures.Add((picture, cropWidth, cropHeight));
            paragraph.Inlines.Add(new InlineUIContainer { Child = picture });
        }
        bool fits = FitContent(rich, width, height, initial, size =>
        {
            rich.FontSize = size; rich.LineHeight = size * 1.15;
            foreach (var picture in pictures) { picture.View.Width = picture.Width * size / reference; picture.View.Height = picture.Height * size / reference; }
        }, minimum);
        rich.Width = width; rich.Height = height;
        return (rich, !fits);
    }
}
