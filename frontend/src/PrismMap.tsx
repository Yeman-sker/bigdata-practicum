import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as CityMap, Marker } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { cityStyle } from "./map-style";
import { particleOffsets, matchesRisk } from "./model.mjs";
import { visibleRoutes } from "./scene";
import type { RiskFilter, FlowFilter } from "./scene";
import type { Scene, SceneRoute, SceneStation } from "./scene";

maplibregl.setWorkerUrl(workerUrl);

type Point = { x: number; y: number };
type Coordinate = [number, number];
type Props = {
  scene: Scene | null;
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
function coordinate(value: number[]): Coordinate {
  return [value[0], value[1]];
}

export function PrismMap(props: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<CityMap | null>(null);
  const refreshRef = useRef<() => void>(() => {});
  const homeRef = useRef<() => void>(() => {});
  const [attempt, setAttempt] = useState(0);
  const [mapState, setMapState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [motion, setMotion] = useState(
    () => !matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [density, setDensity] = useState(1);
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
    if (!hostElement || !canvasElement || !context) return;
    const host = hostElement,
      canvas = canvasElement,
      ctx = context;
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
    let highlightedStations = new Set<string>();
    let roads: Coordinate[][] = [];
    let buildingPoints: Coordinate[] = [];
    let projectedBuildings: Point[] = [];
    let projectedRoads: {
      points: Point[];
      cumulative: number[];
      length: number;
    }[] = [];
    let projectedStations: {
      station: SceneStation;
      point: Point;
      particles: Point[];
    }[] = [];
    let paths: { route: SceneRoute; a: Point; b: Point; c: Point; d: Point }[] =
      [];
    const markers = new Map<
      string,
      { marker: Marker; button: HTMLButtonElement }
    >();
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
          right: width > 720 ? 380 : 40,
        },
        duration: currentRef.current.motion ? 650 : 0,
      });
    }
    homeRef.current = home;

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
          entry.marker.remove();
          markers.delete(id);
        }
      }
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
            marker: new maplibregl.Marker({ element: button })
              .setLngLat(station.coordinate)
              .addTo(map),
          };
          markers.set(station.id, entry);
        }
        entry.marker.setLngLat(station.coordinate);
        entry.button.style.setProperty(
          "--station-color",
          riskColor(station.status),
        );
        entry.button.setAttribute(
          "aria-label",
          `${station.name}，${station.id}，${statusNames[station.status] ?? "状态未知"}${station.inventory === null ? "" : `，当前 ${station.inventory} 辆`}`,
        );
        entry.button.setAttribute(
          "aria-pressed",
          String(station.id === selectedStationId),
        );
        entry.button.dataset.highlighted = String(
          highlightedStations.has(station.id),
        );
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
      const selected = selectedStationId
        ? stations.get(selectedStationId)
        : null;
      if (selected?.coordinate && selectedStationId !== lastSelectedId) {
        const p = map.project(selected.coordinate);
        if (
          p.x < 85 ||
          p.x > width - (width > 720 ? 380 : 40) ||
          p.y < 110 ||
          p.y > height - 160
        ) {
          map.easeTo({
            center: selected.coordinate,
            offset: [width > 720 ? -160 : 0, -20],
            duration: currentRef.current.motion ? 500 : 0,
          });
        }
      }
      if (selectedRoute && selectedSuggestionId !== lastSuggestionId) {
        const endpoints = [stations.get(selectedRoute.from)?.coordinate, stations.get(selectedRoute.to)?.coordinate].filter((p): p is Coordinate => Boolean(p));
        if (endpoints.some((coordinate) => { const p = map.project(coordinate); return p.x < 85 || p.x > width - (width > 720 ? 380 : 40) || p.y < 110 || p.y > height - 160; })) {
          const bounds = new maplibregl.LngLatBounds();
          endpoints.forEach((p) => bounds.extend(p));
          map.fitBounds(bounds, { maxZoom: 16, padding: { top: 110, bottom: 160, left: 85, right: width > 720 ? 380 : 40 }, duration: currentRef.current.motion ? 500 : 0 });
        }
      }
      lastSuggestionId = selectedSuggestionId;
      lastSelectedId = selectedStationId;
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
            roads.push(line.map(coordinate));
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
        (_, index) => index % Math.max(1, Math.ceil(roads.length / 420)) === 0,
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
      projectedBuildings = buildingPoints
        .map((point) => map.project(point))
        .filter((p) => p.x >= 0 && p.x <= width && p.y >= 0 && p.y <= height);
      projectedRoads = roads
        .map((line) => {
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
      projectedStations = [...(scene?.stations.values() ?? [])].flatMap(
        (station) => {
          const anchor = station.coordinate;
          if (!anchor) return [];
          if (scene?.kind !== "replay" && !matchesRisk(station.status, riskFilter) && !highlightedStations.has(station.id)) return [];
          const particles = particleOffsets(
            station.id,
            station.inventory ?? 0,
          ).map((offset: Point) =>
            map.project([
              anchor[0] + offset.x * 0.000019,
              anchor[1] + offset.y * 0.000014,
            ]),
          );
          return [{ station, point: map.project(anchor), particles }];
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
      canvas.dataset.inventoryDots = String(
        projectedStations.reduce(
          (sum, station) => sum + station.particles.length,
          0,
        ),
      );
      canvas.dataset.routes = String(paths.length);
      canvas.dataset.scene = scene?.kind ?? "empty";
      dirty = false;
    }
    function curve(path: (typeof paths)[number], t: number): Point {
      const u = 1 - t;
      return {
        x:
          u ** 3 * path.a.x +
          3 * u * u * t * path.c.x +
          3 * u * t * t * path.d.x +
          t ** 3 * path.b.x,
        y:
          u ** 3 * path.a.y +
          3 * u * u * t * path.c.y +
          3 * u * t * t * path.d.y +
          t ** 3 * path.b.y,
      };
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
    function dot(p: Point, radius: number, color: string, alpha: number) {
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    function schedule() {
      if (!frame && ready && !document.hidden && !disposed)
        frame = requestAnimationFrame(draw);
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
      if (now - lastPaint < 32 && !dirty) {
        if (moving) schedule();
        return;
      }
      lastPaint = now;
      if (dirty) project();
      ctx.clearRect(0, 0, width, height);
      ctx.globalCompositeOperation = "lighter";
      for (const p of projectedBuildings) {
        const scan = Math.pow(
          Math.max(0, Math.sin(time * 0.00075 + p.y * 0.006 + p.x * 0.002)),
          6,
        );
        dot(p, scan > 0.6 ? 1.1 : 0.6, "#e8dcff", 0.08 + scan * 0.55);
      }
      projectedRoads.forEach((line, index) => {
        const count = Math.max(
          1,
          Math.min(12, Math.round((line.length / 32) * amount)),
        );
        for (let i = 0; i < count; i++) {
          const phase =
            (i / count +
              index * 0.173 +
              time * 0.000045 * (0.8 + (index % 4) * 0.18)) %
            1;
          const p = roadPoint(line, phase);
          if (p.x < 0 || p.x > width || p.y < 0 || p.y > height) continue;
          const tail = roadPoint(line, Math.max(0, phase - 0.005));
          ctx.globalAlpha = 0.4;
          ctx.strokeStyle = "#e8dcff";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(tail.x, tail.y);
          ctx.lineTo(p.x, p.y);
          ctx.stroke();
          dot(p, i % 4 === 0 ? 1.5 : 0.85, "#e8dcff", 0.75);
        }
      });
      for (const path of paths) {
        const selected = path.route.id === selectedSuggestionId;
        const color = scene?.kind === "replay" ? "#c6bcff" : "#efa3ff";
        ctx.globalAlpha = selected ? 1 : path.route.top ? 0.7 : 0.22;
        ctx.strokeStyle = color;
        ctx.lineWidth =
          (path.route.quantity >= 20 ? 3 : path.route.quantity >= 8 ? 2 : 1) +
          (selected ? 1 : 0);
        ctx.beginPath();
        ctx.moveTo(path.a.x, path.a.y);
        ctx.bezierCurveTo(
          path.c.x,
          path.c.y,
          path.d.x,
          path.d.y,
          path.b.x,
          path.b.y,
        );
        ctx.stroke();
        for (let i = 0; i < 4; i++)
          dot(
            curve(path, (i / 4 + time * 0.00009) % 1),
            selected ? 2 : 1.5,
            color,
            selected ? 1 : 0.7,
          );
        const arrow = curve(path, 0.83),
          before = curve(path, 0.8);
        const angle = Math.atan2(arrow.y - before.y, arrow.x - before.x);
        ctx.globalAlpha = 1;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(arrow.x, arrow.y);
        ctx.lineTo(
          arrow.x - 9 * Math.cos(angle - 0.45),
          arrow.y - 9 * Math.sin(angle - 0.45),
        );
        ctx.lineTo(
          arrow.x - 9 * Math.cos(angle + 0.45),
          arrow.y - 9 * Math.sin(angle + 0.45),
        );
        ctx.fill();
        if (selected || path.route.top) {
          const p = curve(path, 0.5);
          ctx.globalCompositeOperation = "source-over";
          ctx.font = "12px sans-serif";
          const labelWidth = ctx.measureText(path.route.label).width + 16;
          ctx.fillStyle = "rgba(20,18,29,.94)";
          ctx.fillRect(p.x - labelWidth / 2, p.y - 21, labelWidth, 22);
          ctx.fillStyle = color;
          ctx.textAlign = "center";
          ctx.fillText(path.route.label, p.x, p.y - 6);
          ctx.globalCompositeOperation = "lighter";
        }
      }
      for (const { station, point, particles } of projectedStations) {
        const selected = highlightedStations.has(station.id);
        const color = riskColor(station.status);
        ctx.globalAlpha = selected ? 0.9 : 0.5;
        ctx.strokeStyle = color;
        ctx.lineWidth = selected ? 1.5 : 1;
        ctx.setLineDash(
          [
            "HEALTHY",
            "SHORTAGE_RISK",
            "OVERFLOW_RISK",
            "NOT_APPLICABLE",
          ].includes(station.status)
            ? []
            : [4, 4],
        );
        ctx.beginPath();
        ctx.ellipse(
          point.x,
          point.y,
          selected ? 38 : 29,
          selected ? 22 : 16,
          0,
          0,
          Math.PI * 2,
        );
        ctx.stroke();
        ctx.setLineDash([]);
        for (let i = 0; i < particles.length; i++)
          dot(
            particles[i],
            1.65,
            "#f2edf8",
            0.72 + 0.25 * Math.sin(time * 0.001 + i),
          );
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      canvas.dataset.motion = String(moving);
      canvas.dataset.frame = String(Number(canvas.dataset.frame ?? 0) + 1);
      if (moving) schedule();
    }
    const resize = () => {
      width = host.clientWidth;
      height = host.clientHeight;
      const ratio = Math.min(devicePixelRatio, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
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
    map.on("click", () => currentRef.current.onBackground());
    map.on("error", fail);
    resize();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      map.getCanvas().removeEventListener("webglcontextlost", contextLost);
      for (const entry of markers.values()) entry.marker.remove();
      map.remove();
      mapRef.current = null;
      refreshRef.current = () => {};
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
      <canvas ref={canvasRef} className="city-particles" aria-hidden="true" />
      <div className="map-tools" aria-label="地图视角工具">
        <button
          aria-label="地图归位"
          title="地图归位"
          onClick={() => homeRef.current()}
        >
          ⌖
        </button>
        <button
          aria-label="放大地图"
          title="放大"
          onClick={() => mapRef.current?.zoomIn({ duration: motion ? 300 : 0 })}
        >
          ＋
        </button>
        <button
          aria-label="缩小地图"
          title="缩小"
          onClick={() =>
            mapRef.current?.zoomOut({ duration: motion ? 300 : 0 })
          }
        >
          −
        </button>
        <button
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
      </div>
      <div className="particle-controls">
        <button
          aria-pressed={motion}
          onClick={() => setMotion((value) => !value)}
        >
          {motion ? "Ⅱ 暂停光效" : "▶ 播放光效"}
        </button>
        <label>
          装饰密度
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
      </div>
      {mapState !== "ready" && (
        <div className="city-state" role="status">
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
