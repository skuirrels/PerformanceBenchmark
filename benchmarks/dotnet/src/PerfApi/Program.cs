using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;
using System.Collections.Concurrent;
using System.Net.Sockets;
using System.Text;
using Grpc.Core;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Npgsql;

var builder = WebApplication.CreateSlimBuilder(args);
builder.Logging.ClearProviders();
var grpcMode = Environment.GetEnvironmentVariable("PERFBENCH_GRPC") == "1";
var tunedMode = Environment.GetEnvironmentVariable("PERFBENCH_DOTNET_TUNED") == "1";

if (tunedMode)
{
    ThreadPool.SetMinThreads(Environment.ProcessorCount * 16, Environment.ProcessorCount * 16);
    builder.WebHost.ConfigureKestrel(options =>
    {
        options.AddServerHeader = false;
    });
}

if (grpcMode)
{
    builder.Services.AddGrpc();
    builder.WebHost.ConfigureKestrel(options =>
    {
        options.ConfigureEndpointDefaults(listenOptions =>
        {
            listenOptions.Protocols = HttpProtocols.Http2;
        });
    });
}

var app = builder.Build();

var jsonPayload = new ApiPayload("hello, world", 42, true);
var jsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web);
var jsonContext = PerfJsonContext.Default;
var httpClient = new HttpClient();
var selfBaseUri = Environment.GetEnvironmentVariable("PERFBENCH_SELF_URL");
var dbConnectionString = Environment.GetEnvironmentVariable("PERFBENCH_DB");
var dataSource = string.IsNullOrWhiteSpace(dbConnectionString) ? null : NpgsqlDataSource.Create(dbConnectionString);
var redisEndpoint = Environment.GetEnvironmentVariable("PERFBENCH_REDIS");
var redisPool = string.IsNullOrWhiteSpace(redisEndpoint) ? null : new RedisConnectionPool(redisEndpoint, 64);
var jsonBytes = """{"message":"hello, world","value":42,"active":true}"""u8.ToArray();
var plainBytes = "hello, world"u8.ToArray();
var healthBytes = "ok"u8.ToArray();
var downstreamBytes = """{"service":"downstream","value":42}"""u8.ToArray();

if (grpcMode)
{
    app.MapGrpcService<QuoteGrpcService>();
}

