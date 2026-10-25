using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Unicode;
using System.Threading;
using System.Threading.Tasks;

namespace PageBridge.Windows;

/// <summary>Bounded UTF-8 RPC; local documents reuse a private bridge session.</summary>
public sealed class BackendClient : IDisposable
{
    private const int MaximumRequestBytes = 65_536;
    private const int MaximumLineBytes = 1_048_576;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    // This JSON travels over a private pipe, never an HTML page. Keep Unicode
    // text as UTF-8 so Chinese notes use the actual wire budget.
    private static readonly JsonSerializerOptions RequestJson = new()
    {
        Encoder = JavaScriptEncoder.Create(UnicodeRanges.All)
    };
    private readonly SemaphoreSlim rpcGate = new(1, 1);
    private readonly SemaphoreSlim localRpcGate = new(1, 1);
    private readonly SemaphoreSlim translationGate = new(1, 1);
    private readonly CancellationTokenSource shutdown = new();
    private LocalSession? localSession;
    private int disposed;

    public string DataRoot { get; }

    public BackendClient(string? dataRoot = null)
    {
        DataRoot = Path.GetFullPath(dataRoot ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PageBridge"));
    }

    public async Task<JsonNode?> CallAsync(string action, JsonObject? args = null,
        CancellationToken cancellationToken = default)
    {
        shutdown.Token.ThrowIfCancellationRequested();
        if (action == "translate")
            throw new BackendException("翻译任务需要通过流式接口启动。");
        if (action is "list" or "recover" or "import" or "get" or "rename" or "folders"
            or "create_folder" or "move" or "trash" or "favorite" or "reading"
            or "export" or "render" or "page_info" or "document_navigation" or "page_text"
            or "annotations" or "save_annotation" or "delete_annotation" or "export_annotations"
            or "export_notes" or "search_document" or "rename_folder" or "delete_folder" or "literature_cleanup" or "literature_settings" or "literature_save_settings" or "assistant_history" or "assistant_clear" or "assistant_preview"
            or "update_check" or "update_download" or "update_status" or "update_cancel" or "update_prepare_install")
            return await CallLocalAsync(action, args, cancellationToken).ConfigureAwait(false);
        await rpcGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            JsonObject request = args?.DeepClone() as JsonObject ?? new JsonObject();
            request["action"] = action;
            request["dataRoot"] = DataRoot;
            byte[] payload = EncodeRequest(request);
            using Process process = StartBridge();
            using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, shutdown.Token);
            // Provider timeouts can be up to ten minutes; process/protocol setup
            // has a bounded additional allowance. No stderr is retained.
            lifetime.CancelAfter(TimeSpan.FromMinutes(11));
            Task stderr = DiscardAsync(process.StandardError.BaseStream, lifetime.Token);
            try
            {
                await process.StandardInput.BaseStream.WriteAsync(payload, lifetime.Token).ConfigureAwait(false);
                await process.StandardInput.BaseStream.FlushAsync(lifetime.Token).ConfigureAwait(false);
                process.StandardInput.Close();
                var reader = new BoundedLines(process.StandardOutput.BaseStream);
                JsonObject reply = ParseObject(await reader.ReadAsync(lifetime.Token).ConfigureAwait(false));
                if (await reader.ReadAsync(lifetime.Token).ConfigureAwait(false) is not null)
                    throw new BackendException("后台返回了多余消息，请重试。");
                await process.WaitForExitAsync(lifetime.Token).ConfigureAwait(false);
                if (!TryBoolean(reply["ok"], out bool ok))
                    throw new BackendException("后台响应格式无效。");
                if (!ok)
                    throw new BackendException(ErrorMessage(reply["error"]));
                if (process.ExitCode != 0)
                    throw new BackendException("后台意外退出，操作未确认完成。");
                return reply["result"]?.DeepClone();
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested && !shutdown.IsCancellationRequested)
            {
                throw new BackendException("操作超时，请检查服务连接后重试。");
            }
            catch (Exception error) when (error is IOException or JsonException or DecoderFallbackException)
            {
                throw new BackendException("无法读取后台响应，请检查运行环境后重试。");
            }
            finally
            {
                lifetime.Cancel();
                await StopAsync(process).ConfigureAwait(false);
                await stderr.ConfigureAwait(false);
                Array.Clear(payload);
            }
        }
        finally
        {
            rpcGate.Release();
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        shutdown.Cancel();
        Interlocked.Exchange(ref localSession, null)?.Dispose();
    }

