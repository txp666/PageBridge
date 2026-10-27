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
        Equal(PageScrollGeometry.Locate(left[page] - 1, left, leftHeights).Page, page - 1, "page gap stays with preceding page");
        checks++;
    }
}
var exampleHeights = new[] { 800d, 500d };
var offsets = PageScrollGeometry.Offsets(exampleHeights, 16, 16);
Equal(PageScrollGeometry.Locate(-10, offsets, exampleHeights).Fraction, 0, "negative viewport offset clamps");
Equal(PageScrollGeometry.Offset(99, 99, offsets, exampleHeights), 1332, "out-of-range jump clamps");
Equal(PageScrollGeometry.Offset(0, 0, [], []), 0, "empty document"); checks += 3;

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
