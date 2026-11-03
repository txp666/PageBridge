using PageBridge.Windows;

// Regression: in a 150-page document, page 113 fell back to page 112 after a
// detail flyout closed, because fractional page heights accumulated layout
// rounding. Paper heights are quantized to device pixels before both WinUI
// layout and anchor sums, and page tops tolerate one device pixel.
static void Equal(double actual, double expected, string detail)
{
    if (Math.Abs(actual - expected) > 1e-9) throw new Exception($"{detail}: expected {expected}, got {actual}");
}

int checks = 0;
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
Console.WriteLine($"PASS: {checks} pixel-rounded page-anchor cases.");
