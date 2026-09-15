import test from "node:test";
import assert from "node:assert/strict";
import {
  canDrawForecast,
  canDrawInventory,
  isExpiredAt,
  isMapExpired,
  isTraversalStatus,
  nextAvailableHour,
  particleOffsets,
  playbackDelay,
  sortStations,
} from "./model.mjs";

test("UI model preserves contract semantics", () => {
  const particles = particleOffsets("5484.09", 6);
  assert.equal(particles.length, 6);
  assert.deepEqual(particles, particleOffsets("5484.09", 6));
  assert.notDeepEqual(particles, particleOffsets("4199.12", 6));

  const stations = [
    { station_id: "b", lat: 40.7, lon: -74, current_status: "HEALTHY", forecast_status: "SHORTAGE_RISK" },
    { station_id: "a", lat: 40.7, lon: -74.001, current_status: "SHORTAGE_RISK", forecast_status: "HEALTHY" },
    { station_id: "x", lat: null, lon: null, current_status: "INVALID_DATA", forecast_status: "INVALID_DATA" },
  ];
  assert.deepEqual(sortStations(stations).map(({ station_id }) => station_id), ["a", "x", "b"]);
  assert.deepEqual(sortStations(stations, "dispatch").map(({ station_id }) => station_id), ["b", "x", "a"]);
  assert.deepEqual(sortStations(stations, "current", () => "STALE_DATA").map(({ station_id }) => station_id), ["a", "b", "x"]);
  assert.equal(isTraversalStatus("HEALTHY", "live"), false);
  assert.equal(isTraversalStatus("SHORTAGE_RISK", "live"), true);
  assert.equal(isTraversalStatus("NOT_APPLICABLE", "replay"), true);

  assert.equal(nextAvailableHour([8, 10, 12], 10), 12);
  assert.equal(nextAvailableHour([8, 10, 12], 12), null);
  assert.equal(playbackDelay(5), 400);

  const recorded = {
    clock_mode: "recorded",
    as_of_utc: "2025-02-05T13:04:00Z",
    expires_at_utc: "2025-02-05T13:03:00Z",
  };
  assert.equal(isMapExpired(recorded, 0), true);
  assert.equal(isExpiredAt(recorded, "2025-02-05T13:05:00Z", 0), false);
  const wall = { ...recorded, clock_mode: "wall", as_of_utc: "2025-02-05T13:00:00Z" };
  assert.equal(isExpiredAt(wall, "2025-02-05T13:03:00Z", 179_999), false);
  assert.equal(isExpiredAt(wall, "2025-02-05T13:03:00Z", 180_000), true);
  assert.equal(canDrawInventory({ num_bikes_available: 6, current_status: "SHORTAGE_RISK" }), true);
  assert.equal(canDrawInventory({ num_bikes_available: -1, current_status: "INVALID_DATA" }), false);
  assert.equal(canDrawForecast({ num_bikes_available: 6, current_status: "HEALTHY", projected_bikes_1h: 4, forecast_status: "SHORTAGE_RISK" }), true);
  assert.equal(canDrawForecast({ num_bikes_available: 6, current_status: "HEALTHY", projected_bikes_1h: null, forecast_status: "INSUFFICIENT_DATA" }), false);
});
