import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildScene, replayReady } from "./scene.ts";
import { parseMap, parseAvailability, parseHistory } from "./parse.ts";
const examples = JSON.parse(
  readFileSync(
    new URL("../../fixtures/day2/http-examples.json", import.meta.url),
  ),
);
const fixture = (name) => structuredClone(examples[name].value);

test("Prism keeps exact inventory, expiry, geography and replay isolation", () => {
  const live = parseMap(fixture("live"));
  const current = buildScene(live, "current", 0, live.suggestions);
  const forecast = buildScene(live, "forecast", 0, live.suggestions);
  assert.equal(current.kind, "live");
  assert.deepEqual(
    [...current.stations.values()].map((s) => s.inventory),
    [34, 6],
  );
  assert.deepEqual(
    [...forecast.stations.values()].map((s) => s.inventory),
    [34, 6],
  );
  assert.deepEqual(current.stations.get("4199.12").coordinate, [-74.001, 40.7]);
  const dispatch = buildScene(live, "dispatch", 0, live.suggestions);
  assert.equal(dispatch.dispatches[0].quantity, 8);
  assert.equal(dispatch.dispatches[0].label, "8 辆 · 84 m");
  assert.deepEqual(buildScene(live, "dispatch", 0, []).dispatches, []);
  const stale = buildScene(parseMap(fixture("stale")), "current", 0, []);
  assert.ok([...stale.stations.values()].every((s) => s.inventory === null));
  live.stations[0].lat = null;
  assert.equal(
    buildScene(live, "current", 0, []).stations.get("4199.12").coordinate,
    null,
  );
  const replay = parseMap(fixture("replay"));
  const scene = buildScene(replay, "dispatch", 0, live.suggestions);
  assert.equal(scene.kind, "replay");
  assert.ok([...scene.stations.values()].every((s) => s.inventory === null));
  assert.equal("dispatches" in scene, false);
  assert.equal(scene.flows[0].quantity, replay.flows[0].ride_count);
  assert.ok(scene.flows.length > 0);
  const selection = { serviceDate: replay.service_date, hour: replay.hour };
  assert.equal(replayReady(replay, selection, true), false);
  assert.equal(
    replayReady(replay, { ...selection, hour: replay.hour + 1 }, false),
    false,
  );
  assert.equal(replayReady(replay, selection, false), true);
});

test("API boundary accepts contract nulls and rejects malformed successful payloads", () => {
  for (const name of ["live", "replay", "stale", "no_baseline", "empty_live"])
    assert.doesNotThrow(() => parseMap(fixture(name)));
  for (const name of ["availability", "empty_availability"])
    assert.doesNotThrow(() => parseAvailability(fixture(name)));
  for (const name of ["history", "empty_history"])
    assert.doesNotThrow(() => parseHistory(fixture(name)));
  const invalid = fixture("live");
  invalid.stations[0].lat = 100;
  assert.throws(() => parseMap(invalid));
  invalid.stations[0].lat = 40.7;
  invalid.stations[0].num_bikes_available = -1;
  invalid.stations[0].current_status = "INVALID_DATA";
  assert.equal(
    buildScene(parseMap(invalid), "current", 0, []).stations.get("4199.12")
      .inventory,
    null,
  );
  assert.throws(() => parseMap({ stations: [] }));
  assert.throws(() => parseHistory({ profile: null }));
});

