namespace PageBridge.Windows;

/// <summary>Shared PDF-page anchors, independent of viewport width and zoom.</summary>
internal static class PageScrollGeometry
{
    internal static double PixelAlignedHeight(double height, double rasterizationScale) =>
        Math.Max(1, Math.Round(height * rasterizationScale, MidpointRounding.AwayFromZero)) / rasterizationScale;

    internal static double[] Offsets(IReadOnlyList<double> heights, double gap, double leading)
    {
        var result = new double[heights.Count]; double top = leading;
        for (int i = 0; i < heights.Count; i++) { result[i] = top; top += heights[i] + gap; }
        return result;
    }

    internal static (int Page, double Fraction) Locate(double offset, double[] offsets, IReadOnlyList<double> heights, double boundaryTolerance = 1)
    {
        if (offsets.Length == 0) return (0, 0);
        int index = Array.BinarySearch(offsets, Math.Max(0, offset));
        if (index < 0)
        {
            int next = ~index;
            // ChangeView can round an exact page-top request to a device pixel.
            // Keep that anchor on the requested page instead of the preceding gap.
            index = next < offsets.Length && offset >= offsets[next] - Math.Max(0, boundaryTolerance)
                ? next : Math.Max(0, next - 1);
        }
        index = Math.Min(index, offsets.Length - 1);
        return (index, Math.Clamp((offset - offsets[index]) / Math.Max(1, heights[index]), 0, 1));
    }

    internal static double Offset(int page, double fraction, double[] offsets, IReadOnlyList<double> heights)
    {
        if (offsets.Length == 0) return 0;
        int index = Math.Clamp(page, 0, offsets.Length - 1);
        return offsets[index] + heights[index] * Math.Clamp(fraction, 0, 1);
    }
}