app.MapGet("/health", (HttpContext context) => WriteBytes(context, healthBytes, "text/plain"));
app.MapGet("/plaintext", (HttpContext context) => WriteBytes(context, plainBytes, "text/plain"));
app.MapGet("/json", (HttpContext context) => WriteBytes(context, jsonBytes, "application/json"));
app.MapGet("/json-serde", (HttpContext context) =>
{
    var payload = tunedMode
        ? JsonSerializer.SerializeToUtf8Bytes(jsonPayload, jsonContext.ApiPayload)
        : JsonSerializer.SerializeToUtf8Bytes(jsonPayload, jsonOptions);
    return WriteBytes(context, payload, "application/json");
});
app.MapPost("/orders/quote", async (HttpContext context) =>
{
    var request = (tunedMode
        ? await JsonSerializer.DeserializeAsync(context.Request.Body, jsonContext.QuoteRequest)
        : await JsonSerializer.DeserializeAsync<QuoteRequest>(context.Request.Body, jsonOptions))
        ?? new QuoteRequest("", 0, 0, false);
    var multiplier = request.Expedited ? 1.2m : 1.0m;
    var total = decimal.Round(request.ItemCount * request.UnitPrice * multiplier, 2);
    var response = new QuoteResponse(request.CustomerId, total, request.Expedited, true);
    await WriteJson(context, response, tunedMode ? jsonContext.QuoteResponse : null, jsonOptions);
});
app.MapGet("/fanout", async (HttpContext context) =>
{
    var baseUri = SelfBaseUri(context, selfBaseUri);
    var responses = await Task.WhenAll(
        httpClient.GetStringAsync($"{baseUri}/downstream/a"),
        httpClient.GetStringAsync($"{baseUri}/downstream/b"),
        httpClient.GetStringAsync($"{baseUri}/downstream/c"));
    var response = new FanoutResponse(responses.Length, responses.Sum(value => value.Length), true);
    await WriteJson(context, response, tunedMode ? jsonContext.FanoutResponse : null, jsonOptions);
});
app.MapGet("/downstream/{name}", (HttpContext context) => WriteBytes(context, downstreamBytes, "application/json"));
app.MapGet("/serialize/json", (HttpContext context) =>
{
    var payload = tunedMode
        ? JsonSerializer.SerializeToUtf8Bytes(new FormatPayload(123456, "standard", 99.95m, true), jsonContext.FormatPayload)
        : JsonSerializer.SerializeToUtf8Bytes(new FormatPayload(123456, "standard", 99.95m, true), jsonOptions);
    return WriteBytes(context, payload, "application/json");
});
app.MapGet("/serialize/binary", (HttpContext context) =>
{
    var payload = EncodeBinaryPayload(123456, "standard", 99.95m, true);
    return WriteBytes(context, payload, "application/octet-stream");
});
app.MapGet("/db/orders/{id:int}", async (HttpContext context, int id) =>
{
    if (dataSource is null)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        return;
    }

    await using var connection = await dataSource.OpenConnectionAsync(context.RequestAborted);
    await using var command = new NpgsqlCommand(
        "select id, customer_id, total_cents, status from orders where id = @id",
        connection);
    command.Parameters.AddWithValue("id", id);
    await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
    if (!await reader.ReadAsync(context.RequestAborted))
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    var response = new DbOrderResponse(
        reader.GetInt32(0),
        reader.GetString(1),
        reader.GetInt32(2),
        reader.GetString(3));
    await WriteJson(context, response, tunedMode ? jsonContext.DbOrderResponse : null, jsonOptions);
});
app.MapGet("/db/orders", async (HttpContext context) =>
{
    if (dataSource is null)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        return;
    }

    var customerId = context.Request.Query["customerId"].FirstOrDefault() ?? "customer-42";
    var limit = int.TryParse(context.Request.Query["limit"].FirstOrDefault(), out var parsedLimit)
        ? Math.Clamp(parsedLimit, 1, 100)
        : 50;
    var orders = new List<DbOrderResponse>(limit);
    await using var connection = await dataSource.OpenConnectionAsync(context.RequestAborted);
    await using var command = new NpgsqlCommand(
        "select id, customer_id, total_cents, status from orders where customer_id = @customerId order by id limit @limit",
        connection);
    command.Parameters.AddWithValue("customerId", customerId);
    command.Parameters.AddWithValue("limit", limit);
    await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
    while (await reader.ReadAsync(context.RequestAborted))
    {
        orders.Add(new DbOrderResponse(
            reader.GetInt32(0),
            reader.GetString(1),
            reader.GetInt32(2),
            reader.GetString(3)));
    }

    await WriteJson(context, new DbOrderPageResponse(customerId, orders.Count, orders), tunedMode ? jsonContext.DbOrderPageResponse : null, jsonOptions);
});
app.MapPost("/db/orders", async (HttpContext context) =>
{
    if (dataSource is null)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        return;
    }

    var request = (tunedMode
        ? await JsonSerializer.DeserializeAsync(context.Request.Body, jsonContext.DbOrderWriteRequest)
        : await JsonSerializer.DeserializeAsync<DbOrderWriteRequest>(context.Request.Body, jsonOptions))
        ?? new DbOrderWriteRequest("customer-42", 12345, "open");
    await using var connection = await dataSource.OpenConnectionAsync(context.RequestAborted);
    await using var command = new NpgsqlCommand(
        "insert into order_writes (customer_id, total_cents, status) values (@customerId, @totalCents, @status) returning id",
        connection);
    command.Parameters.AddWithValue("customerId", request.CustomerId);
    command.Parameters.AddWithValue("totalCents", request.TotalCents);
    command.Parameters.AddWithValue("status", request.Status);
    var id = (long)(await command.ExecuteScalarAsync(context.RequestAborted) ?? 0L);
    await WriteJson(context, new DbOrderWriteResponse(id, true), tunedMode ? jsonContext.DbOrderWriteResponse : null, jsonOptions);
});
app.MapGet("/cache/orders/{id:int}", async (HttpContext context, int id) =>
{
    if (redisPool is null)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        return;
    }

    var key = $"order:{id}";
    var cached = await redisPool.GetAsync(key, context.RequestAborted);
    if (cached is not null)
    {
        await WriteBytes(context, cached, "application/json");
        return;
    }

    if (dataSource is null)
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    await using var connection = await dataSource.OpenConnectionAsync(context.RequestAborted);
    await using var command = new NpgsqlCommand(
        "select id, customer_id, total_cents, status from orders where id = @id",
        connection);
    command.Parameters.AddWithValue("id", id);
    await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
    if (!await reader.ReadAsync(context.RequestAborted))
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    var response = new DbOrderResponse(reader.GetInt32(0), reader.GetString(1), reader.GetInt32(2), reader.GetString(3));
    var payload = tunedMode
        ? JsonSerializer.SerializeToUtf8Bytes(response, jsonContext.DbOrderResponse)
        : JsonSerializer.SerializeToUtf8Bytes(response, jsonOptions);
    await redisPool.SetAsync(key, payload, context.RequestAborted);
    await WriteBytes(context, payload, "application/json");
});

