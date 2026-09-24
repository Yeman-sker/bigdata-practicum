import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as CityMap, Marker } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { cityStyle } from "./map-style";
import { particleOffsets, matchesRisk } from "./model.mjs";
import { visibleRoutes } from "./scene";
import { PARTICLE_STRIDE, createBikeLayer } from "./bike-layer";
import {
  PARTICLE_METERS_X,
  PARTICLE_METERS_Y,
  PULSES_PER_ROUTE,
  RING_METERS,
  createGlowCache,
  glowRadius,
  cubicPoint,
  gaugeRatio,
  groundAxes,
  orbitSpeed,
  placeLabel,
  rgba,
  routeWidth,
  scanIntensity,
  smoothstep,
  zoomScale,
} from "./map-fx";
import type { RiskFilter, FlowFilter } from "./scene";
import type { Scene, SceneRoute, SceneStation } from "./scene";

maplibregl.setWorkerUrl(workerUrl);

type Point = { x: number; y: number };
type Coordinate = [number, number];
type Props = {
  scene: Scene | null;
  panelOpen: boolean;
  riskFilter: RiskFilter;
  flowFilter: FlowFilter;
  selectedStationId: string | null;
  selectedSuggestionId: string | null;
  onSelectStation: (id: string) => void;
  onBackground: () => void;
};
const camera = {
  center: [-74.006, 40.706] as Coordinate,
  zoom: 14.8,
  pitch: 58,
  bearing: 28,
};
const statusNames: Record<string, string> = {
  SHORTAGE_RISK: "缺车",
  LOW_INVENTORY: "偏低",
  HEALTHY: "正常",
  HIGH_INVENTORY: "偏高",
  OVERFLOW_RISK: "满桩",
  SERVICE_UNAVAILABLE: "停服",
  STALE_DATA: "过期",
  INVALID_DATA: "异常",
  INSUFFICIENT_DATA: "数据不足",
  NOT_APPLICABLE: "历史站点",
};
function riskColor(status: string) {
  if (["SHORTAGE_RISK", "LOW_INVENTORY"].includes(status)) return "#ffad80";
  if (["OVERFLOW_RISK", "HIGH_INVENTORY"].includes(status)) return "#f28fca";
  if (status === "HEALTHY") return "#eeeef4";
  if (status === "NOT_APPLICABLE") return "#c6bcff";
  return "#9893a3";
}
const majorRoads = new Set(["motorway", "trunk", "primary", "secondary"]);
function coordinate(value: number[]): Coordinate {
  return [value[0], value[1]];
}

