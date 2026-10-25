using Microsoft.UI.Xaml;
using System.Text.Json;

namespace PageBridge.Windows;

public partial class App : Application
{
    private Window? window;
    private Mutex? singleInstance;
    private readonly object lifecycleLock = new();
    private string? lifecyclePath;

    private void RecordLifecycle(string kind, Exception? error = null)
    {
        if (lifecyclePath is null) return;
        // QA only: never serialize messages, RPC payloads or document contents.
        try
        {
            var entry = new { at = DateTimeOffset.UtcNow, processId = Environment.ProcessId, kind,
                exceptionType = error?.GetType().FullName, hresult = error?.HResult, stack = error?.StackTrace,
                exitCode = kind == "process-exit" ? Environment.ExitCode : (int?)null };
            lock (lifecycleLock) File.AppendAllText(lifecyclePath, JsonSerializer.Serialize(entry) + "\n");
        }
        catch { /* Diagnostics must not change application exception behavior. */ }
    }
    public App()
    {
        // Set application-wide resources at startup, before creating controls.
        // A root-only override can leave Application.Resources on the other theme.
        var arguments = Environment.GetCommandLineArgs();
        if (arguments.Contains("--dark", StringComparer.Ordinal)) RequestedTheme = ApplicationTheme.Dark;
        else if (arguments.Contains("--light", StringComparer.Ordinal)) RequestedTheme = ApplicationTheme.Light;
        // With neither override, WinUI follows the Windows app-color setting.
        InitializeComponent();
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var arguments = Environment.GetCommandLineArgs();
        bool readerValidation = arguments.Contains("--reader-validation", StringComparer.Ordinal);
        // Keep legacy/normal launches in their original single-instance group.
        // Reader QA has a fixed separate library and never attaches to user data.
        string instanceName = "Local\\PageBridge.Windows.Preview" + (readerValidation ? ".ReaderValidation" : "");
        singleInstance = new Mutex(true, instanceName, out bool first);
        if (!first) { Exit(); return; }
        bool validation = arguments.Contains("--validation-data", StringComparer.Ordinal);
        bool uiValidation = arguments.Contains("--ui-validation", StringComparer.Ordinal);
        string? isolatedRoot = readerValidation || validation || uiValidation
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), readerValidation ? "PageBridge-WindowsReaderValidation" : uiValidation ? "PageBridge-WindowsUIValidation" : "PageBridge-WindowsValidation") : null;
        if (readerValidation)
        {
            Directory.CreateDirectory(isolatedRoot!);
            lifecyclePath = Path.Combine(isolatedRoot!, $"qa-lifecycle-{Environment.ProcessId}.ndjson");
            RecordLifecycle("launch");
            // Do not set Handled or consume either original exception.
            UnhandledException += (_, eventArgs) => RecordLifecycle("xaml-unhandled", eventArgs.Exception);
            AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) => RecordLifecycle("domain-unhandled", eventArgs.ExceptionObject as Exception);
            AppDomain.CurrentDomain.ProcessExit += (_, _) => RecordLifecycle("process-exit");
        }
        window = new MainWindow(isolatedRoot);
        if (readerValidation) window.Closed += (_, _) => RecordLifecycle("window-closed");
        window.Activate();
        RecordLifecycle("activated");
    }
}
