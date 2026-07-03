package dev.perfbench;

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
public class NBodyBenchmark {
    @Param({"1000", "10000"})
    public int steps;

    private Body[] bodies;

    @Setup
    public void setup() {
        bodies = new Body[] {
            new Body(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            new Body(4.84143144246472090, -1.16032004402742839, -0.103622044471123109, 0.00166007664274403694, 0.00769901118419740425, -0.0000690460016972063023, 0.000954791938424326609),
            new Body(8.34336671824457987, 4.12479856412430479, -0.403523417114321381, -0.00276742510726862411, 0.00499852801234917238, 0.0000230417297573763929, 0.000285885980666130812)
        };
    }

    @Benchmark
    public double simulate() {
        Body[] local = copyBodies(bodies);
        for (int step = 0; step < steps; step++) {
            advance(local, 0.01);
        }
        return energy(local);
    }

    private static Body[] copyBodies(Body[] source) {
        Body[] copy = new Body[source.length];
        for (int i = 0; i < source.length; i++) {
            copy[i] = source[i].copy();
        }
        return copy;
    }

    private static void advance(Body[] bodies, double dt) {
        for (int i = 0; i < bodies.length; i++) {
            for (int j = i + 1; j < bodies.length; j++) {
                double dx = bodies[i].x - bodies[j].x;
                double dy = bodies[i].y - bodies[j].y;
                double dz = bodies[i].z - bodies[j].z;
                double distanceSquared = dx * dx + dy * dy + dz * dz;
                double distance = Math.sqrt(distanceSquared);
                double magnitude = dt / (distanceSquared * distance);

                bodies[i].vx -= dx * bodies[j].mass * magnitude;
                bodies[i].vy -= dy * bodies[j].mass * magnitude;
                bodies[i].vz -= dz * bodies[j].mass * magnitude;
                bodies[j].vx += dx * bodies[i].mass * magnitude;
                bodies[j].vy += dy * bodies[i].mass * magnitude;
                bodies[j].vz += dz * bodies[i].mass * magnitude;
            }
        }

        for (Body body : bodies) {
            body.x += dt * body.vx;
            body.y += dt * body.vy;
            body.z += dt * body.vz;
        }
    }

    private static double energy(Body[] bodies) {
        double energy = 0.0;
        for (int i = 0; i < bodies.length; i++) {
            energy += 0.5 * bodies[i].mass * ((bodies[i].vx * bodies[i].vx) + (bodies[i].vy * bodies[i].vy) + (bodies[i].vz * bodies[i].vz));
            for (int j = i + 1; j < bodies.length; j++) {
                double dx = bodies[i].x - bodies[j].x;
                double dy = bodies[i].y - bodies[j].y;
                double dz = bodies[i].z - bodies[j].z;
                double distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                energy -= (bodies[i].mass * bodies[j].mass) / distance;
            }
        }
        return energy;
    }

    private static final class Body {
        double x;
        double y;
        double z;
        double vx;
        double vy;
        double vz;
        double mass;

        Body(double x, double y, double z, double vx, double vy, double vz, double mass) {
            this.x = x;
            this.y = y;
            this.z = z;
            this.vx = vx;
            this.vy = vy;
            this.vz = vz;
            this.mass = mass;
        }

        Body copy() {
            return new Body(x, y, z, vx, vy, vz, mass);
        }
    }
}

