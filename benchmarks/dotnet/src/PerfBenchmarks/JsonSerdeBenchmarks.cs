using System.Text.Json;
using BenchmarkDotNet.Attributes;

namespace PerfBenchmarks;

[MemoryDiagnoser]
public class JsonSerdeBenchmarks
{
    private Payload[] _payloads = [];
    private byte[] _json = [];

    [Params(100, 1_000)]
    public int ItemCount { get; set; }

    [GlobalSetup]
    public void Setup()
    {
        _payloads = Enumerable.Range(0, ItemCount)
            .Select(i => new Payload(i, $"item-{i}", i % 7 == 0, i * 1.25))
            .ToArray();
        _json = JsonSerializer.SerializeToUtf8Bytes(_payloads);
    }

    [Benchmark]
    public byte[] Serialize() => JsonSerializer.SerializeToUtf8Bytes(_payloads);

    [Benchmark]
    public Payload[] Deserialize() => JsonSerializer.Deserialize<Payload[]>(_json)!;

    public sealed record Payload(int Id, string Name, bool Active, double Score);
}

