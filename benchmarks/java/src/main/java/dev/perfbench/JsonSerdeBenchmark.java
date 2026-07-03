package dev.perfbench;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
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
public class JsonSerdeBenchmark {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Param({"100", "1000"})
    public int itemCount;

    private Payload[] payloads;
    private byte[] json;

    @Setup
    public void setup() throws JsonProcessingException {
        payloads = new Payload[itemCount];
        for (int i = 0; i < itemCount; i++) {
            payloads[i] = new Payload(i, "item-" + i, i % 7 == 0, i * 1.25);
        }
        json = MAPPER.writeValueAsBytes(payloads);
    }

    @Benchmark
    public byte[] serialize() throws JsonProcessingException {
        return MAPPER.writeValueAsBytes(payloads);
    }

    @Benchmark
    public Payload[] deserialize() throws Exception {
        return MAPPER.readValue(json, Payload[].class);
    }

    public record Payload(int id, String name, boolean active, double score) {
    }
}

