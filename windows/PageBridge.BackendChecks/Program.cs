using System.Text.Json.Nodes;
using PageBridge.Windows;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        var library = Directory.CreateTempSubdirectory("PageBridge-backend-check-");
        try
        {
            using var client = new BackendClient(library.FullName);
            client.CallAsync("list").GetAwaiter().GetResult();

            // Exercise the same uncontended gate and live process as a reader layout callback.
            var observer = new WaitObserver();
            var previous = SynchronizationContext.Current;
            Task<JsonNode?> request;
            try
            {
                SynchronizationContext.SetSynchronizationContext(observer);
                request = client.CallAsync("list");
            }
            finally
            {
                SynchronizationContext.SetSynchronizationContext(previous);
            }

            if (request.GetAwaiter().GetResult() is not JsonArray { Count: 0 })
                throw new InvalidOperationException("Expected a successful reply for the empty library.");
            if (observer.WaitCount != 0)
                throw new InvalidOperationException($"Local RPC performed {observer.WaitCount} synchronous wait(s) on the STA.");
            Console.WriteLine("PASS: a warm local RPC leaves the calling STA free of synchronous waits.");
        }
        finally
        {
            library.Delete(recursive: true);
        }
    }

    private sealed class WaitObserver : SynchronizationContext
    {
        public int WaitCount { get; private set; }

        public WaitObserver() => SetWaitNotificationRequired();

        public override int Wait(IntPtr[] handles, bool waitAll, int millisecondsTimeout)
        {
            WaitCount++;
            return WaitHelper(handles, waitAll, millisecondsTimeout);
        }
    }
}
