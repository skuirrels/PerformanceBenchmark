package perfbench

import (
	"math"
	"testing"
)

type body struct {
	x, y, z    float64
	vx, vy, vz float64
	mass       float64
}

var initialBodies = []body{
	{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0},
	{4.84143144246472090, -1.16032004402742839, -0.103622044471123109, 0.00166007664274403694, 0.00769901118419740425, -0.0000690460016972063023, 0.000954791938424326609},
	{8.34336671824457987, 4.12479856412430479, -0.403523417114321381, -0.00276742510726862411, 0.00499852801234917238, 0.0000230417297573763929, 0.000285885980666130812},
}

func BenchmarkNBody1000(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = simulateNBody(1000)
	}
}

func BenchmarkNBody10000(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = simulateNBody(10000)
	}
}

func simulateNBody(steps int) float64 {
	bodies := append([]body(nil), initialBodies...)
	for step := 0; step < steps; step++ {
		advance(bodies, 0.01)
	}
	return energy(bodies)
}

func advance(bodies []body, dt float64) {
	for i := range bodies {
		for j := i + 1; j < len(bodies); j++ {
			dx := bodies[i].x - bodies[j].x
			dy := bodies[i].y - bodies[j].y
			dz := bodies[i].z - bodies[j].z
			distanceSquared := dx*dx + dy*dy + dz*dz
			distance := math.Sqrt(distanceSquared)
			magnitude := dt / (distanceSquared * distance)

			bodies[i].vx -= dx * bodies[j].mass * magnitude
			bodies[i].vy -= dy * bodies[j].mass * magnitude
			bodies[i].vz -= dz * bodies[j].mass * magnitude
			bodies[j].vx += dx * bodies[i].mass * magnitude
			bodies[j].vy += dy * bodies[i].mass * magnitude
			bodies[j].vz += dz * bodies[i].mass * magnitude
		}
	}

	for i := range bodies {
		bodies[i].x += dt * bodies[i].vx
		bodies[i].y += dt * bodies[i].vy
		bodies[i].z += dt * bodies[i].vz
	}
}

func energy(bodies []body) float64 {
	total := 0.0
	for i := range bodies {
		total += 0.5 * bodies[i].mass * ((bodies[i].vx * bodies[i].vx) + (bodies[i].vy * bodies[i].vy) + (bodies[i].vz * bodies[i].vz))
		for j := i + 1; j < len(bodies); j++ {
			dx := bodies[i].x - bodies[j].x
			dy := bodies[i].y - bodies[j].y
			dz := bodies[i].z - bodies[j].z
			distance := math.Sqrt(dx*dx + dy*dy + dz*dz)
			total -= (bodies[i].mass * bodies[j].mass) / distance
		}
	}
	return total
}
