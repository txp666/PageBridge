using System.Text.Json.Nodes;
using PageBridge.Windows;

static void Equal(double actual, double expected, string detail)
{
    if (Math.Abs(actual - expected) > 1e-9) throw new Exception($"{detail}: expected {expected}, got {actual}");
}

int checks = 0;
var pdfSizes = new[] { (595d, 842d), (842d, 595d), (320d, 1200d), (420d, 220d) };
foreach (double leftWidth in new[] { 320d, 700d })
foreach (double rightWidth in new[] { 420d, 650d })
foreach (double zoom in new[] { .5, 1, 1.5, 3 })
{
    var leftHeights = pdfSizes.Select(size => leftWidth * zoom * size.Item2 / size.Item1).ToArray();
    var rightHeights = pdfSizes.Select(size => rightWidth * zoom * size.Item2 / size.Item1).ToArray();
    var left = PageScrollGeometry.Offsets(leftHeights, 16, 16);
    var right = PageScrollGeometry.Offsets(rightHeights, 16, 16);
    for (int page = 0; page < pdfSizes.Length; page++)
    foreach (double fraction in new[] { 0, .1, .5, .999 })
    {
        double originalOffset = PageScrollGeometry.Offset(page, fraction, left, leftHeights);
        var original = PageScrollGeometry.Locate(originalOffset, left, leftHeights);
        double translatedOffset = PageScrollGeometry.Offset(original.Page, original.Fraction, right, rightHeights);
        var translated = PageScrollGeometry.Locate(translatedOffset, right, rightHeights);
        Equal(translated.Page, page, "page across unequal pane widths");
        Equal(translated.Fraction, fraction, "within-page fraction across unequal widths");
        Equal(PageScrollGeometry.Offset(translated.Page, translated.Fraction, left, leftHeights), originalOffset, "reverse scroll round trip");
        checks++;
    }
    for (int page = 1; page < pdfSizes.Length; page++)
    {
        Equal(PageScrollGeometry.Locate(left[page], left, leftHeights).Page, page, "exact cross-page boundary");
        Equal(PageScrollGeometry.Locate(left[page] - 2, left, leftHeights).Page, page - 1, "page gap outside pixel tolerance stays with preceding page");
        checks++;
    }
}
var exampleHeights = new[] { 800d, 500d };
var offsets = PageScrollGeometry.Offsets(exampleHeights, 16, 16);
Equal(PageScrollGeometry.Locate(-10, offsets, exampleHeights).Fraction, 0, "negative viewport offset clamps");
Equal(PageScrollGeometry.Offset(99, 99, offsets, exampleHeights), 1332, "out-of-range jump clamps");
Equal(PageScrollGeometry.Offset(0, 0, [], []), 0, "empty document"); checks += 3;

