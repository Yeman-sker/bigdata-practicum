import test from "node:test";
import assert from "node:assert/strict";
import {
  PULSES_PER_ROUTE,
  gaugeRatio,
  glowRadius,
  rgba,
  groundAxes,
  placeLabel,
  scanIntensity,
  SCAN_PERIOD_MS,
  zoomScale,
} from "./map-fx.ts";

test("inventory gauge is bikes over capacity and never invents a value", () => {
  assert.equal(gaugeRatio(6, 40), 0.15);
  assert.equal(gaugeRatio(0, 40), 0);
  assert.equal(gaugeRatio(44, 40), 1);
  assert.equal(gaugeRatio(null, 40), null);
  assert.equal(gaugeRatio(6, null), null);
  assert.equal(gaugeRatio(6, 0), null);
  assert.equal(gaugeRatio(-1, 40), null);
});

test("ground axes point east and north by the requested distance", () => {
  const { east, north } = groundAxes([-74, 40.7], 70);
  assert.equal(east[1], 40.7);
  assert.ok(east[0] > -74 && east[0] - -74 < 0.001);
  assert.equal(north[0], -74);
  assert.ok(Math.abs((north[1] - 40.7) * 110540 - 70) < 1e-6);
});

test("decorative scan and scale stay bounded", () => {
  assert.equal(PULSES_PER_ROUTE, 4);
  for (let t = 0; t < SCAN_PERIOD_MS * 2; t += 97)
    for (const position of [0, 0.3, 0.7, 1]) {
      const value = scanIntensity(position, t);
      assert.ok(value >= 0 && value <= 1);
    }
  assert.equal(zoomScale(14.8), 1);
  assert.ok(zoomScale(5) >= 0.5 && zoomScale(22) <= 1.7);
});

test("route labels are nudged apart instead of overlapping", () => {
  const placed = [];
  const first = placeLabel({ x: 0, y: 100, w: 80, h: 22 }, placed);
  const second = placeLabel({ x: 10, y: 100, w: 80, h: 22 }, placed);
  assert.deepEqual(first, { x: 0, y: 100, w: 80, h: 22 });
  assert.ok(second.y + second.h <= first.y || second.y >= first.y + first.h);
});

test("glow buckets and memoised colors are stable for the frame loop", () => {
  assert.equal(glowRadius(0.2), 1);
  assert.equal(glowRadius(4.2), 4);
  assert.equal(glowRadius(4.26), 4.5);
  assert.equal(rgba("#f2edf8", 0.5), "rgba(242,237,248,0.5)");
  assert.equal(rgba("#f2edf8", 0.5), rgba("#f2edf8", 0.5));
});
