using BenchmarkDotNet.Attributes;

namespace PerfBenchmarks;

[MemoryDiagnoser]
public class HashMapBenchmarks
{
    private string[] _keys = [];

    [Params(1_000, 100_000)]
    public int ItemCount { get; set; }

    [GlobalSetup]
    public void Setup()
    {
        _keys = Enumerable.Range(0, ItemCount)
            .Select(i => $"key-{i:000000}")
            .ToArray();
    }

    [Benchmark]
    public long BuildAndLookup()
    {
        var map = new Dictionary<string, int>(_keys.Length, StringComparer.Ordinal);
        for (var i = 0; i < _keys.Length; i++)
        {
            map[_keys[i]] = i;
        }

        var sum = 0L;
        for (var i = _keys.Length - 1; i >= 0; i--)
        {
            sum += map[_keys[i]];
        }

        return sum;
    }
}