    private async Task<JsonNode?> CallLocalAsync(string action, JsonObject? args, CancellationToken cancellationToken)
    {
        JsonObject request = args?.DeepClone() as JsonObject ?? new JsonObject();
        request["action"] = action;
        request["dataRoot"] = DataRoot;
        request["protocolVersion"] = 1;
        request["requestId"] = Guid.Empty.ToString();
        // Reject oversized input before touching an existing reader session.
        // ExchangeAsync replaces this same-length placeholder with a fresh ID.
        _ = SerializeRequest(request);
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, shutdown.Token);
        lifetime.CancelAfter(TimeSpan.FromSeconds(action is "import" or "export" or "export_annotations" or "export_notes" ? 120 : 30));
        bool acquired = false, received = false;
        LocalSession? session = null;
        try
        {
            await localRpcGate.WaitAsync(lifetime.Token).ConfigureAwait(false);
            acquired = true;
            lifetime.Token.ThrowIfCancellationRequested();
            session = localSession;
            if (session is null || !session.IsAlive)
            {
                session?.Dispose();
                session = new LocalSession(StartBridge());
                localSession = session;
                // Dispose may race process creation while the window closes.
                lifetime.Token.ThrowIfCancellationRequested();
                var hello = await ExchangeAsync(session, new JsonObject { ["action"] = "serve", ["dataRoot"] = DataRoot }, lifetime.Token).ConfigureAwait(false);
                if (!TryBoolean(hello["ok"], out bool ready) || !ready || StringValue(hello["result"]?["mode"]) != "serve")
                    throw new BackendException("本地文档会话未能启动，请检查安装是否完整。");
            }
            JsonObject reply = await ExchangeAsync(session, request, lifetime.Token).ConfigureAwait(false);
            received = true;
            if (!TryBoolean(reply["ok"], out bool ok) || !ok)
                throw new BackendException(ErrorMessage(reply["error"]));
            return reply["result"]?.DeepClone();
        }
        catch (Exception) when (lifetime.IsCancellationRequested)
        {
            if (cancellationToken.IsCancellationRequested || shutdown.IsCancellationRequested)
                throw new OperationCanceledException(cancellationToken.IsCancellationRequested ? cancellationToken : shutdown.Token);
            throw new BackendException("本地文档操作超时，请重试。");
        }
        catch (Exception error) when (error is IOException or JsonException or DecoderFallbackException or ObjectDisposedException or InvalidOperationException)
        {
            throw new BackendException("本地文档连接已结束，请重试；操作结果请刷新文档库确认。");
        }
        finally
        {
            // Never replay a failed write: a reply may have been lost after a
            // successful import/export. The next independent call reconnects.
            if (!received && session is not null)
            {
                if (ReferenceEquals(localSession, session)) localSession = null;
                session.Dispose();
            }
            if (acquired) localRpcGate.Release();
        }
    }

    private static async Task<JsonObject> ExchangeAsync(LocalSession session, JsonObject request, CancellationToken token)
    {
        string identifier = Guid.NewGuid().ToString();
        request["protocolVersion"] = 1;
        request["requestId"] = identifier;
        byte[] payload = EncodeRequest(request);
        // Termination also wakes a pending synchronous Windows pipe read if
        // its Stream implementation cannot interrupt it on token cancellation.
        using var abort = token.Register(session.Dispose);
        try
        {
            token.ThrowIfCancellationRequested();
            await session.Process.StandardInput.BaseStream.WriteAsync(payload, token).ConfigureAwait(false);
            await session.Process.StandardInput.BaseStream.FlushAsync(token).ConfigureAwait(false);
            JsonObject reply = ParseObject(await session.Reader.ReadAsync(token).ConfigureAwait(false));
            if (!TryInteger(reply["protocolVersion"], out int version) || version != 1
                || StringValue(reply["requestId"]) != identifier || !TryBoolean(reply["ok"], out _))
                throw new BackendException("本地文档响应身份或格式无效。");
            return reply;
        }
        finally { Array.Clear(payload); }
    }

    private sealed class LocalSession : IDisposable
    {
        public Process Process { get; }
        public BoundedLines Reader { get; }
        private readonly CancellationTokenSource lifetime = new();
        private int disposed;
        public bool IsAlive
        {
            get
            {
                if (Volatile.Read(ref disposed) != 0) return false;
                try { return !Process.HasExited; }
                catch (InvalidOperationException) { return false; }
            }
        }

        public LocalSession(Process process)
        {
            Process = process;
            Reader = new BoundedLines(process.StandardOutput.BaseStream);
            // Drain diagnostics without retaining text. DiscardAsync handles
            // cancellation/stream disposal itself, including late completion.
            _ = DiscardAsync(process.StandardError.BaseStream, lifetime.Token);
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposed, 1) != 0) return;
            lifetime.Cancel();
            try { if (!Process.HasExited) Process.Kill(entireProcessTree: true); }
            catch (Exception error) when (error is InvalidOperationException or Win32Exception) { }
            Process.Dispose();
            lifetime.Dispose();
        }
    }

    public async Task<JsonObject?> TranslateAsync(string documentId, string serviceId,
        string sourceLang, string targetLang, string? pages, Action<JsonObject> onEvent,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(onEvent);
        if (!await translationGate.WaitAsync(0, cancellationToken).ConfigureAwait(false))
            throw new BackendException("已有翻译正在进行，请完成或取消后再试。");
        try
        {
            // Only the profile ID crosses this interface. The bridge resolves
            // its saved credential privately through Windows Credential Manager.
            var request = new JsonObject
            {
                ["action"] = "translate", ["dataRoot"] = DataRoot,
                ["document_id"] = documentId, ["service_id"] = serviceId,
                ["sourceLanguage"] = sourceLang, ["targetLanguage"] = targetLang,
                ["pages"] = string.IsNullOrWhiteSpace(pages) ? null : pages
            };
            byte[] payload = EncodeRequest(request);
            cancellationToken.ThrowIfCancellationRequested();
            using Process process = StartBridge();
            using var lifetime = new CancellationTokenSource();
            Task stderr = DiscardAsync(process.StandardError.BaseStream, lifetime.Token);
            Task<JsonObject>? readTask = null;
            try
            {
                await process.StandardInput.BaseStream.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
                await process.StandardInput.BaseStream.FlushAsync(cancellationToken).ConfigureAwait(false);
                readTask = ReadTranslationAsync(process, documentId, onEvent, lifetime.Token);
                var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                using CancellationTokenRegistration registration = cancellationToken.Register(() => cancelled.TrySetResult());
                await Task.WhenAny(readTask, cancelled.Task).ConfigureAwait(false);
                if (cancellationToken.IsCancellationRequested)
                {
                    // The bridge gives its worker ten seconds to cancel and
                    // terminate its Job Object, then persists the final status.
                    using var cancelDeadline = new CancellationTokenSource(TimeSpan.FromSeconds(12));
                    try
                    {
                        byte[] command = Utf8.GetBytes("{\"action\":\"cancel\"}\n");
                        await process.StandardInput.BaseStream.WriteAsync(command, cancelDeadline.Token).ConfigureAwait(false);
                        await process.StandardInput.BaseStream.FlushAsync(cancelDeadline.Token).ConfigureAwait(false);
                        await readTask.WaitAsync(cancelDeadline.Token).ConfigureAwait(false);
                    }
                    catch (Exception error) when (error is OperationCanceledException or IOException or BackendException)
                    {
                        // Cancellation is the caller-visible outcome, even if
                        // the bridge reports its expected cancelled error first.
                    }
                    throw new OperationCanceledException(cancellationToken);
                }
                JsonObject document = await readTask.ConfigureAwait(false);
                cancellationToken.ThrowIfCancellationRequested();
                return document;
            }
            catch (Exception error) when (error is IOException or JsonException or DecoderFallbackException)
            {
                throw new BackendException("无法读取翻译进度，请刷新文档状态后检查结果。");
            }
            finally
            {
                lifetime.Cancel();
                await StopAsync(process).ConfigureAwait(false);
                if (readTask is not null)
                {
                    try { await readTask.ConfigureAwait(false); }
                    catch (Exception) { /* Observe background reader failure after cancellation. */ }
                }
                await stderr.ConfigureAwait(false);
                Array.Clear(payload);
            }
        }
        finally
        {
            translationGate.Release();
        }
    }

    private static async Task<JsonObject> ReadTranslationAsync(Process process, string documentId,
        Action<JsonObject> onEvent, CancellationToken cancellationToken)
    {
        var reader = new BoundedLines(process.StandardOutput.BaseStream);
        string? job = null, attempt = null;
        int lastSequence = 0;
        JsonObject? terminal = null;
        while (await reader.ReadAsync(cancellationToken).ConfigureAwait(false) is string line)
        {
            JsonObject message = ParseObject(line);
            string? kind = StringValue(message["type"]);
            string? messageJob = StringValue(message["jobId"]);
            string? messageAttempt = StringValue(message["attemptId"]);
            if (!TryInteger(message["protocolVersion"], out int version) || version != 1
                || !TryInteger(message["sequence"], out int sequence) || sequence <= lastSequence
                || !Guid.TryParse(messageJob, out _) || !Guid.TryParse(messageAttempt, out _)
                || (job is not null && (job != messageJob || attempt != messageAttempt))
                || kind is not ("ready" or "stage" or "progress" or "result" or "error")
                || (job is null && kind is not ("ready" or "error"))
                || terminal is not null)
                throw new BackendException("翻译消息身份、顺序或格式无效，结果未被确认。");
            if (message["progress"] is JsonNode progress
                && (!(progress is JsonValue value && value.TryGetValue<double>(out double fraction))
                    || !double.IsFinite(fraction) || fraction < 0 || fraction > 1))
                throw new BackendException("翻译进度值无效。");
            job ??= messageJob;
            attempt ??= messageAttempt;
            lastSequence = sequence;
            if (kind is "result" or "error") terminal = message;
            onEvent((JsonObject)message.DeepClone());
        }
        await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        if (terminal is null)
            throw new BackendException("翻译进程已结束，但没有返回完整结果。");
        if (StringValue(terminal["type"]) == "error")
            throw new BackendException(ErrorMessage(terminal["message"]));
        if (process.ExitCode != 0 || terminal["document"] is not JsonObject document
            || StringValue(document["id"]) != documentId || StringValue(document["status"]) != "completed")
            throw new BackendException("翻译结果身份或保存状态无效。");
        return (JsonObject)document.DeepClone();
    }

    private static string SerializeRequest(JsonObject request)
    {
        string json = request.ToJsonString(RequestJson);
        if (Utf8.GetByteCount(json) + 1 > MaximumRequestBytes)
            throw new BackendException("本次输入超过 64 KB，请缩短笔记、问题或选文，分段保存后重试。");
        return json;
    }

    private static byte[] EncodeRequest(JsonObject request) => Utf8.GetBytes(SerializeRequest(request) + "\n");

    private static Process StartBridge()
    {
        (string python, string bridge) = LocateRuntime();
        var info = new ProcessStartInfo(python)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = Utf8, StandardOutputEncoding = Utf8, StandardErrorEncoding = Utf8,
            WorkingDirectory = Path.GetDirectoryName(bridge)!
        };
        info.ArgumentList.Add("-B");
        info.ArgumentList.Add("-u");
        info.ArgumentList.Add(bridge);
        foreach (string key in new System.Collections.Generic.List<string>(info.Environment.Keys))
            if (key.EndsWith("API_KEY", StringComparison.OrdinalIgnoreCase)
                || key.EndsWith("API_TOKEN", StringComparison.OrdinalIgnoreCase)
                || key is "PYTHONPATH" or "PYTHONHOME" or "VIRTUAL_ENV")
                info.Environment.Remove(key);
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["PYTHONNOUSERSITE"] = "1";
        info.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        try
        {
            return Process.Start(info) ?? throw new BackendException("无法启动私有文档引擎。");
        }
        catch (Exception error) when (error is Win32Exception or IOException or InvalidOperationException)
        {
            throw new BackendException("无法启动私有文档引擎，请检查安装是否完整。");
        }
    }

    private static (string Python, string Bridge) LocateRuntime()
    {
        string resources = Path.Combine(AppContext.BaseDirectory, "resources");
        string python = Path.Combine(resources, "engine", "runtime", "python.exe");
        string bridge = Path.Combine(resources, "desktop", "bridge.py");
        if (File.Exists(python) && File.Exists(bridge)) return (python, bridge);
        // An explicit developer root never silently falls back to another tree.
        string? requestedRoot = Environment.GetEnvironmentVariable("PAGEBRIDGE_ROOT");
        if (!string.IsNullOrWhiteSpace(requestedRoot))
            return SourceRuntime(Path.GetFullPath(requestedRoot))
                ?? throw new BackendException("PAGEBRIDGE_ROOT 中未找到 Windows 引擎与 desktop/bridge.py。");
        for (DirectoryInfo? directory = new(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
            if (SourceRuntime(directory.FullName) is { } source) return source;
        throw new BackendException("未找到私有 Python 引擎，请完整解压安装包；源码运行需先安装 Windows 引擎。");
    }

    private static (string Python, string Bridge)? SourceRuntime(string root)
    {
        string python = Path.Combine(root, "engine", ".venv", "Scripts", "python.exe");
        string bridge = Path.Combine(root, "desktop", "bridge.py");
        return File.Exists(python) && File.Exists(bridge) ? (python, bridge) : null;
    }

    private static async Task StopAsync(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception or OperationCanceledException)
        {
            // No process command lines or stderr are surfaced to the user.
        }
    }

    private static async Task DiscardAsync(Stream stream, CancellationToken token)
    {
        byte[] buffer = new byte[8_192];
        try { while (await stream.ReadAsync(buffer, token).ConfigureAwait(false) != 0) { } }
        catch (Exception error) when (error is IOException or ObjectDisposedException or OperationCanceledException) { }
        finally { Array.Clear(buffer); }
    }

    private static JsonObject ParseObject(string? line)
        => line is not null && JsonNode.Parse(line, documentOptions: new JsonDocumentOptions { MaxDepth = 64 }) is JsonObject value
            ? value : throw new BackendException("后台没有返回有效的 JSON 消息。");

    private static string? StringValue(JsonNode? node)
        => node is JsonValue value && value.TryGetValue<string>(out string? text) ? text : null;
    private static bool TryInteger(JsonNode? node, out int result)
    {
        result = 0;
        return node is JsonValue value && value.TryGetValue<int>(out result);
    }
    private static bool TryBoolean(JsonNode? node, out bool result)
    {
        result = false;
        return node is JsonValue value && value.TryGetValue<bool>(out result);
    }
    private static string ErrorMessage(JsonNode? node)
    {
        string? message = StringValue(node);
        return string.IsNullOrWhiteSpace(message) ? "无法完成操作，请重试。" : message[..Math.Min(message.Length, 2_000)];
    }

    private sealed class BoundedLines(Stream stream)
    {
        private readonly byte[] buffer = new byte[8_192];
        private int start, end;

        public async Task<string?> ReadAsync(CancellationToken cancellationToken)
        {
            using var line = new MemoryStream();
            while (true)
            {
                if (start == end)
                {
                    start = 0;
                    end = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                    if (end == 0)
                    {
                        if (line.Length != 0) throw new BackendException("后台消息未完整结束。");
                        return null;
                    }
                }
                int newline = Array.IndexOf(buffer, (byte)'\n', start, end - start);
                int count = (newline >= 0 ? newline : end) - start;
                if (line.Length + count > MaximumLineBytes)
                    throw new BackendException("后台消息超过大小限制。");
                line.Write(buffer, start, count);
                start += count;
                if (newline >= 0)
                {
                    start++;
                    return Utf8.GetString(line.GetBuffer(), 0, checked((int)line.Length)).TrimEnd('\r');
                }
            }
        }
    }
}

public sealed class BackendException(string message) : Exception(message) { }