export function PrismMap(props: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bikeCanvasRef = useRef<HTMLCanvasElement>(null);
  const markCanvasRef = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<CityMap | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  const reframeRef = useRef<() => void>(() => {});
  const homeRef = useRef<() => void>(() => {});
  const [attempt, setAttempt] = useState(0);
  const [mapState, setMapState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [motion, setMotion] = useState(
    () => !matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [density, setDensity] = useState(1);
  const [densityOpen, setDensityOpen] = useState(false);
  const densityButtonRef = useRef<HTMLButtonElement>(null);
  const currentRef = useRef({ ...props, motion, density });
  currentRef.current = { ...props, motion, density };

  useEffect(() => {
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setMotion(!query.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);

  useEffect(() => {
    const hostElement = hostRef.current;
    const canvasElement = canvasRef.current;
    const context = canvasElement?.getContext("2d");
    const markContext = markCanvasRef.current?.getContext("2d");
    const bikeCanvas = bikeCanvasRef.current;
    if (!hostElement || !canvasElement || !context || !markContext || !bikeCanvas)
      return;
    const host = hostElement,
      canvas = canvasElement,
      ctx = context,
      markCtx = markContext;
    // Offscreen layers for static station art (see renderStationLayers).
    const ringLayer = document.createElement("canvas");
    const coreLayer = document.createElement("canvas");
    const ringCtx = ringLayer.getContext("2d")!;
    const coreCtx = coreLayer.getContext("2d")!;
    let bikes = createBikeLayer(bikeCanvas, [242 / 255, 237 / 255, 248 / 255]);
    // If the GPU context is lost, fall back to the identical canvas path.
    const bikesLost = () => {
      bikes = null;
      dirty = true;
      schedule();
    };
    bikeCanvas.addEventListener("webglcontextlost", bikesLost);
    let particleData = new Float32Array(0);
    let particleCount = 0;
    let marksDirty = true;
    let map: CityMap;
    let ready = false;
    let failed = false;
    let disposed = false;
    let frame = 0;
    let lastPaint = 0;
    let time = 0;
    let dirty = true;
    let width = host.clientWidth;
    let height = host.clientHeight;
    let fitted = false;
    let lastSelectedId: string | null = null;
    let lastSuggestionId: string | null = null;
    let lastRightPadding = 0;
    let highlightedStations = new Set<string>();
    let roads: { line: Coordinate[]; major: boolean }[] = [];
    let buildingPoints: Coordinate[] = [];
    let scale = 1;
    let pixelRatio = Math.min(devicePixelRatio, 2);
    let glow = createGlowCache(pixelRatio);
    let projectedBuildings: (Point & { sweep: number })[] = [];
    let projectedRoads: {
      points: Point[];
      cumulative: number[];
      length: number;
      major: boolean;
    }[] = [];
    let projectedStations: {
      station: SceneStation;
      point: Point;
      u: Point;
      v: Point;
      particles: { radius: number; angle: number; speed: number }[];
      gauge: number | null;
    }[] = [];
    let paths: { route: SceneRoute; a: Point; b: Point; c: Point; d: Point }[] =
      [];
    // Idle station diamonds are painted on the canvas (same size, colors and
    // stacking as the DOM markers). A real <button> marker is attached only for
    // stations that are hovered, selected/highlighted or focused, so the CSS
    // hover scale and glass tooltip still play. 2,520 always-attached DOM
    // markers cost more than the whole canvas. `state` memoises written attributes.
    const markers = new Map<
      string,
      {
        marker: Marker;
        button: HTMLButtonElement;
        attached: boolean;
        state: string;
        lngLat: Coordinate;
        order: number;
      }
    >();
    // Stations near the pointer get real DOM markers *before* the pointer enters
    // their 44px button, so the CSS hover transition still animates.
    let nearIds = new Set<string>();
    const lingering = new Set<string>();
    let hostRect = host.getBoundingClientRect();
    // Polar particle layout per station and bike count; stable between moves.
    const layouts = new Map<string, { radius: number; angle: number; speed: number }[]>();
    function orbitLayout(id: string, count: number) {
      const key = `${id}:${count}`;
      let layout = layouts.get(key);
      if (!layout) {
        if (layouts.size > 8000) layouts.clear();
        layout = particleOffsets(id, count).map((offset: Point) => {
          const radius = Math.hypot(offset.x, offset.y);
          return {
            radius,
            angle: Math.atan2(offset.y, offset.x),
            speed: orbitSpeed(radius),
          };
        });
        layouts.set(key, layout!);
      }
      return layout!;
    }
    function attach(entry: { marker: Marker; attached: boolean; order: number }) {
      if (entry.attached) return;
      entry.marker.addTo(map);
      entry.attached = true;
      // Keep attached markers in scene order so stacking and click targets
      // match the painted diamonds (and the former all-DOM markers).
      const element = entry.marker.getElement();
      element.dataset.order = String(entry.order);
      let next = element.parentElement?.firstElementChild ?? null;
      while (next && !(next !== element && next.classList.contains("maplibregl-marker") && Number((next as HTMLElement).dataset.order) > entry.order))
        next = next.nextElementSibling;
      if (next) element.parentElement!.insertBefore(element, next);
    }
    function detach(entry: { marker: Marker; attached: boolean }) {
      if (entry.attached) {
        entry.marker.remove();
        entry.attached = false;
      }
    }
    function syncMarkers() {
      for (const [id, entry] of markers) {
        if (
          nearIds.has(id) ||
          lingering.has(id) ||
          highlightedStations.has(id) ||
          document.activeElement === entry.button
        )
          attach(entry);
        else detach(entry);
      }
    }
    setMapState("loading");
    try {
      map = new maplibregl.Map({
        container: host,
        style: cityStyle,
        ...camera,
        attributionControl: false,
        canvasContextAttributes: { antialias: true },
        minZoom: 10,
        maxZoom: 19,
        maxPitch: 70,
      });
      mapRef.current = map;
    } catch {
      setMapState("error");
      return;
    }
    const timeout = window.setTimeout(() => {
      if (!ready) setMapState("error");
    }, 18000);
    const fail = () => {
      if (!disposed) {
        failed = true;
        setMapState("error");
      }
    };
    const contextLost = (event: Event) => {
      event.preventDefault();
      ready = false;
      fail();
    };
    map.getCanvas().addEventListener("webglcontextlost", contextLost);

    function rightPadding() {
      if (window.innerWidth <= 640) return 40;
      if (!currentRef.current.panelOpen) return 118;
      return window.innerWidth <= 900 ? 336 : 368;
    }

    function home() {
      const located = [
        ...(currentRef.current.scene?.stations.values() ?? []),
      ].flatMap((station) => (station.coordinate ? [station.coordinate] : []));
      if (!located.length) {
        map.jumpTo(camera);
        return;
      }
      const west = Math.min(...located.map((p) => p[0]));
      const east = Math.max(...located.map((p) => p[0]));
      const south = Math.min(...located.map((p) => p[1]));
      const north = Math.max(...located.map((p) => p[1]));
      const center = [(west + east) / 2, (south + north) / 2];
      const bounds: [Coordinate, Coordinate] = [
        [Math.min(west, center[0] - 0.009), Math.min(south, center[1] - 0.003)],
        [Math.max(east, center[0] + 0.009), Math.max(north, center[1] + 0.012)],
      ];
      map.fitBounds(bounds, {
        maxZoom: 14.9,
        pitch: 58,
        bearing: 28,
        padding: {
          top: 100,
          bottom: 150,
          left: 90,
          right: rightPadding(),
        },
        duration: currentRef.current.motion ? 650 : 0,
      });
    }
    homeRef.current = home;

    function reframeSelection() {
      if (!ready || disposed) return;
      const { scene, selectedStationId, selectedSuggestionId, panelOpen } = currentRef.current;
      const stations = scene?.stations ?? new Map<string, SceneStation>();
      const selectedRoute = scene?.kind === "live"
        ? scene.dispatches.find((route) => route.id === selectedSuggestionId)
        : undefined;
      const selected = selectedStationId ? stations.get(selectedStationId) : null;
      const paddingRight = rightPadding();
      const newlyObscured = panelOpen && paddingRight !== lastRightPadding;
      if (selected?.coordinate && (selectedStationId !== lastSelectedId || newlyObscured)) {
        const p = map.project(selected.coordinate);
        if (p.x < 85 || p.x > width - paddingRight || p.y < 110 || p.y > height - 160) {
          map.easeTo({
            center: selected.coordinate,
            offset: [-paddingRight / 2 + 20, -20],
            duration: currentRef.current.motion ? 500 : 0,
          });
        }
      }
      if (selectedRoute && (selectedSuggestionId !== lastSuggestionId || newlyObscured)) {
        const endpoints = [stations.get(selectedRoute.from)?.coordinate, stations.get(selectedRoute.to)?.coordinate].filter((p): p is Coordinate => Boolean(p));
        if (endpoints.some((coordinate) => { const p = map.project(coordinate); return p.x < 85 || p.x > width - paddingRight || p.y < 110 || p.y > height - 160; })) {
          const bounds = new maplibregl.LngLatBounds();
          endpoints.forEach((p) => bounds.extend(p));
          map.fitBounds(bounds, { maxZoom: 16, padding: { top: 110, bottom: 160, left: 85, right: paddingRight }, duration: currentRef.current.motion ? 500 : 0 });
        }
      }
      lastRightPadding = paddingRight;
      lastSuggestionId = selectedSuggestionId;
      lastSelectedId = selectedStationId;
    }
    reframeRef.current = reframeSelection;

    function refresh() {
      if (!ready || disposed) return;
      const { scene, selectedStationId, selectedSuggestionId } =
        currentRef.current;
      const stations = scene?.stations ?? new Map<string, SceneStation>();
      highlightedStations = new Set(
        selectedStationId ? [selectedStationId] : [],
      );
      const selectedRoute =
        scene?.kind === "live"
          ? scene.dispatches.find((route) => route.id === selectedSuggestionId)
          : undefined;
      if (selectedRoute) {
        highlightedStations.add(selectedRoute.from);
        highlightedStations.add(selectedRoute.to);
      }
      for (const [id, entry] of markers) {
        const station = stations.get(id);
        if (!station?.coordinate || (scene?.kind !== "replay" && !matchesRisk(station.status, currentRef.current.riskFilter) && !highlightedStations.has(id))) {
          detach(entry);
          markers.delete(id);
        }
      }
      let order = 0;
      for (const station of stations.values()) {
        if (!station.coordinate) continue;
        if (scene?.kind !== "replay" && !matchesRisk(station.status, currentRef.current.riskFilter) && !highlightedStations.has(station.id)) continue;
        let entry = markers.get(station.id);
        if (!entry) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "city-marker";
          button.dataset.stationId = station.id;
          const label = document.createElement("span");
          button.append(label);
          button.onclick = (event) => {
            event.stopPropagation();
            button.focus();
            currentRef.current.onSelectStation(station.id);
          };
          entry = {
            button,
            marker: new maplibregl.Marker({ element: button }).setLngLat(
              station.coordinate,
            ),
            attached: false,
            state: "",
            lngLat: station.coordinate,
            order: 0,
          };
          markers.set(station.id, entry);
        }
        if (
          entry.lngLat[0] !== station.coordinate[0] ||
          entry.lngLat[1] !== station.coordinate[1]
        ) {
          entry.marker.setLngLat(station.coordinate);
          entry.lngLat = station.coordinate;
        }
        entry.order = order++;
        const pressed = station.id === selectedStationId;
        const highlighted = highlightedStations.has(station.id);
        const state = `${station.status}|${station.inventory}|${station.name}|${pressed}|${highlighted}`;
        if (entry.state === state) continue;
        entry.state = state;
        entry.button.style.setProperty(
          "--station-color",
          riskColor(station.status),
        );
        entry.button.setAttribute(
          "aria-label",
          `${station.name}，${station.id}，${statusNames[station.status] ?? "状态未知"}${station.inventory === null ? "" : `，当前 ${station.inventory} 辆`}`,
        );
        entry.button.setAttribute("aria-pressed", String(pressed));
        entry.button.dataset.highlighted = String(highlighted);
        entry.button.dataset.inventory =
          station.inventory === null
            ? "unavailable"
            : String(station.inventory);
        entry.button.dataset.status = station.status;
        if (entry.button.firstChild)
          entry.button.firstChild.textContent = `${station.name} · ${statusNames[station.status] ?? station.status}`;
      }
      if (!fitted && [...stations.values()].some((station) => station.coordinate)) {
        fitted = true;
        home();
      }
      reframeSelection();
      dirty = true;
      schedule();
    }
    refreshRef.current = refresh;

    function collectGeometry() {
      if (!ready || !map.isStyleLoaded()) return;
      roads = [];
      buildingPoints = [];
      const seen = new Set<string>();
      for (const feature of map.queryRenderedFeatures({
        layers: ["streets"],
      })) {
        if (
          ["rail", "transit", "ferry", "aerialway"].includes(
            feature.properties.class,
          )
        )
          continue;
        const lines =
          feature.geometry.type === "LineString"
            ? [feature.geometry.coordinates]
            : feature.geometry.type === "MultiLineString"
              ? feature.geometry.coordinates
              : [];
        for (const line of lines) {
          if (line.length < 2) continue;
          const key = `${line[0]}:${line.at(-1)}`;
          if (!seen.has(key)) {
            seen.add(key);
            roads.push({
              line: line.map(coordinate),
              major: majorRoads.has(feature.properties.class),
            });
          }
        }
      }
      for (const feature of map.queryRenderedFeatures({
        layers: ["footprints"],
      })) {
        const polygons =
          feature.geometry.type === "Polygon"
            ? [feature.geometry.coordinates]
            : feature.geometry.type === "MultiPolygon"
              ? feature.geometry.coordinates
              : [];
        for (const polygon of polygons)
          for (const point of polygon[0].slice(0, -1))
            buildingPoints.push(coordinate(point));
      }
      buildingPoints = buildingPoints.filter((point) => {
        const p = map.project(point);
        return p.x >= 0 && p.x <= width && p.y >= 0 && p.y <= height;
      });
      // ponytail: sample decorative geometry to bound redraw cost; use GPU particles for denser scenes.
      roads = roads.filter(
        (_, index) => index % Math.max(1, Math.ceil(roads.length / 360)) === 0,
      );
      buildingPoints = buildingPoints.filter(
        (_, index) =>
          index % Math.max(1, Math.ceil(buildingPoints.length / 4200)) === 0,
      );
      host.dataset.roadFeatures = String(roads.length);
      host.dataset.buildingPoints = String(buildingPoints.length);
      dirty = true;
      schedule();
    }

    function project() {
      scale = zoomScale(map.getZoom());
      projectedBuildings = [];
      for (const point of buildingPoints) {
        const p = map.project(point);
        if (p.x < 0 || p.x > width || p.y < 0 || p.y > height) continue;
        projectedBuildings.push({
          x: p.x,
          y: p.y,
          sweep: (p.x / width + 1 - p.y / height) / 2,
        });
      }
      projectedRoads = roads
        .map(({ line, major }) => {
          const points = line.map((point) => map.project(point));
          const cumulative = [0];
          for (let index = 1; index < points.length; index++)
            cumulative.push(
              cumulative[index - 1] +
                Math.hypot(
                  points[index].x - points[index - 1].x,
                  points[index].y - points[index - 1].y,
                ),
            );
          return {
            points,
            cumulative,
            length: cumulative[cumulative.length - 1],
            major,
          };
        })
        .filter(
          (line) =>
            line.length > 15 &&
            line.points.some(
              (p) =>
                p.x > -80 && p.x < width + 80 && p.y > -80 && p.y < height + 80,
            ),
        );
      const { scene, riskFilter, flowFilter, selectedStationId } = currentRef.current;
      let inventoryDots = 0;
      projectedStations = [...(scene?.stations.values() ?? [])].flatMap(
        (station) => {
          const anchor = station.coordinate;
          if (!anchor) return [];
          if (scene?.kind !== "replay" && !matchesRisk(station.status, riskFilter) && !highlightedStations.has(station.id)) return [];
          inventoryDots += station.inventory ?? 0;
          const point = map.project(anchor);
          // Cull by a margin wide enough for ripples, beam and tooltips.
          if (
            point.x < -160 ||
            point.x > width + 160 ||
            point.y < -160 ||
            point.y > height + 220
          ) {
            if (!highlightedStations.has(station.id)) return [];
          }
          const axes = groundAxes(anchor, RING_METERS);
          const east = map.project(axes.east),
            north = map.project(axes.north);
          // One offset per available bike; orbiting only rotates them.
          const particles = orbitLayout(station.id, station.inventory ?? 0);
          return [
            {
              station,
              point,
              u: { x: east.x - point.x, y: east.y - point.y },
              v: { x: north.x - point.x, y: north.y - point.y },
              particles,
              gauge: gaugeRatio(station.inventory, station.capacity),
            },
          ];
        },
      );
      const routes = visibleRoutes(scene, flowFilter, selectedStationId);
      paths = routes.flatMap((route) => {
        const from = scene?.stations.get(route.from)?.coordinate;
        const to = scene?.stations.get(route.to)?.coordinate;
        if (!from || !to) return [];
        const a = map.project(from),
          b = map.project(to);
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        const loop = distance < 1;
        return [
          {
            route,
            a,
            b,
            c: {
              x: loop ? a.x - 70 : a.x + (b.x - a.x) / 3,
              y: loop
                ? a.y - 85
                : a.y + (b.y - a.y) / 3 - Math.max(40, distance * 0.2),
            },
            d: {
              x: loop ? a.x + 70 : a.x + ((b.x - a.x) * 2) / 3,
              y: loop
                ? a.y - 85
                : a.y + ((b.y - a.y) * 2) / 3 - Math.max(40, distance * 0.2),
            },
          },
        ];
      });
      // Total over every drawable station, independent of on-screen culling.
      canvas.dataset.inventoryDots = String(inventoryDots);
      syncMarkers();
      canvas.dataset.routes = String(paths.length);
      canvas.dataset.scene = scene?.kind ?? "empty";
      dirty = false;
    }
    function curve(path: (typeof paths)[number], t: number): Point {
      return cubicPoint(path.a, path.c, path.d, path.b, t);
    }
    function roadPoint(
      line: (typeof projectedRoads)[number],
      phase: number,
    ): Point {
      const target = phase * line.length;
      let i = 1;
      while (i < line.cumulative.length - 1 && line.cumulative[i] < target) i++;
      const a = line.points[i - 1],
        b = line.points[i];
      const distance = line.cumulative[i] - line.cumulative[i - 1];
      const t = distance ? (target - line.cumulative[i - 1]) / distance : 0;
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
    }
    function glowAt(p: Point, radius: number, color: string, alpha: number) {
      glowXY(p.x, p.y, radius, color, alpha);
    }
    function glowXY(
      x: number,
      y: number,
      radius: number,
      color: string,
      alpha: number,
      g: CanvasRenderingContext2D = ctx,
    ) {
      if (alpha <= 0.01) return;
      const r = glowRadius(radius);
      g.globalAlpha = alpha < 1 ? alpha : 1;
      g.drawImage(glow(color, r), x - r, y - r, r * 2, r * 2);
    }
    /** Trace an arc lying on the ground plane around a projected station. */
    function groundArc(
      s: (typeof projectedStations)[number],
      k: number,
      start = 0,
      end = Math.PI * 2,
      anticlockwise = false,
      g: CanvasRenderingContext2D = ctx,
    ) {
      // The path is built in the station's ground frame; restoring the plain
      // device transform before stroking keeps line widths in screen pixels.
      const r = pixelRatio;
      g.setTransform(r * s.u.x, r * s.u.y, r * s.v.x, r * s.v.y, r * s.point.x, r * s.point.y);
      g.beginPath();
      g.arc(0, 0, k, start, end, anticlockwise);
      g.setTransform(r, 0, 0, r, 0, 0);
    }
    function tracePath(path: (typeof paths)[number]) {
      ctx.beginPath();
      ctx.moveTo(path.a.x, path.a.y);
      ctx.bezierCurveTo(path.c.x, path.c.y, path.d.x, path.d.y, path.b.x, path.b.y);
    }
    /** Whether a point is inside a marker button (44px, border-radius 50%). */
    function inMarker(p: Point, x: number, y: number) {
      const dx = x - Math.round(p.x),
        dy = y - Math.round(p.y);
      return dx * dx + dy * dy <= 22 * 22;
    }
    /** Topmost drawn station whose marker button contains the point. */
    function hitTest(x: number, y: number) {
      for (let i = projectedStations.length - 1; i >= 0; i--)
        if (inMarker(projectedStations[i].point, x, y))
          return projectedStations[i].station.id;
      return null;
    }
    /**
     * Every station whose 44px button contains the pointer, plus the nearest
     * others within 64px (capped) so an approaching pointer finds real buttons.
     */
    function stationsNear(x: number, y: number) {
      const near = new Set<string>();
      const around: { id: string; distance: number }[] = [];
      for (const { station, point } of projectedStations) {
        const dx = Math.abs(point.x - x),
          dy = Math.abs(point.y - y);
        if (inMarker(point, x, y)) near.add(station.id);
        else if (dx <= 64 && dy <= 64) around.push({ id: station.id, distance: dx * dx + dy * dy });
      }
      around.sort((a, b) => a.distance - b.distance);
      for (const { id } of around) {
        if (near.size >= 24) break;
        near.add(id);
      }
      return near;
    }
    function setNear(next: Set<string>) {
      let changed = false;
      for (const id of nearIds)
        if (!next.has(id)) {
          changed = true;
          // Keep the DOM marker until its CSS hover-out transition has played.
          lingering.add(id);
          window.setTimeout(() => {
            if (nearIds.has(id)) return;
            lingering.delete(id);
            if (disposed) return;
            syncMarkers();
            marksDirty = true;
            schedule();
          }, 260);
        }
      for (const id of next)
        if (!nearIds.has(id)) {
          changed = true;
          lingering.delete(id);
        }
      nearIds = next;
      if (!changed) return;
      syncMarkers();
      marksDirty = true;
      schedule();
    }
    /** Idle diamonds on their own top canvas, above particles like the DOM markers were. */
    function drawDiamonds(g: CanvasRenderingContext2D) {
      g.clearRect(0, 0, width, height);
      g.globalCompositeOperation = "source-over";
      g.globalAlpha = 1;
      g.lineWidth = 1;
      g.strokeStyle = "rgba(255,255,255,0.85)";
      // The DOM ::before is a 7px content box plus a 1px border (pseudo-elements
      // are not covered by the global border-box rule): a 9px square rotated
      // 45°, whose box starts at 18.5px inside the 44px button, i.e. centered
      // 1px right and below the marker point.
      const outer = 4.5 * Math.SQRT2,
        inner = outer - Math.SQRT1_2;
      for (const { station, point } of projectedStations) {
        if (markers.get(station.id)?.attached) continue;
        // maplibre places DOM markers on whole pixels; match it.
        const x = Math.round(point.x) + 1,
          y = Math.round(point.y) + 1;
        g.fillStyle = riskColor(station.status);
        g.beginPath();
        g.moveTo(x, y - outer);
        g.lineTo(x + outer, y);
        g.lineTo(x, y + outer);
        g.lineTo(x - outer, y);
        g.closePath();
        g.fill();
        g.beginPath();
        g.moveTo(x, y - inner);
        g.lineTo(x + inner, y);
        g.lineTo(x, y + inner);
        g.lineTo(x - inner, y);
        g.closePath();
        g.stroke();
      }
    }
    function schedule() {
      if (!frame && ready && !document.hidden && !disposed)
        frame = requestAnimationFrame(draw);
    }
    function drawCity(amount: number) {
      // Faint building vertices, lit briefly as the scan band passes.
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = 0.07;
      ctx.fillStyle = "#e8dcff";
      ctx.beginPath();
      for (const p of projectedBuildings) ctx.rect(p.x - 0.5, p.y - 0.5, 1, 1);
      ctx.fill();
      for (const p of projectedBuildings) {
        const scan = scanIntensity(p.sweep, time);
        if (scan > 0.05) glowAt(p, (2 + scan * 3) * scale, "#e8dcff", scan * 0.75);
      }
      // Road comets: tails batched by brightness level, heads as glow sprites.
      const levels = 5;
      const tails: number[][] = Array.from({ length: levels }, () => []);
      const heads: { p: Point; major: boolean; fade: number }[] = [];
      projectedRoads.forEach((line, index) => {
        const count = Math.max(
          1,
          Math.min(3, Math.round((line.length / 180) * amount)),
        );
        const pixelsPerMs = (line.major ? 0.05 : 0.028) * (0.85 + (index % 4) * 0.1);
        const tail = Math.min(0.3, (line.major ? 46 : 30) / line.length);
        for (let i = 0; i < count; i++) {
          const phase =
            (i / count + index * 0.173 + (time * pixelsPerMs) / line.length) % 1;
          const head = roadPoint(line, phase);
          if (head.x < 0 || head.x > width || head.y < 0 || head.y > height) continue;
          const fade = smoothstep(0, 0.06, phase) * (1 - smoothstep(0.94, 1, phase));
          heads.push({ p: head, major: line.major, fade });
          let previous = roadPoint(line, Math.max(0, phase - tail));
          for (let level = 0; level < levels; level++) {
            const t = phase - tail * (1 - (level + 1) / levels);
            if (t <= 0) continue;
            const next = roadPoint(line, t);
            tails[level].push(previous.x, previous.y, next.x, next.y);
            previous = next;
          }
        }
      });
      ctx.strokeStyle = "#e8dcff";
      ctx.lineCap = "round";
      ctx.lineWidth = Math.max(0.7, scale);
      tails.forEach((segments, level) => {
        ctx.globalAlpha = ((level + 1) / levels) * 0.3;
        ctx.beginPath();
        for (let i = 0; i < segments.length; i += 4) {
          ctx.moveTo(segments[i], segments[i + 1]);
          ctx.lineTo(segments[i + 2], segments[i + 3]);
        }
        ctx.stroke();
      });
      for (const { p, major, fade } of heads)
        glowAt(p, (major ? 4.5 : 3) * scale, "#e8dcff", fade * (major ? 0.75 : 0.4));
    }
    function drawRoutes(kind: Scene["kind"] | undefined, selectedSuggestionId: string | null) {
      const color = kind === "replay" ? "#c6bcff" : "#efa3ff";
      const placed: Parameters<typeof placeLabel>[1] = [];
      // Thousands of replay OD arcs: keep the essentials, drop the ornaments.
      const busy = paths.length > 120;
      const labelled = new Set(
        paths.length > 40
          ? [...paths]
              .sort((a, b) => b.route.quantity - a.route.quantity)
              .slice(0, 20)
              .map((path) => path.route.id)
          : paths.map((path) => path.route.id),
      );
      const speed = 0.00009;
      for (const path of paths) {
        const selected = path.route.id === selectedSuggestionId;
        const dimmed = Boolean(selectedSuggestionId) && !selected;
        const strength =
          (selected ? 1 : path.route.top ? 0.75 : 0.25) * (dimmed ? 0.4 : 1);
        const lineWidth = routeWidth(path.route.quantity) * Math.min(1.3, scale + 0.2);
        ctx.globalCompositeOperation = "lighter";
        tracePath(path);
        ctx.globalAlpha = 1;
        ctx.lineCap = "round";
        if (!busy || selected) {
          ctx.strokeStyle = rgba(color, 0.1 * strength);
          ctx.lineWidth = lineWidth * 6;
          ctx.stroke();
        }
        const gradient = ctx.createLinearGradient(path.a.x, path.a.y, path.b.x, path.b.y);
        gradient.addColorStop(0, rgba(color, 0.3 * strength));
        gradient.addColorStop(1, rgba(color, strength));
        ctx.strokeStyle = gradient;
        ctx.lineWidth = lineWidth;
        ctx.stroke();
        if (!busy && (selected || path.route.top)) {
          ctx.strokeStyle = rgba("#ffffff", 0.35 * strength);
          ctx.lineWidth = Math.max(0.5, lineWidth * 0.3);
          ctx.stroke();
        }
        // Direction chevrons brighten as the flow passes them.
        const flow = (time * speed) % 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        for (let t = busy && !selected ? 0.8 : 0.14; t < 0.9; t += 0.12) {
          const wave = Math.pow(Math.max(0, Math.cos(Math.PI * 2 * (t - flow * PULSES_PER_ROUTE))), 6);
          const tip = curve(path, t),
            back = curve(path, t - 0.02);
          const angle = Math.atan2(tip.y - back.y, tip.x - back.x);
          const size = 4.5 * Math.min(1.3, scale + 0.2);
          ctx.globalAlpha = strength * (0.25 + 0.75 * wave);
          ctx.beginPath();
          ctx.moveTo(tip.x - size * Math.cos(angle - 0.6), tip.y - size * Math.sin(angle - 0.6));
          ctx.lineTo(tip.x, tip.y);
          ctx.lineTo(tip.x - size * Math.cos(angle + 0.6), tip.y - size * Math.sin(angle + 0.6));
          ctx.stroke();
        }
        if (!dimmed) {
          const trail = busy && !selected ? 1 : 6;
          for (let i = 0; i < PULSES_PER_ROUTE; i++) {
            const head = (i / PULSES_PER_ROUTE + time * speed) % 1;
            for (let k = trail - 1; k >= 0; k--) {
              const t = head - k * 0.014;
              if (t < 0) continue;
              glowAt(
                curve(path, t),
                (selected ? 7 : 5.5) * (1 - k / (trail + 1)) * Math.min(1.3, scale + 0.2),
                color,
                strength * (1 - k / trail),
              );
            }
          }
        }
        if (selected || (path.route.top && labelled.has(path.route.id))) {
          const mid = curve(path, 0.5);
          const [amount, ...rest] = path.route.label.split(" · ");
          const detail = rest.length ? ` · ${rest.join(" · ")}` : "";
          ctx.globalCompositeOperation = "source-over";
          ctx.font = "600 12px Inter, 'PingFang SC', sans-serif";
          const amountWidth = ctx.measureText(amount).width;
          ctx.font = "400 11px Inter, 'PingFang SC', sans-serif";
          const detailWidth = ctx.measureText(detail).width;
          const w = amountWidth + detailWidth + 20,
            h = 22;
          const box = placeLabel({ x: mid.x - w / 2, y: mid.y - h - 6, w, h }, placed);
          ctx.globalAlpha = dimmed ? 0.5 : 1;
          ctx.fillStyle = "rgba(20,18,29,0.9)";
          ctx.strokeStyle = rgba(color, selected ? 0.8 : 0.4);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.roundRect(box.x, box.y, box.w, box.h, 11);
          ctx.fill();
          ctx.stroke();
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          ctx.font = "600 12px Inter, 'PingFang SC', sans-serif";
          ctx.fillStyle = color;
          ctx.fillText(amount, box.x + 10, box.y + h / 2 + 0.5);
          ctx.font = "400 11px Inter, 'PingFang SC', sans-serif";
          ctx.fillStyle = "#aaa4b2";
          ctx.fillText(detail, box.x + 10 + amountWidth, box.y + h / 2 + 0.5);
        }
      }
    }
    const statusSolid = new Set(["HEALTHY", "SHORTAGE_RISK", "OVERFLOW_RISK", "NOT_APPLICABLE"]);
    const statusFull = new Set(["OVERFLOW_RISK", "HIGH_INVENTORY"]);
    /**
     * Static station art, re-rendered only when the projection or data change:
     * wash, rings and gauge (source-over) on one layer, core glows (additive)
     * on another. Per-frame work is then two drawImage calls.
     */
    function renderStationLayers() {
      ringCtx.clearRect(0, 0, width, height);
      coreCtx.clearRect(0, 0, width, height);
      coreCtx.globalCompositeOperation = "lighter";
      const g = ringCtx;
      for (const s of projectedStations) {
        const { station, point } = s;
        const selected = highlightedStations.has(station.id);
        const color = riskColor(station.status);
        const solid = statusSolid.has(station.status);
        groundArc(s, selected ? 1.3 : 1.05, 0, Math.PI * 2, false, g);
        g.globalAlpha = 1;
        g.fillStyle = rgba(color, selected ? 0.1 : 0.05);
        g.fill();
        groundArc(s, selected ? 1.3 : 1, 0, Math.PI * 2, false, g);
        g.setLineDash(solid ? [] : [4, 4]);
        g.strokeStyle = rgba(color, selected ? 0.95 : 0.55);
        g.lineWidth = selected ? 1.5 : 1;
        g.stroke();
        g.setLineDash([]);
        if (statusFull.has(station.status)) {
          groundArc(s, selected ? 1.5 : 1.22, 0, Math.PI * 2, false, g);
          g.strokeStyle = rgba(color, 0.3);
          g.lineWidth = 1;
          g.stroke();
        }
        if (s.gauge !== null && solid) {
          const k = selected ? 1.42 : 1.12;
          groundArc(s, k, 0, Math.PI * 2, false, g);
          g.strokeStyle = rgba(color, 0.12);
          g.lineWidth = 2.2 * scale;
          g.stroke();
          if (s.gauge > 0) {
            groundArc(s, k, Math.PI / 2, Math.PI / 2 - s.gauge * Math.PI * 2, true, g);
            g.lineCap = "round";
            g.strokeStyle = rgba(color, selected ? 1 : 0.85);
            g.stroke();
          }
        }
        glowXY(point.x, point.y, (selected ? 14 : 9) * scale, color, selected ? 0.9 : 0.5, coreCtx);
      }
    }
    /** Animated ripples and beam for highlighted stations only. */
    function drawSelection(moving: boolean) {
      ctx.globalCompositeOperation = "lighter";
      for (const s of projectedStations) {
        if (!highlightedStations.has(s.station.id)) continue;
        const { point } = s;
        const color = riskColor(s.station.status);
        for (let i = 0; i < 2; i++) {
          const phase = moving ? ((time / 2400 + i / 2) % 1) : 0.35 + i * 0.25;
          groundArc(s, 1.3 + phase * 1.7);
          ctx.globalAlpha = 1;
          ctx.strokeStyle = rgba(color, (1 - phase) * 0.45);
          ctx.lineWidth = 1.2;
          ctx.stroke();
        }
        const top = { x: point.x, y: point.y - 90 * scale };
        const beam = ctx.createLinearGradient(point.x, point.y, top.x, top.y);
        beam.addColorStop(0, rgba(color, 0.85));
        beam.addColorStop(1, rgba(color, 0));
        ctx.strokeStyle = beam;
        ctx.lineCap = "round";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(point.x, point.y);
        ctx.lineTo(top.x, top.y);
        ctx.stroke();
        ctx.lineWidth = 8;
        ctx.globalAlpha = 0.25;
        ctx.stroke();
      }
    }
    /** Orbiting inventory particles; one per available bike. */
    function particleSize() {
      return Math.min(1.4, scale + 0.15);
    }
    function buildParticles() {
      let count = 0;
      for (const s of projectedStations) count += s.particles.length;
      if (particleData.length < count * PARTICLE_STRIDE)
        particleData = new Float32Array(Math.ceil(count * 1.25) * PARTICLE_STRIDE);
      let o = 0;
      for (const s of projectedStations) {
        if (!s.particles.length) continue;
        const ex = (s.u.x * PARTICLE_METERS_X) / RING_METERS,
          ey = (s.u.y * PARTICLE_METERS_X) / RING_METERS,
          nx = (s.v.x * PARTICLE_METERS_Y) / RING_METERS,
          ny = (s.v.y * PARTICLE_METERS_Y) / RING_METERS;
        for (const particle of s.particles) {
          particleData[o++] = s.point.x;
          particleData[o++] = s.point.y;
          particleData[o++] = ex;
          particleData[o++] = ey;
          particleData[o++] = nx;
          particleData[o++] = ny;
          particleData[o++] = particle.radius;
          particleData[o++] = particle.angle;
          particleData[o++] = particle.speed;
        }
      }
      particleCount = count;
      bikes?.setParticles(particleData, count);
    }
    /** Canvas fallback when WebGL2 is unavailable (identical formula). */
    function drawParticles2D() {
      ctx.globalCompositeOperation = "lighter";
      const size = particleSize();
      for (let i = 0, o = 0; i < particleCount; i++, o += PARTICLE_STRIDE) {
        const radius = particleData[o + 6];
        const angle = particleData[o + 7] + time * particleData[o + 8];
        const cos = radius * Math.cos(angle);
        const sin = Math.sin(angle);
        const north = radius * sin;
        const near = 0.5 - sin * 0.5;
        glowXY(
          particleData[o] + cos * particleData[o + 2] + north * particleData[o + 4],
          particleData[o + 1] + cos * particleData[o + 3] + north * particleData[o + 5],
          (4.2 + near * 1.6) * size,
          "#f2edf8",
          0.62 + near * 0.38,
        );
      }
    }
    function draw(now: number) {
      frame = 0;
      if (!ready || document.hidden || disposed) return;
      const {
        scene,
        motion: moving,
        density: amount,
        selectedSuggestionId,
      } = currentRef.current;
      if (moving && lastPaint) time += Math.min(now - lastPaint, 80);
      if (now - lastPaint < 32 && !dirty && !marksDirty) {
        if (moving) schedule();
        return;
      }
      lastPaint = now;
      if (dirty) {
        project();
        renderStationLayers();
        buildParticles();
        marksDirty = true;
      }
      ctx.clearRect(0, 0, width, height);
      drawCity(amount);
      drawRoutes(scene?.kind, selectedSuggestionId);
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = 1;
      ctx.drawImage(ringLayer, 0, 0, width, height);
      ctx.globalCompositeOperation = "lighter";
      ctx.drawImage(coreLayer, 0, 0, width, height);
      drawSelection(moving);
      if (bikes) bikes.draw(time, particleSize());
      else drawParticles2D();
      if (marksDirty) {
        drawDiamonds(markCtx);
        marksDirty = false;
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      canvas.dataset.motion = String(moving);
      canvas.dataset.frame = String(Number(canvas.dataset.frame ?? 0) + 1);
      if (moving) schedule();
    }
    const resize = () => {
      hostRect = host.getBoundingClientRect();
      width = host.clientWidth;
      height = host.clientHeight;
      const ratio = Math.min(devicePixelRatio, 2);
      pixelRatio = ratio;
      for (const [layer, layerCtx] of [
        [ringLayer, ringCtx],
        [coreLayer, coreCtx],
        [markCtx.canvas, markCtx],
      ] as const) {
        layer.width = Math.round(width * ratio);
        layer.height = Math.round(height * ratio);
        layerCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
      }
      bikes?.resize(width, height, ratio);
      marksDirty = true;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      glow = createGlowCache(ratio);
      map.resize();
      dirty = true;
      schedule();
    };
    const visibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(frame);
        frame = 0;
      } else {
        lastPaint = 0;
        dirty = true;
        schedule();
      }
    };
    const moved = () => {
      dirty = true;
      schedule();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    document.addEventListener("visibilitychange", visibility);
    map.on("load", () => {
      ready = true;
      window.clearTimeout(timeout);
      setMapState(failed ? "error" : "ready");
      refresh();
      collectGeometry();
    });
    map.on("idle", collectGeometry);
    map.on("move", moved);
    // Clicks on a painted diamond (e.g. a tap before any hover) select the station.
    map.on("click", (event) => {
      const id = hitTest(event.point.x, event.point.y);
      if (id) currentRef.current.onSelectStation(id);
      else currentRef.current.onBackground();
    });
    const pointerMove = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      setNear(stationsNear(event.clientX - hostRect.left, event.clientY - hostRect.top));
    };
    const pointerLeave = () => setNear(new Set());
    host.addEventListener("pointermove", pointerMove);
    host.addEventListener("pointerleave", pointerLeave);
    map.on("error", fail);
    resize();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      cancelAnimationFrame(frame);
      observer.disconnect();
      bikes?.dispose();
      bikeCanvas.removeEventListener("webglcontextlost", bikesLost);
      host.removeEventListener("pointermove", pointerMove);
      host.removeEventListener("pointerleave", pointerLeave);
      document.removeEventListener("visibilitychange", visibility);
      map.getCanvas().removeEventListener("webglcontextlost", contextLost);
      for (const entry of markers.values()) detach(entry);
      map.remove();
      mapRef.current = null;
      refreshRef.current = () => {};
      reframeRef.current = () => {};
      homeRef.current = () => {};
    };
  }, [attempt]);

  useEffect(
    () => refreshRef.current(),
    [
      props.scene,
      props.riskFilter,
      props.flowFilter,
      props.selectedStationId,
      props.selectedSuggestionId,
      motion,
      density,
    ],
  );
  useEffect(() => reframeRef.current(), [props.panelOpen]);

  return (
    <section className="map-stage" aria-label="纽约三维运营地图">
      <div ref={hostRef} className="city-map" data-map-state={mapState} />
      <div className="map-attribution">
        <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">
          OpenFreeMap
        </a>{" "}
        ·{" "}
        <a href="https://openmaptiles.org/" target="_blank" rel="noreferrer">
          OpenMapTiles
        </a>{" "}
        ·{" "}
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noreferrer"
        >
          © OpenStreetMap
        </a>
      </div>
      <div className="map-atmosphere" aria-hidden="true" />
      <canvas ref={canvasRef} className="city-particles" aria-hidden="true" />
      <canvas ref={bikeCanvasRef} className="city-particles" aria-hidden="true" />
      <canvas ref={markCanvasRef} className="city-particles" aria-hidden="true" />
      <div className="map-dock" role="toolbar" aria-label="地图视角与光效">
        <button
          className="dock-button"
          aria-label="地图归位"
          title="地图归位"
          onClick={() => homeRef.current()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="6.5" />
            <path d="M12 2.5v4M12 17.5v4M2.5 12h4M17.5 12h4" />
          </svg>
        </button>
        <button
          className="dock-button"
          aria-label="放大地图"
          title="放大"
          onClick={() => mapRef.current?.zoomIn({ duration: motion ? 300 : 0 })}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <button
          className="dock-button"
          aria-label="缩小地图"
          title="缩小"
          onClick={() =>
            mapRef.current?.zoomOut({ duration: motion ? 300 : 0 })
          }
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12h14" />
          </svg>
        </button>
        <button
          className="dock-button dock-text"
          aria-label="切换二维三维视角"
          title="二维 / 三维"
          onClick={() => {
            const map = mapRef.current;
            if (map)
              map.easeTo({
                pitch: map.getPitch() > 30 ? 0 : 58,
                duration: motion ? 500 : 0,
              });
          }}
        >
          3D
        </button>
        <span className="dock-divider" aria-hidden="true" />
        <button
          className="dock-button"
          aria-pressed={motion}
          aria-label={motion ? "暂停光效" : "播放光效"}
          title={motion ? "暂停光效" : "播放光效"}
          onClick={() => setMotion((value) => !value)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {motion ? (
              <path d="M9 6.5v11M15 6.5v11" />
            ) : (
              <path className="filled" d="M8.5 6v12l10-6z" />
            )}
          </svg>
        </button>
        <div
          className="dock-density"
          onKeyDown={(event) => {
            if (event.key === "Escape" && densityOpen) {
              event.stopPropagation();
              setDensityOpen(false);
              densityButtonRef.current?.focus();
            }
          }}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget))
              setDensityOpen(false);
          }}
        >
          <button
            ref={densityButtonRef}
            className="dock-button"
            aria-label="调节城市装饰密度"
            title="装饰密度"
            aria-expanded={densityOpen}
            aria-controls="density-popover"
            onClick={() => setDensityOpen((open) => !open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z" />
              <path d="M18.5 16v4M16.5 18h4" />
            </svg>
          </button>
          {densityOpen && (
            <div id="density-popover" className="dock-popover">
              <label>
                <span>
                  装饰密度 <output>{density.toFixed(1)}×</output>
                </span>
                <input
                  aria-label="城市装饰密度"
                  type="range"
                  min="0.4"
                  max="1.6"
                  step="0.2"
                  value={density}
                  onChange={(event) => setDensity(Number(event.target.value))}
                />
              </label>
              <small>只影响道路光点等装饰，不改变任何业务数量。</small>
            </div>
          )}
        </div>
      </div>
      {mapState !== "ready" && (
        <div className="city-state" role="status">
          {mapState === "loading" && <span className="loader-dot" aria-hidden="true" />}
          <span>
            {mapState === "error"
              ? "城市底图暂不可用，业务面板仍可使用。"
              : "正在加载纽约街道与建筑…"}
          </span>
          {mapState === "error" && (
            <button onClick={() => setAttempt((value) => value + 1)}>
              重试底图
            </button>
          )}
        </div>
      )}
    </section>
  );
}