// Quantize the requested Paper.Height before both WinUI layout and anchor sums.
// Cover long documents and both sync directions without depending on layout timing.
foreach (double scale in new[] { 1d, 1.25, 1.5, 2 })
{
    var requested = Enumerable.Repeat(569d * 792 / 612, 150).ToArray();
    var arranged = requested.Select(height => PageScrollGeometry.PixelAlignedHeight(height, scale)).ToArray();
    foreach (double height in arranged)
        Equal(height * scale, Math.Round(height * scale), "paper height is an exact device-pixel count");
    var measured = PageScrollGeometry.Offsets(arranged, 16, 16);
    var theoretical = PageScrollGeometry.Offsets(requested, 16, 16);
    if (Math.Abs(theoretical[112] - measured[112]) < 1)
        throw new Exception("Fixture must exercise accumulated layout rounding at page 113.");
    double pixel = 1 / scale;
    foreach (int page in new[] { 1, 112, 149 })
    foreach (double rounding in new[] { -1d, -.5, 0, .5 })
    {
        double actual = measured[page] + rounding * pixel;
        var anchor = PageScrollGeometry.Locate(actual, measured, arranged, pixel);
        Equal(anchor.Page, page, "pixel-rounded page top retains requested page");
        Equal(anchor.Fraction, Math.Max(0, rounding * pixel) / arranged[page], "page-top fraction clamps rounding before paper");
        double restored = PageScrollGeometry.Offset(anchor.Page, anchor.Fraction, measured, arranged);
        var returned = PageScrollGeometry.Locate(restored, measured, arranged, pixel);
        Equal(returned.Page, page, "page-top sync does not fall back through preceding gap");
        Equal(returned.Fraction, anchor.Fraction, "reverse page-top sync anchor is stable");
        checks++;
    }
    var otherHeights = Enumerable.Repeat(PageScrollGeometry.PixelAlignedHeight(650d * 792 / 612, scale), 150).ToArray();
    var otherOffsets = PageScrollGeometry.Offsets(otherHeights, 16, 16);
    foreach (int page in new[] { 1, 112, 149 })
    {
        double synced = measured[page] - pixel;
        for (int repeat = 0; repeat < 20; repeat++)
        {
            var left = PageScrollGeometry.Locate(synced, measured, arranged, pixel);
            double rightOffset = PageScrollGeometry.Offset(left.Page, left.Fraction, otherOffsets, otherHeights);
            var right = PageScrollGeometry.Locate(Math.Round(rightOffset * scale) / scale, otherOffsets, otherHeights, pixel);
            synced = PageScrollGeometry.Offset(right.Page, right.Fraction, measured, arranged);
            Equal(right.Page, page, "unequal-width repeated sync retains page after pixel rounding");
            Equal(synced, measured[page], "repeated sync does not accumulate page-gap drift");
            checks++;
        }
        Equal(PageScrollGeometry.Locate(measured[page] - 2 * pixel, measured, arranged, pixel).Page, page - 1,
            "gap outside one device pixel stays with preceding page");
        var end = PageScrollGeometry.Locate(measured[page - 1] + arranged[page - 1], measured, arranged, pixel);
        Equal(end.Page, page - 1, "previous page bottom is not promoted across gap");
        Equal(end.Fraction, 1, "previous page bottom retains end anchor");
        checks++;
    }
}

Equal(PageScrollGeometry.PixelAlignedHeight(10.25, 2), 10.5, "half-device-pixel height is assigned consistently");
Equal(PageScrollGeometry.PixelAlignedHeight(.1, 2), .5, "very short page retains one visible device pixel");
checks += 2;

var spans = JsonNode.Parse("""[{"token":"[[M1]]","latex":"x^2","bbox":[10,20,30,40]},{"token":"[[M2]]","latex":"\\alpha","bbox":[40,20,50,40]}]""")!.AsArray();
var fragments = ReadingInlineMath.Fragments("前文 [[M2]] 与 [[M1]]，尾文 $z$", spans).ToArray();
if (fragments.Length != 5 || fragments[0].Text != "前文 " || fragments[1].Math?["latex"]?.GetValue<string>() != "\\alpha"
    || fragments[2].Text != " 与 " || fragments[3].Math?["latex"]?.GetValue<string>() != "x^2" || fragments[4].Text != "，尾文 $z$")
    throw new Exception("Declared inline math tokens must retain the paragraph's Unicode text and formula order.");
if (ReadingInlineMath.Fragments("没有公式", spans).Single().Text != "没有公式") throw new Exception("Plain paragraph changed.");
if (ReadingInlineMath.Fragments("", spans).Any()) throw new Exception("Empty paragraph produced content.");
checks += 3;
var declared = new[] { .2, .3, 10d, 20d };
var crop = ReadingInlineMath.Crop(declared, 100, 100);
Equal(crop.Left, 0, "inline crop clamps left"); Equal(crop.Top, 0, "inline crop clamps top");
Equal(crop.Width, 10.75, "inline crop retains extra antialiasing pixels"); Equal(crop.Height, 20.75, "inline crop retains a formula hat");
Equal(declared[0], .2, "parser bbox remains immutable"); checks++;
crop = ReadingInlineMath.Crop(new[] { 90d, 90d, 100d, 100d }, 100, 100);
Equal(crop.Left + crop.Width, 100, "inline crop clamps right"); Equal(crop.Top + crop.Height, 100, "inline crop clamps bottom"); checks++;
Console.WriteLine($"PASS: {checks} page-anchor and inline-token cases; no native GUI claims.");