test("map parser rejects incompatible modes and malformed fields", () => {
  const malformed = [
    [
      "live",
      (value) => {
        value.contract_version = "1.0";
      },
    ],
    [
      "live",
      (value) => {
        value.data_origin = "HISTORICAL";
      },
    ],
    [
      "live",
      (value) => {
        value.dataset_id = value.snapshot_id;
      },
    ],
    [
      "live",
      (value) => {
        value.snapshot_id = null;
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].current_status = "NOT_APPLICABLE";
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].forecast_status = "NOT_APPLICABLE";
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].is_renting = 1;
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].sample_days = 0;
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].num_bikes_available = 1.5;
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].current_reason = "invented";
      },
    ],
    [
      "live",
      (value) => {
        value.stations[0].fill_ratio = 1.1;
      },
    ],
    [
      "live",
      (value) => {
        value.suggestions[0].distance_meters = 0.5;
      },
    ],
    [
      "live",
      (value) => {
        delete value.suggestions[0].from_surplus;
      },
    ],
    [
      "live",
      (value) => {
        delete value.suggestions[0].to_deficit;
      },
    ],
    [
      "live",
      (value) => {
        delete value.suggestions[0].generated_at_utc;
      },
    ],
    [
      "live",
      (value) => {
        value.service_date = "2025-02-30";
      },
    ],
    [
      "live",
      (value) => {
        value.as_of_utc = "2025-02-30T13:00:00Z";
      },
    ],
    [
      "live",
      (value) => {
        value.as_of_utc = "2025-02-05T13:00:00+00:00";
      },
    ],
    [
      "live",
      (value) => {
        value.flows = fixture("replay").flows;
      },
    ],
    [
      "replay",
      (value) => {
        value.data_origin = "GBFS_REPLAY";
      },
    ],
    [
      "replay",
      (value) => {
        value.hour = 24;
      },
    ],
    [
      "replay",
      (value) => {
        value.stations[0].num_bikes_available = 0;
      },
    ],
    [
      "replay",
      (value) => {
        value.stations[0].current_status = "HEALTHY";
      },
    ],
    [
      "replay",
      (value) => {
        value.stations[0].forecast_for_utc = value.as_of_utc;
      },
    ],
    [
      "replay",
      (value) => {
        value.suggestions = fixture("live").suggestions;
      },
    ],
    [
      "replay",
      (value) => {
        value.flows[0].ride_count = 0;
      },
    ],
    [
      "replay",
      (value) => {
        value.flows[0].from_station_id = "missing-endpoint";
      },
    ],
    [
      "replay",
      (value) => {
        value.flows.push(value.flows[0]);
      },
    ],
    [
      "live",
      (value) => {
        value.unexpected = true;
      },
    ],
  ];
  for (const [name, mutate] of malformed) {
    const value = fixture(name);
    mutate(value);
    assert.throws(() => parseMap(value), `${name}: ${mutate}`);
  }
  const forecast = fixture("live");
  forecast.stations[0].projected_bikes_1h = -2.4;
  assert.equal(parseMap(forecast).stations[0].projected_bikes_1h, -2.4);
});

test("availability and history reject inconsistent dates, hours, identities and numeric rows", () => {
  for (const mutate of [
    (value) => {
      delete value.contract_version;
    },
    (value) => {
      value.dataset_id = "wrong-hash";
    },
    (value) => {
      value.source_months.push(value.source_months[0]);
    },
    (value) => {
      value.source_months = ["2025-13"];
    },
    (value) => {
      value.dates.push(value.dates[0]);
    },
    (value) => {
      value.dates.reverse();
    },
    (value) => {
      value.dates[0].hours = [1, 1];
    },
    (value) => {
      value.dates[0].hours = [1, 0];
    },
    (value) => {
      value.dates[0].hours = [1.5];
    },
    (value) => {
      value.dates[0].service_date = "2025-02-30";
    },
  ]) {
    const value = fixture("availability");
    mutate(value);
    assert.throws(() => parseAvailability(value), String(mutate));
  }
  const expected = {
    stationId: "5484.09",
    dayOfWeek: 3,
    serviceDate: "2025-01-15",
  };
  assert.doesNotThrow(() => parseHistory(fixture("history"), expected));
  for (const field of ["profile_start_date", "profile_end_date"]) {
    const partialCoverage = fixture("history");
    partialCoverage[field] = null;
    assert.doesNotThrow(() => parseHistory(partialCoverage, expected));
  }
  for (const mutate of [
    (value) => {
      value.contract_version = "2.0";
    },
    (value) => {
      value.station_id = "another-station";
    },
    (value) => {
      value.day_of_week = 4;
    },
    (value) => {
      value.service_date = "2025-01-22";
    },
    (value) => {
      value.service_date = null;
    },
    (value) => {
      value.profile_start_date = "2025-02-30";
    },
    (value) => {
      value.profile_start_date = "2026-01-01";
    },
    (value) => {
      value.profile.pop();
    },
    (value) => {
      value.actual.pop();
    },
    (value) => {
      value.profile.reverse();
    },
    (value) => {
      value.actual[0].hour = 1;
    },
    (value) => {
      value.profile[0].avg_inbound = -1;
    },
    (value) => {
      value.profile[0].sample_days = 1.5;
    },
    (value) => {
      delete value.profile[0].median_net_flow;
    },
    (value) => {
      value.actual[0].outbound_rides = 1.5;
    },
    (value) => {
      value.actual[0].electric_outbound = -1;
    },
    (value) => {
      delete value.actual[0].casual_outbound;
    },
  ]) {
    const value = fixture("history");
    mutate(value);
    assert.throws(() => parseHistory(value, expected), String(mutate));
  }
  const actualOnly = fixture("history");
  actualOnly.profile = [];
  assert.equal(parseHistory(actualOnly, expected).actual.length, 24);
  const noDate = fixture("history");
  noDate.service_date = null;
  noDate.actual = [];
  assert.doesNotThrow(() =>
    parseHistory(noDate, { ...expected, serviceDate: null }),
  );
});
