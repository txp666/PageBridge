namespace PageBridge.Windows;

internal static class AppVersion
{
    private static readonly Version Value = typeof(AppVersion).Assembly.GetName().Version
        ?? throw new InvalidOperationException("Application version is missing.");
    public static string Display => $"{Value.Major}.{Value.Minor}.{Value.Build}";
    public static int BuildNumber => Value.Revision;
}
