using BenchmarkDotNet.Attributes;

namespace PerfBenchmarks;

[MemoryDiagnoser]
public class NBodyBenchmarks
{
    private Body[] _bodies = [];

    [Params(1_000, 10_000)]
    public int Steps { get; set; }

    [GlobalSetup]
    public void Setup()
    {
        _bodies =
        [
            new(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            new(4.84143144246472090, -1.16032004402742839, -0.103622044471123109, 0.00166007664274403694, 0.00769901118419740425, -0.0000690460016972063023, 0.000954791938424326609),
            new(8.34336671824457987, 4.12479856412430479, -0.403523417114321381, -0.00276742510726862411, 0.00499852801234917238, 0.0000230417297573763929, 0.000285885980666130812)
        ];
    }

    [Benchmark]
    public double Simulate()
    {
        var bodies = CopyBodies(_bodies);
        for (var step = 0; step < Steps; step++)
        {
            Advance(bodies, 0.01);
        }

        return Energy(bodies);
    }

    private static Body[] CopyBodies(Body[] bodies)
    {
        var copy = new Body[bodies.Length];
        Array.Copy(bodies, copy, bodies.Length);
        return copy;
    }

    private static void Advance(Body[] bodies, double dt)
    {
        for (var i = 0; i < bodies.Length; i++)
        {
            for (var j = i + 1; j < bodies.Length; j++)
            {
                var dx = bodies[i].X - bodies[j].X;
                var dy = bodies[i].Y - bodies[j].Y;
                var dz = bodies[i].Z - bodies[j].Z;
                var distanceSquared = dx * dx + dy * dy + dz * dz;
                var distance = Math.Sqrt(distanceSquared);
                var magnitude = dt / (distanceSquared * distance);

                bodies[i].Vx -= dx * bodies[j].Mass * magnitude;
                bodies[i].Vy -= dy * bodies[j].Mass * magnitude;
                bodies[i].Vz -= dz * bodies[j].Mass * magnitude;
                bodies[j].Vx += dx * bodies[i].Mass * magnitude;
                bodies[j].Vy += dy * bodies[i].Mass * magnitude;
                bodies[j].Vz += dz * bodies[i].Mass * magnitude;
            }
        }

        foreach (ref var body in bodies.AsSpan())
        {
            body.X += dt * body.Vx;
            body.Y += dt * body.Vy;
            body.Z += dt * body.Vz;
        }
    }

    private static double Energy(Body[] bodies)
    {
        var energy = 0.0;
        for (var i = 0; i < bodies.Length; i++)
        {
            energy += 0.5 * bodies[i].Mass * ((bodies[i].Vx * bodies[i].Vx) + (bodies[i].Vy * bodies[i].Vy) + (bodies[i].Vz * bodies[i].Vz));
            for (var j = i + 1; j < bodies.Length; j++)
            {
                var dx = bodies[i].X - bodies[j].X;
                var dy = bodies[i].Y - bodies[j].Y;
                var dz = bodies[i].Z - bodies[j].Z;
                var distance = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                energy -= (bodies[i].Mass * bodies[j].Mass) / distance;
            }
        }

        return energy;
    }

    private struct Body(double x, double y, double z, double vx, double vy, double vz, double mass)
    {
        public double X = x;
        public double Y = y;
        public double Z = z;
        public double Vx = vx;
        public double Vy = vy;
        public double Vz = vz;
        public double Mass = mass;
    }
}

