using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;

namespace PageBridge.Windows;

/// <summary>A compact, baseline-aligned page field without NumberBox header/spinner layout.</summary>
internal sealed class PageNumberInput : TextBox
{
    private double minimum = 1, maximum = 1, current = 1;
    internal event EventHandler? ValueChanged;
    internal double Minimum { get => minimum; set { minimum = value; Value = current; } }
    internal double Maximum { get => maximum; set { maximum = value; Width = Math.Clamp(12 + Math.Max(1, (int)maximum).ToString(CultureInfo.InvariantCulture).Length * 7, 32, 58); Value = current; } }
    internal double Value
    {
        get => current;
        set
        {
            double next = Math.Clamp(double.IsFinite(value) ? Math.Truncate(value) : current, minimum, Math.Max(minimum, maximum));
            bool changed = next != current; current = next;
            string label = ((int)next).ToString(CultureInfo.InvariantCulture);
            if (Text != label) Text = label;
            if (changed) ValueChanged?.Invoke(this, EventArgs.Empty);
        }
    }

    internal PageNumberInput()
    {
        Text = "1"; MaxLength = 6; FontSize = 12;
        Width = 32; MinWidth = 0; Height = 28; MinHeight = 0;
        TextAlignment = TextAlignment.Center; VerticalAlignment = VerticalAlignment.Center;
        VerticalContentAlignment = VerticalAlignment.Center;
        // Match the digit baseline of the adjacent page-count TextBlock.
        Padding = new Thickness(3, 7, 3, 1); BorderThickness = new Thickness(0);
        InputScope = new InputScope { Names = { new InputScopeName { NameValue = InputScopeNameValue.Number } } };
        GotFocus += (_, _) => SelectAll();
        LostFocus += (_, _) => Commit();
        KeyDown += (_, e) =>
        {
            if (e.Key == global::Windows.System.VirtualKey.Enter) { Commit(); SelectAll(); e.Handled = true; }
            else if (e.Key == global::Windows.System.VirtualKey.Escape) { Value = current; SelectAll(); e.Handled = true; }
        };
    }

    private void Commit() => Value = int.TryParse(Text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed) ? parsed : current;
}
