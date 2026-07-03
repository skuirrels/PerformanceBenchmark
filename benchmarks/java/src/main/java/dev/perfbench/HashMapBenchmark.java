package dev.perfbench;

import java.util.HashMap;
import java.util.concurrent.TimeUnit;
import org.openjdk.jmh.annotations.Benchmark;
import org.openjdk.jmh.annotations.BenchmarkMode;
import org.openjdk.jmh.annotations.Fork;
import org.openjdk.jmh.annotations.Measurement;
import org.openjdk.jmh.annotations.Mode;
import org.openjdk.jmh.annotations.OutputTimeUnit;
import org.openjdk.jmh.annotations.Param;
import org.openjdk.jmh.annotations.Scope;
import org.openjdk.jmh.annotations.Setup;
import org.openjdk.jmh.annotations.State;
import org.openjdk.jmh.annotations.Warmup;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.NANOSECONDS)
@Warmup(iterations = 5)
@Measurement(iterations = 10)
@Fork(3)
@State(Scope.Thread)
public class HashMapBenchmark {
    @Param({"1000", "100000"})
    public int itemCount;

    private String[] keys;

    @Setup
    public void setup() {
        keys = new String[itemCount];
        for (int i = 0; i < itemCount; i++) {
            keys[i] = String.format("key-%06d", i);
        }
    }

    @Benchmark
    public long buildAndLookup() {
        HashMap<String, Integer> map = new HashMap<>(keys.length);
        for (int i = 0; i < keys.length; i++) {
            map.put(keys[i], i);
        }

        long sum = 0;
        for (int i = keys.length - 1; i >= 0; i--) {
            sum += map.get(keys[i]);
        }
        return sum;
    }
}

