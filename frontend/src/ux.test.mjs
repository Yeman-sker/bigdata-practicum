import test from "node:test";
import assert from "node:assert/strict";
import { validateStyleMin } from "@maplibre/maplibre-gl-style-spec";
import { cityStyle } from "./map-style.ts";
import { buildScene, visibleRoutes, routeSummary, replayReady } from "./scene.ts";
import { matchesRisk, particleOffsets, latestRequestGate, availableSelection, hourAtIndex, nextAvailableHour, playbackDelay } from "./model.mjs";
import { readFileSync } from "node:fs";

const examples = JSON.parse(readFileSync(new URL("../../fixtures/day2/http-examples.json", import.meta.url)));
const fixture = (name) => structuredClone(examples[name].value);

test("restored Prism visual defaults keep original camera, light and full station rendering", () => {
  assert.deepEqual(validateStyleMin(cityStyle).map((error) => error.message), []);
  const source = readFileSync(new URL("./PrismMap.tsx", import.meta.url), "utf8");
  assert.match(source, /zoom: 14\.8/);
  assert.match(source, /pitch: 58/);
  assert.match(source, /const \[density, setDensity\] = useState\(1\)/);
  assert.match(source, /\(\) => !matchMedia\("\(prefers-reduced-motion: reduce\)"\)\.matches/);
  assert.match(source, /0\.08 \+ scan \* 0\.55/);
  assert.match(source, /for \(let i = 0; i < 4; i\+\+\)/);
  assert.doesNotMatch(source, /detailVisible|station-clusters|stationMapStyle|map-level|<details/);
  assert.match(source, /if \(!fitted &&/); // Preserve refresh camera stability.
  const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  assert.match(app, /useState<FlowFilter>\("all"\)/);
  assert.match(app, /setFlowFilter\("all"\)/);
  assert.match(app, /银白库存点 = 1 辆当前可用车/);
  assert.doesNotMatch(app, /总览聚合|街区小点|近景选中站点每个/);
});

test("risk filters partition statuses without treating stale, missing or invalid as healthy", () => {
  const statuses = ["SHORTAGE_RISK", "LOW_INVENTORY", "OVERFLOW_RISK", "HIGH_INVENTORY", "HEALTHY", "STALE_DATA", "INVALID_DATA", "INSUFFICIENT_DATA", "SERVICE_UNAVAILABLE"];
  assert.equal(statuses.filter((s) => matchesRisk(s, "all")).length, 9);
  assert.deepEqual(statuses.filter((s) => matchesRisk(s, "shortage")), statuses.slice(0, 2));
  assert.deepEqual(statuses.filter((s) => matchesRisk(s, "full")), statuses.slice(2, 4));
  assert.deepEqual(statuses.filter((s) => matchesRisk(s, "issue")), statuses.slice(5));
  assert.equal(matchesRisk("UNKNOWN", "issue"), true);
  assert.equal(matchesRisk("NOT_APPLICABLE", "issue"), false);
});

test("all station inventories remain exact in current and forecast views", () => {
  const live = fixture("live");
  const current = buildScene(live, "current", 0, []);
  const forecast = buildScene(live, "forecast", 0, []);
  for (const station of current.stations.values()) {

    assert.equal(particleOffsets(station.id, station.inventory).length, station.inventory);
    assert.equal(forecast.stations.get(station.id).inventory, station.inventory);
  }
  for (const name of ["stale", "replay"]) {
    for (const station of buildScene(fixture(name), "forecast", 0, []).stations.values())
      assert.equal(station.inventory, null);
  }
  live.stations[0].num_bikes_available = 0;
  const zero = buildScene(live, "current", 0, []).stations.get(live.stations[0].station_id);

  assert.deepEqual(particleOffsets(zero.id, zero.inventory), []);
  live.stations[0].current_status = "INVALID_DATA";
  assert.equal(buildScene(live, "current", 0, []).stations.get(zero.id).inventory, null);
  const noBaseline = buildScene(fixture("no_baseline"), "forecast", 0, []);
  assert.ok([...noBaseline.stations.values()].some((s) => s.inventory !== null));
});

test("OD visibility reports full denominators and never truncates or rewrites counts", () => {
  const stations = new Map([
    ["a", { id: "a", coordinate: [-74, 40.7] }],
    ["b", { id: "b", coordinate: [-73.99, 40.7] }],
    ["missing", { id: "missing", coordinate: null }],
  ]);
  const flows = Array.from({ length: 3000 }, (_, i) => ({ id: String(i), from: i % 2 ? "a" : "b", to: i % 3 ? "b" : "missing", quantity: i + 1 }));
  const scene = { kind: "replay", stations, flows };
  const snapshot = structuredClone(flows);
  assert.equal(visibleRoutes(scene, "all", null).length, 3000);
  const top = visibleRoutes(scene, "top", null);
  assert.equal(top.length, 20);
  assert.equal(top[0].quantity, 3000);
  assert.equal(top.at(-1).quantity, 2981);
  assert.equal(routeSummary(scene, "top", null).total, 3000);
  assert.equal(routeSummary(scene, "top", null).totalRides, 4501500);
  assert.equal(visibleRoutes(scene, "hidden", "a").length, 0);
  assert.equal(visibleRoutes(scene, "selected", null).length, 0);
  assert.equal(visibleRoutes(scene, "selected", "a").length, 1500);
  const all = routeSummary(scene, "all", null);
  assert.equal(all.total, 3000);
  assert.equal(all.shown, 2000);
  assert.equal(all.totalRides, 4501500);
  assert.equal(all.shownRides, flows.filter((f) => f.to !== "missing").reduce((n, f) => n + f.quantity, 0));
  const hidden = routeSummary(scene, "hidden", null);
  assert.equal(hidden.totalRides, all.totalRides);
  assert.equal(hidden.shownRides, 0);
  assert.deepEqual(flows, snapshot);
  const live = fixture("live");
  const dispatch = buildScene(live, "dispatch", 0, live.suggestions);
  assert.deepEqual(visibleRoutes(dispatch, "hidden", null), dispatch.dispatches);
  assert.ok(buildScene(fixture("replay"), "current", 0, []).flows.every((flow) => flow.top));
});

test("OD detail pagination remains independent of map visibility controls", () => {
  const source = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  assert.match(source, /const listedFlows = scene\?\.kind === "replay" \? scene\.flows : \[\];/);
  assert.match(source, /listedFlows\.slice\(flowPage \* 50, \(flowPage \+ 1\) \* 50\)/);
  assert.match(source, /OD 完整明细/);
});

test("request gate cancels stale success/failure and metadata continuations; last request wins", async () => {
  const gate = latestRequestGate();
  const first = gate.start();
  let finish;
  const late = new Promise((resolve) => { finish = resolve; }).then(() => first.isCurrent() ? "old" : null);
  const second = gate.start();
  assert.equal(first.signal.aborted, true);
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), true);
  finish();
  assert.equal(await late, null);
  // A retry is a new request even when its requested hour matches the failed one.
  const retry = gate.start();
  assert.equal(second.isCurrent(), false);
  assert.equal(second.signal.aborted, true);
  assert.equal(retry.isCurrent(), true);
  gate.cancel(); // Mode switch/unmount also invalidates cached synchronous results.
  assert.equal(retry.isCurrent(), false);
  assert.equal(retry.signal.aborted, true);
  assert.equal(gate.start().isCurrent(), true);
});