app.Run();

static Task WriteBytes(HttpContext context, byte[] payload, string contentType)
{
    context.Response.StatusCode = StatusCodes.Status200OK;
    context.Response.ContentType = contentType;
    context.Response.ContentLength = payload.Length;
    return context.Response.Body.WriteAsync(payload).AsTask();
}

static async Task WriteJson<T>(HttpContext context, T value, JsonTypeInfo<T>? jsonTypeInfo, JsonSerializerOptions options)
{
    var payload = jsonTypeInfo is null
        ? JsonSerializer.SerializeToUtf8Bytes(value, options)
        : JsonSerializer.SerializeToUtf8Bytes(value, jsonTypeInfo);
    await WriteBytes(context, payload, "application/json");
}

static string SelfBaseUri(HttpContext context, string? configuredBaseUri)
{
    return string.IsNullOrWhiteSpace(configuredBaseUri)
        ? $"{context.Request.Scheme}://{context.Request.Host}"
        : configuredBaseUri.TrimEnd('/');
}

static byte[] EncodeBinaryPayload(int id, string category, decimal amount, bool active)
{
    using var stream = new MemoryStream();
    using var writer = new BinaryWriter(stream);
    writer.Write(id);
    writer.Write(category);
    writer.Write(decimal.ToDouble(amount));
    writer.Write(active);
    return stream.ToArray();
}

internal sealed record ApiPayload(string Message, int Value, bool Active);
internal sealed record QuoteRequest(string CustomerId, int ItemCount, decimal UnitPrice, bool Expedited);
internal sealed record QuoteResponse(string CustomerId, decimal Total, bool Expedited, bool Accepted);
internal sealed record FanoutResponse(int Services, int Bytes, bool Complete);
internal sealed record FormatPayload(int Id, string Category, decimal Amount, bool Active);
internal sealed record DbOrderResponse(int Id, string CustomerId, int TotalCents, string Status);
internal sealed record DbOrderPageResponse(string CustomerId, int Count, IReadOnlyList<DbOrderResponse> Orders);
internal sealed record DbOrderWriteRequest(string CustomerId, int TotalCents, string Status);
internal sealed record DbOrderWriteResponse(long Id, bool Accepted);

[JsonSourceGenerationOptions(JsonSerializerDefaults.Web)]
[JsonSerializable(typeof(ApiPayload))]
[JsonSerializable(typeof(QuoteRequest))]
[JsonSerializable(typeof(QuoteResponse))]
[JsonSerializable(typeof(FanoutResponse))]
[JsonSerializable(typeof(FormatPayload))]
[JsonSerializable(typeof(DbOrderResponse))]
[JsonSerializable(typeof(DbOrderPageResponse))]
[JsonSerializable(typeof(DbOrderWriteRequest))]
[JsonSerializable(typeof(DbOrderWriteResponse))]
internal sealed partial class PerfJsonContext : JsonSerializerContext;

internal sealed class QuoteGrpcService : PerfBench.Grpc.QuoteService.QuoteServiceBase
{
    public override Task<PerfBench.Grpc.QuoteResponse> Quote(PerfBench.Grpc.QuoteRequest request, ServerCallContext context)
    {
        var multiplier = request.Expedited ? 1.2 : 1.0;
        var total = Math.Round(request.ItemCount * request.UnitPrice * multiplier, 2);
        return Task.FromResult(new PerfBench.Grpc.QuoteResponse
        {
            CustomerId = request.CustomerId,
            Total = total,
            Expedited = request.Expedited,
            Accepted = true
        });
    }
}

