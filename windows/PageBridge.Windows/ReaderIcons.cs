using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;

namespace PageBridge.Windows;

// Shared outline shapes keep the Windows reading controls aligned with macOS.
internal static class ReaderIcons
{
    internal static FrameworkElement Make(string name, double size = 16)
    {
        string data = name switch
        {
            "plus" => "M10 3 V17 M3 10 H17",
            "search" => "M14 14 L18 18 M16 9 A7 7 0 1 1 2 9 A7 7 0 1 1 16 9",
            "folder" => "M2 5 H8 L10 7 H18 V17 H2 Z M2 5 V3 H7 L9 5 H18 V7",
            "stack" => "M5 2 H15 M4 5 H16 M3 8 H17 V18 H3 Z",
            "tray" => "M2 8 L5 3 H15 L18 8 V17 H2 Z M2 8 H7 L8 11 H12 L13 8 H18",
            "star" => "M10 2 L12.5 7 H18 L14 11 L15.5 17 L10 14 L4.5 17 L6 11 L2 7 H7.5 Z",
            "clock" => "M18 10 A8 8 0 1 1 2 10 A8 8 0 1 1 18 10 M10 5 V10 H6",
            "trash" => "M3 5 H17 M7 5 V2 H13 V5 M5 5 L6 18 H14 L15 5 M8 8 V15 M12 8 V15",
            "settings" => "M8 2 H12 L13 5 L16 6 L18 8 V12 L15 13 L14 16 L12 18 H8 L7 15 L4 14 L2 12 V8 L5 7 L6 4 Z M13 10 A3 3 0 1 1 7 10 A3 3 0 1 1 13 10",
            "books" => "M2 4 H5 V18 H2 Z M7 2 H10 V18 H7 Z M12 5 L15 4 L19 17 L16 18 Z",
            "sidebar" => "M2 3 H18 V17 H2 Z M7 3 V17 M4 6 H5 M4 9 H5",
            "globe" => "M18 10 A8 8 0 1 1 2 10 A8 8 0 1 1 18 10 M2 10 H18 M10 2 C4 6 4 14 10 18 C16 14 16 6 10 2",
            "note" => "M10 3 H3 V17 H17 V10 M8 12 L9 8 L16 1 L19 4 L12 11 Z",
            "highlight" => "M4 13 L12 3 L17 7 L9 16 Z M4 13 L3 17 H7 M2 19 H18",
            "underline" => "M5 2 V10 C5 17 15 17 15 10 V2 M4 18 H16",
            "bookmark" => "M5 2 H15 V18 L10 14 L5 18 Z",
            "copy" => "M7 2 H16 V14 H7 Z M4 5 H2 V18 H12 V16",
            "notes" => "M2 3 H18 V17 H2 Z M5 7 H6 M9 7 H15 M5 10 H6 M9 10 H15 M5 13 H6 M9 13 H13",
            "sparkles" => "M11 2 L13 7 L18 9 L13 11 L11 17 L9 11 L4 9 L9 7 Z M3 1 V5 M1 3 H5",
            "translate" => "M2 2 H18 V14 H11 L7 18 V14 H2 Z M5 6 H13 M9 4 V6 M7 6 C7 9 10 11 13 11 M12 6 C12 9 9 11 6 11",
            "link" => "M7 12 L12 7 M6 13 L4 15 C0 19 -2 13 2 10 L6 6 C10 2 14 6 11 9 M9 11 C6 14 10 18 14 14 L18 10 C22 6 16 2 13 5",
            "close" => "M5 5 L15 15 M15 5 L5 15",
            "left" => "M12 4 L6 10 L12 16",
            "right" => "M8 4 L14 10 L8 16",
            "down" => "M5 7 L10 12 L15 7",
            "info" => "M18 10 A8 8 0 1 1 2 10 A8 8 0 1 1 18 10 M10 9 V14 M10 5 V6",
            _ => "M4 2 H12 L16 6 V18 H4 Z M12 2 V6 H16 M7 10 H13 M7 13 H13"
        };
        var path = (Microsoft.UI.Xaml.Shapes.Path)XamlReader.Load($"<Path xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Data='{data}' StrokeThickness='1.5' StrokeStartLineCap='Round' StrokeEndLineCap='Round' StrokeLineJoin='Round' />");
        void Tint() => path.Stroke = new SolidColorBrush(path.ActualTheme == ElementTheme.Dark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black);
        path.Opacity = .8; path.Loaded += (_, _) => Tint(); path.ActualThemeChanged += (_, _) => Tint(); Tint();
        var canvas = new Canvas { Width = 20, Height = 20 }; canvas.Children.Add(path);
        return new Viewbox { Width = size, Height = size, Child = canvas };
    }
}
