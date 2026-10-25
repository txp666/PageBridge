using System.Runtime.InteropServices;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using WinRT.Interop;

namespace PageBridge.Windows;

// AppWindow dimensions are physical pixels; the Mac layout sizes are logical units.
internal sealed class WindowSizing
{
    private readonly nint handle;
    private readonly SubclassProc procedure;
    private readonly Window window;
    private delegate nint SubclassProc(nint hwnd, uint message, nuint wparam, nint lparam, nuint id, nuint data);
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] private struct MinMaxInfo { public Point Reserved, MaxSize, MaxPosition, MinTrackSize, MaxTrackSize; }
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint hwnd);
    [DllImport("comctl32.dll")] private static extern bool SetWindowSubclass(nint hwnd, SubclassProc callback, nuint id, nuint data);
    [DllImport("comctl32.dll")] private static extern bool RemoveWindowSubclass(nint hwnd, SubclassProc callback, nuint id);
    [DllImport("comctl32.dll")] private static extern nint DefSubclassProc(nint hwnd, uint message, nuint wparam, nint lparam);

    public WindowSizing(Window window, double scale)
    {
        this.window = window; handle = WindowNative.GetWindowHandle(window); procedure = ProcessMessage;
        SetWindowSubclass(handle, procedure, 1, 0);
        window.Closed += (_, _) => RemoveWindowSubclass(handle, procedure, 1);
        var area = DisplayArea.GetFromWindowId(window.AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        int width = Math.Min(area.Width - 24, (int)Math.Round(1480 * scale));
        int height = Math.Min(area.Height - 24, (int)Math.Round(960 * scale));
        window.AppWindow.MoveAndResize(new global::Windows.Graphics.RectInt32(area.X + (area.Width - width) / 2, area.Y + (area.Height - height) / 2, width, height));
    }
    private nint ProcessMessage(nint hwnd, uint message, nuint wparam, nint lparam, nuint id, nuint data)
    {
        if (message == 0x24)
        {
            var limits = Marshal.PtrToStructure<MinMaxInfo>(lparam);
            double scale = Math.Max(96, GetDpiForWindow(hwnd)) / 96d;
            var area = DisplayArea.GetFromWindowId(window.AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
            limits.MinTrackSize = new Point { X = Math.Min(area.Width, (int)(1100 * scale)), Y = Math.Min(area.Height, (int)(740 * scale)) };
            Marshal.StructureToPtr(limits, lparam, false);
            return 0;
        }
        return DefSubclassProc(hwnd, message, wparam, lparam);
    }
}