internal sealed class RedisConnectionPool
{
    private readonly ConcurrentQueue<RedisConnection> connections = new();
    private readonly SemaphoreSlim semaphore;

    public RedisConnectionPool(string endpoint, int size)
    {
        var parts = endpoint.Split(':', 2);
        var host = parts[0];
        var port = parts.Length == 2 ? int.Parse(parts[1]) : 6379;
        semaphore = new SemaphoreSlim(size, size);
        for (var i = 0; i < size; i++)
        {
            connections.Enqueue(RedisConnection.Connect(host, port));
        }
    }

    public async Task<byte[]?> GetAsync(string key, CancellationToken cancellationToken)
    {
        var connection = await Borrow(cancellationToken);
        try
        {
            return await connection.GetAsync(key, cancellationToken);
        }
        finally
        {
            Release(connection);
        }
    }

    public async Task SetAsync(string key, byte[] value, CancellationToken cancellationToken)
    {
        var connection = await Borrow(cancellationToken);
        try
        {
            await connection.SetAsync(key, value, cancellationToken);
        }
        finally
        {
            Release(connection);
        }
    }

    private async Task<RedisConnection> Borrow(CancellationToken cancellationToken)
    {
        await semaphore.WaitAsync(cancellationToken);
        return connections.TryDequeue(out var connection) ? connection : throw new InvalidOperationException("redis pool exhausted");
    }

    private void Release(RedisConnection connection)
    {
        connections.Enqueue(connection);
        semaphore.Release();
    }
}

internal sealed class RedisConnection
{
    private readonly NetworkStream stream;

    private RedisConnection(NetworkStream stream)
    {
        this.stream = stream;
    }

    public static RedisConnection Connect(string host, int port)
    {
        var client = new TcpClient { NoDelay = true };
        client.Connect(host, port);
        return new RedisConnection(client.GetStream());
    }

    public async Task<byte[]?> GetAsync(string key, CancellationToken cancellationToken)
    {
        await WriteCommand(cancellationToken, "GET", key);
        return await ReadBulk(cancellationToken);
    }

    public async Task SetAsync(string key, byte[] value, CancellationToken cancellationToken)
    {
        await WriteCommand(cancellationToken, "SET", key, Encoding.UTF8.GetString(value));
        await ReadSimple(cancellationToken);
    }

    private async Task WriteCommand(CancellationToken cancellationToken, params string[] parts)
    {
        var builder = new StringBuilder();
        builder.Append('*').Append(parts.Length).Append("\r\n");
        foreach (var part in parts)
        {
            builder.Append('$').Append(Encoding.UTF8.GetByteCount(part)).Append("\r\n");
            builder.Append(part).Append("\r\n");
        }
        var payload = Encoding.UTF8.GetBytes(builder.ToString());
        await stream.WriteAsync(payload, cancellationToken);
        await stream.FlushAsync(cancellationToken);
    }

    private async Task<byte[]?> ReadBulk(CancellationToken cancellationToken)
    {
        var prefix = await ReadByte(cancellationToken);
        if (prefix != '$')
        {
            throw new InvalidOperationException("unexpected redis response");
        }
        var length = int.Parse(await ReadLine(cancellationToken));
        if (length < 0)
        {
            return null;
        }
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, cancellationToken);
        await ReadByte(cancellationToken);
        await ReadByte(cancellationToken);
        return body;
    }

    private async Task ReadSimple(CancellationToken cancellationToken)
    {
        var prefix = await ReadByte(cancellationToken);
        if (prefix != '+')
        {
            throw new InvalidOperationException("unexpected redis response");
        }
        await ReadLine(cancellationToken);
    }

    private async Task<int> ReadByte(CancellationToken cancellationToken)
    {
        var buffer = new byte[1];
        await stream.ReadExactlyAsync(buffer, cancellationToken);
        return buffer[0];
    }

    private async Task<string> ReadLine(CancellationToken cancellationToken)
    {
        var bytes = new List<byte>(16);
        while (true)
        {
            var value = await ReadByte(cancellationToken);
            if (value == '\r')
            {
                await ReadByte(cancellationToken);
                return Encoding.UTF8.GetString(bytes.ToArray());
            }
            bytes.Add((byte)value);
        }
    }
}