test("availability selections and replay readiness separate displayed time from requested/preview time", () => {
  const dates = [{ service_date: "2025-01-15", hours: [0, 4, 8, 23] }, { service_date: "2025-01-16", hours: [3, 9] }, { service_date: "2025-01-17", hours: [] }];
  const committed = { mode: "replay", service_date: "2025-01-15", hour: 4 };
  const target = availableSelection(dates, "2025-01-16");
  assert.deepEqual(target, { serviceDate: "2025-01-16", hour: 3 });
  assert.equal(availableSelection(dates, "2025-01-17"), null);
  assert.equal(availableSelection(dates, "2025-01-18"), null);
  assert.equal(hourAtIndex(dates[0].hours, 2), 8); // Slider indices cannot request an unavailable 02:00.
  assert.equal(hourAtIndex([], 0), null);
  assert.equal(hourAtIndex(dates[0].hours, 99), 23);
  assert.equal(replayReady(committed, target, false), false); // Failed new target never advances old map.
  assert.equal(replayReady(committed, { serviceDate: committed.service_date, hour: 4 }, true), false);
  assert.equal(replayReady(committed, { serviceDate: committed.service_date, hour: 4 }, false), true);
  assert.equal(nextAvailableHour(dates[0].hours, 4), 8);
  assert.equal(nextAvailableHour(dates[0].hours, 23), null);
  assert.deepEqual([0.5, 1, 2, 5].map(playbackDelay), [4000, 2000, 1000, 400]);
});

test("map-first panel morph preserves business controls and accessible state", () => {
  const app = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  const map = readFileSync(new URL("./PrismMap.tsx", import.meta.url), "utf8");
  assert.match(app, /aria-expanded=\{panelOpen\}/);
  assert.match(app, /aria-controls="operations-panel-body"/);
  assert.match(app, /className="pane-toggle"/);
  assert.match(app, /className="toggle-mark"/);
  assert.match(app, /inert=\{!panelOpen\}/);
  assert.match(app, /panelOpen=\{panelOpen\}/);
  assert.match(app, /className="legend-detail"/);
  assert.match(app, /aria-label="回放倍速"/);
  assert.match(app, /const usesFixture = fixtureParameter !== null;/);
  assert.doesNotMatch(app, /import\.meta\.env\.DEV && !pageQuery\.has\("api"\)/);
  assert.match(css, /\.app\.panel-collapsed \.work-pane/);
  assert.match(css, /\.pane-toggle \.toggle-mark/);
  assert.match(css, /\.app\.panel-collapsed \.pane-toggle \{[^}]*width: 44px/);
  assert.match(css, /\.operations-panel-body/);
  assert.match(map, /function rightPadding\(\)/);
  assert.match(map, /useEffect\(\(\) => reframeRef\.current\(\), \[props\.panelOpen\]\)/);
  assert.doesNotMatch(map, /props\.scene,\s*props\.panelOpen/);
  assert.doesNotMatch(map, /width > 720 \? 380 : 40/);
});
