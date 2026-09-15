import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";
import examplesJson from "../../fixtures/day2/http-examples.json";
import {
  canDrawInventory,
  isMapExpired,
  nextAvailableHour,
  particleOffsets,
  playbackDelay,
  sortStations,
  stableHash,
} from "./model.mjs";
import "leaflet/dist/leaflet.css";

type Mode = "live" | "replay";
type View = "current" | "forecast" | "dispatch";
type Panel = "search" | "view" | "status" | "replay" | null;
type Position = { x: number; y: number };
type Projection = { positions: Map<string, Position>; width: number; height: number };

type Station = {
  station_id: string;
  station_name: string | null;
  lat: number | null;
  lon: number | null;
  capacity: number | null;
  num_bikes_available: number | null;
  num_docks_available: number | null;
  current_status: string;
  forecast_status: string;
  current_reason: string | null;
  forecast_reason: string | null;
  projected_bikes_1h: number | null;
  sample_days: number | null;
  observed_at_utc?: string | null;
  snapshot_at_utc: string | null;
  last_reported_at_utc: string | null;
  expires_at_utc: string | null;
  forecast_for_utc: string | null;
};

type Flow = {
  from_station_id: string;
  to_station_id: string;
  ride_count: number;
};

type Suggestion = {
  suggestion_id: string;
  from_station_id: string;
  to_station_id: string;
  move_bikes: number;
  distance_meters: number;
  priority: number;
  expires_at_utc: string;
};

type MapResponse = {
  contract_version: string;
  mode: Mode;
  data_origin: "GBFS_LIVE" | "GBFS_REPLAY" | "HISTORICAL" | "FIXTURE";
  clock_mode: "wall" | "recorded";
  dataset_id: string | null;
  snapshot_id: string | null;
  service_date: string;
  hour: number | null;
  observed_at_utc: string | null;
  as_of_utc: string;
  served_at_utc: string;
  expires_at_utc: string | null;
  stations: Station[];
  flows: Flow[];
  suggestions: Suggestion[];
};

type AvailabilityResponse = {
  dataset_id: string;
  source_months: string[];
  dates: { service_date: string; hours: number[] }[];
};

type ProfileHour = {
  hour: number;
  avg_inbound: number;
  avg_outbound: number;
  avg_net_flow: number;
  sample_days: number;
};

type ActualHour = {
  hour: number;
  inbound_rides: number;
  outbound_rides: number;
  net_flow: number;
  total_activity: number;
};

type HistoryResponse = {
  station_id: string;
  station_name: string | null;
  service_date: string | null;
  profile_start_date: string | null;
  profile_end_date: string | null;
  profile: ProfileHour[];
  actual: ActualHour[];
};

type Example = { value: unknown; "x-status"?: number };
const examples = examplesJson as Record<string, Example>;
const fixtureParameter = new URLSearchParams(window.location.search).get("fixture");
const usesFixture = fixtureParameter !== null;
const fixtureScenario = fixtureParameter || "live";
const speeds = [0.5, 1, 2, 5] as const;

class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const cloneExample = <T,>(name: string): T =>
  structuredClone(examples[name].value) as T;

const delay = (signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const id = window.setTimeout(resolve, 90);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(id);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

async function requestJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  const body = (await response.json().catch(() => null)) as
    | { error?: { code?: string; message?: string } }
    | null;
  if (!response.ok) {
    throw new ApiFailure(
      response.status,
      body?.error?.code ?? "INTERNAL_ERROR",
      body?.error?.message ?? `HTTP ${response.status}`,
    );
  }
  return body as T;
}

async function getMap(
  mode: Mode,
  signal: AbortSignal,
  selection?: { serviceDate: string; hour: number },
  refresh = false,
): Promise<MapResponse> {
  if (!usesFixture) {
    const query = new URLSearchParams({ mode });
    if (selection) {
      query.set("service_date", selection.serviceDate);
      query.set("hour", String(selection.hour));
    }
    return requestJson(`/api/v1/map?${query}`, signal);
  }

  await delay(signal);
  if (refresh && fixtureScenario === "refresh_failure") {
    throw new ApiFailure(503, "DATA_UNAVAILABLE", "fixture refresh failure");
  }
  const failures: Record<string, number> = {
    bad_query: 400,
    not_found: 404,
    too_large: 422,
    unavailable: 503,
  };
  if (mode === "live" && failures[fixtureScenario]) {
    const failure = cloneExample<{ error: { code: string; message: string } }>(fixtureScenario);
    throw new ApiFailure(
      failures[fixtureScenario],
      failure.error.code,
      failure.error.message,
    );
  }
  if (mode === "live") {
    const source = ["no_baseline", "stale", "empty_live"].includes(fixtureScenario)
      ? fixtureScenario
      : "live";
    const response = cloneExample<MapResponse>(source);
    if (fixtureScenario === "no_coordinates" && response.stations[0]) {
      response.stations[0].lat = null;
      response.stations[0].lon = null;
    }
    if (fixtureScenario === "invalid_inventory" && response.stations[0]) {
      response.stations[0].num_bikes_available = -1;
      response.stations[0].current_status = "INVALID_DATA";
      response.stations[0].forecast_status = "INVALID_DATA";
      response.stations[0].current_reason = "NEGATIVE_INVENTORY";
      response.stations[0].forecast_reason = "NEGATIVE_INVENTORY";
      response.suggestions = [];
    }
    return response;
  }

  const response = cloneExample<MapResponse>("replay");
  if (selection) {
    const isExampleHour =
      selection.serviceDate === response.service_date && selection.hour === response.hour;
    response.service_date = selection.serviceDate;
    response.hour = selection.hour;
    if (!isExampleHour) response.flows = [];
  }
  return response;
}

async function getAvailability(signal: AbortSignal): Promise<AvailabilityResponse> {
  if (!usesFixture) return requestJson("/api/v1/history/availability", signal);
  await delay(signal);
  if (fixtureScenario === "availability_failure") throw new ApiFailure(503, "DATA_UNAVAILABLE", "fixture availability failure");
  return cloneExample<AvailabilityResponse>(
    fixtureScenario === "empty_availability" ? "empty_availability" : "availability",
  );
}

async function getHistory(
  station: Station,
  dayOfWeek: number,
  serviceDate: string | null,
  signal: AbortSignal,
): Promise<HistoryResponse> {
  if (!usesFixture) {
    const query = new URLSearchParams({ day_of_week: String(dayOfWeek) });
    if (serviceDate) query.set("service_date", serviceDate);
    return requestJson(
      `/api/v1/stations/${encodeURIComponent(station.station_id)}/history?${query}`,
      signal,
    );
  }

  await delay(signal);
  const hasProfile = station.station_id === "5484.09" && fixtureScenario !== "no_baseline";
  const response = cloneExample<HistoryResponse>(hasProfile ? "history" : "empty_history");
  response.station_id = station.station_id;
  response.station_name = station.station_name;
  response.service_date = serviceDate;
  if (!serviceDate) response.actual = [];
  return response;
}

function assertMapSelection(
  response: MapResponse,
  mode: Mode,
  selection?: { serviceDate: string; hour: number },
) {
  if (
    response.mode !== mode ||
    (selection &&
      (response.service_date !== selection.serviceDate || response.hour !== selection.hour))
  ) {
    throw new ApiFailure(500, "INTERNAL_ERROR", "响应与当前选择不一致");
  }
}

const statusLabels: Record<string, string> = {
  SHORTAGE_RISK: "缺车",
  LOW_INVENTORY: "偏低",
  HEALTHY: "正常",
  HIGH_INVENTORY: "偏高",
  OVERFLOW_RISK: "满桩",
  SERVICE_UNAVAILABLE: "服务不可用",
  STALE_DATA: "数据已过期",
  INVALID_DATA: "数据异常",
  INSUFFICIENT_DATA: "数据不足",
  NOT_APPLICABLE: "历史回放",
};

const reasonLabels: Record<string, string> = {
  NO_BASELINE: "暂无历史基线",
  NEGATIVE_INVENTORY: "库存字段非法",
  STALE_OBSERVATION: "观测已过期",
  SERVICE_FLAGS: "站点已停服",
  MISSING_INVENTORY: "库存字段缺失",
  ZERO_SERVICEABLE_CAPACITY: "可服务容量为零",
  INVALID_TIME: "观测时间非法",
};

const errorLabels: Record<number, string> = {
  400: "所选参数无效",
  404: "该站点或日期不可用",
  422: "查询范围过大",
  503: "暂时无法连接",
};

const originLabels = {
  GBFS_LIVE: "实时",
  GBFS_REPLAY: "录制快照",
  HISTORICAL: "历史",
  FIXTURE: "开发样例",
};

function formatNewYorkTime(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function isoWeekday(serviceDate: string) {
  const day = new Date(`${serviceDate}T12:00:00Z`).getUTCDay();
  return day === 0 ? 7 : day;
}

function statusClass(status: string) {
  if (["SHORTAGE_RISK", "LOW_INVENTORY"].includes(status)) return "shortage";
  if (["OVERFLOW_RISK", "HIGH_INVENTORY"].includes(status)) return "overflow";
  if (status === "HEALTHY") return "healthy";
  if (status === "NOT_APPLICABLE") return "replay";
  return "neutral";
}

function Icon({ name }: { name: "search" | "clock" | "layers" | "close" }) {
  if (name === "search") {
    return <path d="m19 19-3.5-3.5m1.5-5A6.5 6.5 0 1 1 4 10.5a6.5 6.5 0 0 1 13 0Z" />;
  }
  if (name === "clock") {
    return <><circle cx="12" cy="12" r="8" /><path d="M12 7v5h4" /></>;
  }
  if (name === "layers") {
    return <><path d="m12 4 8 4-8 4-8-4 8-4Z" /><path d="m4 12 8 4 8-4M4 16l8 4 8-4" /></>;
  }
  return <path d="m6 6 12 12M18 6 6 18" />;
}

function IconButton({
  label,
  icon,
  active = false,
  disabled = false,
  buttonRef,
  expanded,
  onClick,
}: {
  label: string;
  icon: "search" | "clock" | "layers";
  active?: boolean;
  disabled?: boolean;
  buttonRef?: RefObject<HTMLButtonElement | null>;
  expanded?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      ref={buttonRef}
      className={`icon-button ${active ? "active" : ""}`}
      type="button"
      aria-label={label}
      aria-expanded={expanded}
      data-tooltip={label}
      disabled={disabled}
      onClick={onClick}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true"><Icon name={icon} /></svg>
    </button>
  );
}

function MapBackdrop({ background = true }: { background?: boolean }) {
  return (
    <g aria-hidden="true">
      <defs>
        <pattern id="streets" width="78" height="66" patternUnits="userSpaceOnUse" patternTransform="rotate(-8)">
          <path d="M0 8h78M0 36h78M12 0v66M52 0v66" fill="none" stroke="#fff" strokeWidth="5" />
          <path d="M0 8h78M0 36h78M12 0v66M52 0v66" fill="none" stroke="#d9e1ea" strokeWidth="1" />
        </pattern>
        <marker id="dispatch-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" fill="#f5a000" />
        </marker>
        <marker id="replay-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" fill="#11a8a5" />
        </marker>
        <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="#667085" strokeWidth="2" />
        </pattern>
      </defs>
      {background && <>
        <rect width="1000" height="700" fill="#bfe3f6" />
        <path d="M55-30 800-30 690 730 120 730Z" fill="#f7f5ef" />
        <path d="M820-20 1020-20 1020 720 760 720Z" fill="#f7f5ef" opacity=".95" />
        <path d="M90 55 180 20 204 130 125 165ZM185 510l130-44 38 136-151 58ZM610 70l110 15-30 92-118-22Z" fill="#ddebd7" />
        <path d="M55-30 800-30 690 730 120 730Z" fill="url(#streets)" opacity=".78" />
        <path d="M820-20 1020-20 1020 720 760 720Z" fill="url(#streets)" opacity=".7" />
        <path d="M0 265 790 178M85 0l676 638M208 700 800 205" fill="none" stroke="#fff" strokeWidth="12" opacity=".9" />
        <path d="M0 265 790 178M85 0l676 638M208 700 800 205" fill="none" stroke="#d9e1ea" strokeWidth="2" opacity=".7" />
      </>}
    </g>
  );
}

function MapSync({
  stations,
  onProjection,
  onMapClick,
}: {
  stations: Station[];
  onProjection: (projection: Projection) => void;
  onMapClick: () => void;
}) {
  const map = useMap();
  const frameRef = useRef<number | null>(null);
  const fittedKeyRef = useRef("");
  const sync = useCallback(() => {
    const size = map.getSize();
    const positions = new Map<string, Position>();
    for (const station of stations) {
      if (!Number.isFinite(station.lat) || !Number.isFinite(station.lon)) continue;
      const point = map.latLngToContainerPoint([station.lat!, station.lon!]);
      positions.set(station.station_id, { x: point.x, y: point.y });
    }
    onProjection({ positions, width: Math.max(size.x, 1), height: Math.max(size.y, 1) });
  }, [map, onProjection, stations]);
  const scheduleSync = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(sync);
  }, [sync]);

  useMapEvents({
    click: onMapClick,
    move: scheduleSync,
    resize: scheduleSync,
    zoom: scheduleSync,
  });

  useEffect(() => {
    const located = stations.filter(
      ({ lat, lon }) => Number.isFinite(lat) && Number.isFinite(lon),
    );
    const key = located.map(({ station_id, lat, lon }) => `${station_id}:${lat}:${lon}`).join("|");
    if (key && key !== fittedKeyRef.current) {
      fittedKeyRef.current = key;
      if (located.length === 1) {
        map.setView([located[0].lat!, located[0].lon!], 16, { animate: false });
      } else {
        const narrow = map.getSize().x < 760;
        map.fitBounds(located.map(({ lat, lon }) => [lat!, lon!]), {
          paddingTopLeft: [narrow ? 48 : 180, narrow ? 150 : 110],
          paddingBottomRight: [narrow ? 48 : 180, narrow ? 170 : 130],
          maxZoom: 17,
          animate: false,
        });
      }
    }
    scheduleSync();
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, [map, scheduleSync, stations]);

  return null;
}

function curvePath(from: Position, to: Position, key: string) {
  if (from.x === to.x && from.y === to.y) {
    return `M${from.x} ${from.y}c-70-80 90-80 20 0`;
  }
  const bendSize = Math.hypot(to.x - from.x, to.y - from.y) < 180 ? 105 : 46;
  const bend = stableHash(key) % 2 ? bendSize : -bendSize;
  const controlX = (from.x + to.x) / 2;
  const controlY = (from.y + to.y) / 2 + bend;
  return `M${from.x} ${from.y}Q${controlX} ${controlY} ${to.x} ${to.y}`;
}

function MapScene({
  map,
  view,
  expired,
  positions,
  selectedStationId,
  hoveredStationId,
  selectedSuggestionId,
  onSelectSuggestion,
  width,
  height,
}: {
  map: MapResponse | null;
  view: View;
  expired: boolean;
  positions: Map<string, Position>;
  selectedStationId: string | null;
  hoveredStationId: string | null;
  selectedSuggestionId: string | null;
  onSelectSuggestion: (id: string) => void;
  width: number;
  height: number;
}) {
  if (!map) return <svg className="map-svg" viewBox={`0 0 ${width} ${height}`}><MapBackdrop background={false} /></svg>;

  const dispatches =
    map.mode === "live" && view === "dispatch" && !expired ? map.suggestions : [];
  return (
    <svg className="map-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="运营地图数据图层">
      <MapBackdrop background={false} />

      <g className="routes">
        {dispatches.map((suggestion) => {
          const from = positions.get(suggestion.from_station_id);
          const to = positions.get(suggestion.to_station_id);
          if (!from || !to) return null;
          const selected = suggestion.suggestion_id === selectedSuggestionId;
          const path = curvePath(from, to, suggestion.suggestion_id);
          const labelX = (from.x + to.x) / 2;
          const labelY = (from.y + to.y) / 2 - 90;
          return (
            <g key={suggestion.suggestion_id} className={selected ? "route selected" : suggestion.priority <= 5 ? "route top" : "route"}>
              <path className="route-line" d={path} markerEnd="url(#dispatch-arrow)" />
              <path
                className="route-hit"
                d={path}
                role="button"
                tabIndex={0}
                aria-label={`${suggestion.from_station_id} 到 ${suggestion.to_station_id}，调度 ${suggestion.move_bikes} 辆，${suggestion.distance_meters} 米`}
                onClick={(event) => { event.stopPropagation(); onSelectSuggestion(suggestion.suggestion_id); }}
                onFocus={() => onSelectSuggestion(suggestion.suggestion_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectSuggestion(suggestion.suggestion_id);
                  }
                }}
              />
              {selected && (
                <g className="route-label" transform={`translate(${labelX - 115} ${labelY - 28})`}>
                  <rect width="230" height="56" rx="12" />
                  <text x="14" y="22">{suggestion.from_station_id} → {suggestion.to_station_id} · {suggestion.move_bikes} 辆 · {suggestion.distance_meters} m</text>
                  <text className="route-reason" x="14" y="43">缓解 +1h 缺车风险</text>
                </g>
              )}
            </g>
          );
        })}

        {map.mode === "replay" && map.flows.map((flow) => {
          const from = positions.get(flow.from_station_id);
          const to = positions.get(flow.to_station_id);
          if (!from || !to) return null;
          const path = curvePath(from, to, `${flow.from_station_id}:${flow.to_station_id}`);
          return (
            <g key={`${flow.from_station_id}:${flow.to_station_id}`} className="flow-route">
              <path d={path} markerEnd="url(#replay-arrow)" />
              <g className="flow-label" transform={`translate(${(from.x + to.x) / 2 - 39} ${(from.y + to.y) / 2 - 36})`}>
                <rect width="78" height="34" rx="10" />
                <text x="39" y="22" textAnchor="middle">{flow.ride_count} 次骑行</text>
              </g>
            </g>
          );
        })}
      </g>

      <g className="stations" aria-hidden="true">
        {map.stations.map((station) => {
          const position = positions.get(station.station_id);
          if (!position) return null;
          const status = map.mode === "replay"
            ? "NOT_APPLICABLE"
            : view === "forecast"
              ? station.forecast_status
              : station.current_status;
          const kind = statusClass(status);
          const showInventory = map.mode === "live" && canDrawInventory(station, expired);
          const showLabel =
            station.station_id === selectedStationId || station.station_id === hoveredStationId;
          const particles = showInventory
            ? particleOffsets(station.station_id, station.num_bikes_available)
            : [];
          return (
            <g key={station.station_id} transform={`translate(${position.x} ${position.y})`} className={`station ${kind} ${status.toLocaleLowerCase().replaceAll("_", "-")}`}>
              {map.mode === "live" && <>
                <circle className="halo halo-outer" r={kind === "healthy" ? 27 : 42} />
                {!["healthy", "neutral"].includes(kind) && <circle className="halo halo-inner" r="34" />}
              </>}
              {map.mode === "replay" && <circle className="replay-pulse" r="24" />}
              {particles.map((offset: Position, index: number) => (
                <circle key={index} className="inventory-particle" cx={offset.x} cy={offset.y} r="4" />
              ))}
              {kind === "overflow" ? <rect className="station-anchor" x="-9" y="-9" width="18" height="18" rx="3" /> : <circle className="station-anchor" r="9" />}
              {showLabel && (
                <g className="station-label" transform="translate(-78 -64)">
                  <rect width="156" height="40" rx="10" />
                  <text x="12" y="25">{station.station_name || station.station_id}</text>
                </g>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

function FlowChart({ history, replay }: { history: HistoryResponse; replay: boolean }) {
  const values = [
    ...history.profile.flatMap((item) => [item.avg_inbound, item.avg_outbound, item.avg_net_flow]),
    ...history.actual.map((item) => item.net_flow),
  ];
  const extent = Math.max(2, ...values.map((value) => Math.abs(value)));
  const points = (items: { hour: number; value: number }[]) =>
    items.map(({ hour, value }) => `${24 + (hour / 23) * 292},${61 - (value / extent) * 36}`).join(" ");
  return (
    <div className="chart-wrap">
      <div className="chart-legend" aria-hidden="true">
        <span className="inbound">入</span><span className="outbound">出</span><span className="net">净</span>
        {replay && <span className="actual">当日净</span>}
      </div>
      <svg className="flow-chart" viewBox="0 0 340 105" role="img" aria-label="24 小时流入、流出和净流量曲线">
        <path className="chart-axis" d="M24 25V78H322M24 61H322" />
        {[0, 8, 16, 23].map((hour) => <text key={hour} x={24 + (hour / 23) * 292} y="98" textAnchor="middle">{String(hour).padStart(2, "0")}</text>)}
        {history.profile.length > 0 && <>
          <polyline className="chart-line inbound" points={points(history.profile.map((item) => ({ hour: item.hour, value: item.avg_inbound })))} />
          <polyline className="chart-line outbound" points={points(history.profile.map((item) => ({ hour: item.hour, value: item.avg_outbound })))} />
          <polyline className="chart-line net" points={points(history.profile.map((item) => ({ hour: item.hour, value: item.avg_net_flow })))} />
        </>}
        {replay && history.actual.length > 0 && (
          <polyline className="chart-line actual" points={points(history.actual.map((item) => ({ hour: item.hour, value: item.net_flow })))} />
        )}
      </svg>
    </div>
  );
}

export default function App() {
  const [mode, setMode] = useState<Mode>("live");
  const [view, setView] = useState<View>("current");
  const [map, setMap] = useState<MapResponse | null>(null);
  const [projection, setProjection] = useState<Projection>({ positions: new Map(), width: 1000, height: 700 });
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(null);
  const [serviceDate, setServiceDate] = useState<string>("");
  const [hour, setHour] = useState(0);
  const [panel, setPanel] = useState<Panel>(null);
  const [query, setQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null);
  const [hoveredStationId, setHoveredStationId] = useState<string | null>(null);
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fatalError, setFatalError] = useState<ApiFailure | null>(null);
  const [refreshError, setRefreshError] = useState<ApiFailure | null>(null);
  const [transientMessage, setTransientMessage] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof speeds)[number]>(1);
  const [replayActivity, setReplayActivity] = useState(0);

  const mapRef = useRef<MapResponse | null>(null);
  const mapAbortRef = useRef<AbortController | null>(null);
  const mapRequestRef = useRef(0);
  const modeRequestRef = useRef(0);
  const historyAbortRef = useRef<AbortController | null>(null);
  const panelTriggerRef = useRef<HTMLButtonElement | null>(null);
  const searchButtonRef = useRef<HTMLButtonElement>(null);
  const modeButtonRef = useRef<HTMLButtonElement>(null);
  const viewButtonRef = useRef<HTMLButtonElement>(null);
  const statusButtonRef = useRef<HTMLButtonElement>(null);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const lensRef = useRef<HTMLElement>(null);
  const replayRef = useRef<HTMLDivElement>(null);
  const stationRefs = useRef(new Map<string, HTMLButtonElement>());

  const loadMap = useCallback(async (
    targetMode: Mode,
    selection?: { serviceDate: string; hour: number },
    refresh = false,
  ) => {
    const request = ++mapRequestRef.current;
    mapAbortRef.current?.abort();
    const controller = new AbortController();
    mapAbortRef.current = controller;
    if (!mapRef.current || mapRef.current.mode !== targetMode) setLoading(true);
    setTransientMessage(null);
    try {
      const response = await getMap(targetMode, controller.signal, selection, refresh);
      assertMapSelection(response, targetMode, selection);
      if (request !== mapRequestRef.current) return;
      mapRef.current = response;
      setMap(response);
      setFatalError(null);
      setRefreshError(null);
      setSelectedSuggestionId((current) =>
        current && response.suggestions.some(({ suggestion_id }) => suggestion_id === current)
          ? current
          : null,
      );
      setSelectedStationId((current) =>
        current && response.stations.some(({ station_id }) => station_id === current)
          ? current
          : null,
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (request !== mapRequestRef.current) return;
      const failure = error instanceof ApiFailure
        ? error
        : new ApiFailure(0, "NETWORK_ERROR", "network failure");
      const previous = mapRef.current;
      if (previous && previous.mode === targetMode) setRefreshError(failure);
      else setFatalError(failure);
    } finally {
      if (request === mapRequestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMap("live");
    return () => mapAbortRef.current?.abort();
  }, [loadMap]);

  useEffect(() => {
    mapRef.current = map;
  }, [map]);

  useEffect(() => {
    if (mode !== "live") return;
    const refresh = () => void loadMap("live", undefined, true);
    const interval = window.setInterval(refresh, 60_000);
    const onVisibility = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loadMap, mode]);

  const closePanel = useCallback((restoreFocus = true) => {
    setPanel(null);
    if (restoreFocus) window.requestAnimationFrame(() => panelTriggerRef.current?.focus());
  }, []);

  const openPanel = (next: Exclude<Panel, null>, trigger: HTMLButtonElement | null) => {
    panelTriggerRef.current = trigger;
    setSelectedStationId(null);
    setPanel(next);
  };

  const toggleMode = async (retryReplay = false) => {
    const request = ++modeRequestRef.current;
    mapRequestRef.current += 1;
    mapAbortRef.current?.abort();
    setPanel(null);
    setSelectedStationId(null);
    setSelectedSuggestionId(null);
    setRefreshError(null);
    setFatalError(null);
    setMap(null);
    mapRef.current = null;
    setPlaying(false);
    if (mode === "replay" && !retryReplay) {
      setMode("live");
      setView("current");
      await loadMap("live");
      return;
    }

    setMode("replay");
    setView("current");
    setLoading(true);
    const controller = new AbortController();
    mapAbortRef.current = controller;
    try {
      const response = await getAvailability(controller.signal);
      if (request !== modeRequestRef.current) return;
      setAvailability(response);
      if (!response.dates.length) {
        setLoading(false);
        setTransientMessage("暂无可用历史");
        return;
      }
      const fixtureReplay = cloneExample<MapResponse>("replay");
      const preferred = usesFixture
        ? response.dates.find(({ service_date }) => service_date === fixtureReplay.service_date)
        : response.dates.at(-1);
      const date = preferred ?? response.dates.at(-1)!;
      const selectedHour = usesFixture && date.hours.includes(fixtureReplay.hour ?? -1)
        ? fixtureReplay.hour!
        : date.hours[0];
      setServiceDate(date.service_date);
      setHour(selectedHour);
      panelTriggerRef.current = modeButtonRef.current;
      setPanel("replay");
      await loadMap("replay", { serviceDate: date.service_date, hour: selectedHour });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (request !== modeRequestRef.current) return;
      setFatalError(error instanceof ApiFailure ? error : new ApiFailure(0, "NETWORK_ERROR", "network failure"));
      setLoading(false);
    }
  };

  const currentDate = availability?.dates.find(({ service_date }) => service_date === serviceDate);
  const availableHours = currentDate?.hours ?? [];

  const selectHour = useCallback((nextHour: number, pause = true) => {
    if (!serviceDate || !availableHours.includes(nextHour)) {
      setTransientMessage("该小时不可用");
      return;
    }
    if (pause) setPlaying(false);
    setHour(nextHour);
    setReplayActivity((value) => value + 1);
    void loadMap("replay", { serviceDate, hour: nextHour });
  }, [availableHours, loadMap, serviceDate]);

  useEffect(() => {
    if (!playing || mode !== "replay") return;
    const timer = window.setTimeout(() => {
      const next = nextAvailableHour(availableHours, hour);
      if (next === null) {
        setPlaying(false);
        return;
      }
      selectHour(next, false);
    }, playbackDelay(speed));
    return () => window.clearTimeout(timer);
  }, [availableHours, hour, mode, playing, selectHour, speed]);

  useEffect(() => {
    if (!playing || panel !== "replay") return;
    const timer = window.setTimeout(() => {
      if (!replayRef.current?.contains(document.activeElement)) setPanel(null);
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [panel, playing, replayActivity]);

  const selectedStation = map?.stations.find(({ station_id }) => station_id === selectedStationId) ?? null;
  useEffect(() => {
    historyAbortRef.current?.abort();
    setHistory(null);
    if (!selectedStation) return;
    const controller = new AbortController();
    historyAbortRef.current = controller;
    setHistoryLoading(true);
    const date = mode === "replay" ? serviceDate : null;
    void getHistory(selectedStation, isoWeekday(date ?? map?.service_date ?? "2025-01-01"), date, controller.signal)
      .then((response) => setHistory(response))
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setHistory({
            station_id: selectedStation.station_id,
            station_name: selectedStation.station_name,
            service_date: date,
            profile_start_date: null,
            profile_end_date: null,
            profile: [],
            actual: [],
          });
        }
      })
      .finally(() => setHistoryLoading(false));
    window.requestAnimationFrame(() => lensRef.current?.focus());
    return () => controller.abort();
  }, [map?.service_date, mode, selectedStation, serviceDate]);

  const positions = projection.positions;
  const expired = isMapExpired(map);
  const sortedStations = useMemo(
    () => sortStations(map?.stations ?? [], view) as Station[],
    [map?.stations, view],
  );
  const searchResults = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return sortedStations
      .filter((station) => !needle || `${station.station_name ?? ""} ${station.station_id}`.toLocaleLowerCase("zh-CN").includes(needle))
      .slice(0, 8);
  }, [query, sortedStations]);

  useEffect(() => setSearchIndex(0), [query]);

  const openStation = useCallback((stationId: string) => {
    setPanel(null);
    setSelectedStationId(stationId);
    setTransientMessage(null);
  }, []);

  const closeStation = useCallback(() => {
    const stationId = selectedStationId;
    setSelectedStationId(null);
    window.requestAnimationFrame(() => {
      const stationButton = stationId ? stationRefs.current.get(stationId) : null;
      if (stationButton) stationButton.focus();
      else searchButtonRef.current?.focus();
    });
  }, [selectedStationId]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
      if (event.key === "Escape") {
        if (selectedStationId) closeStation();
        else if (panel) closePanel();
        else if (selectedSuggestionId) setSelectedSuggestionId(null);
        return;
      }
      if (typing) return;
      if (event.key.toLocaleLowerCase() === "n" && sortedStations.length) {
        event.preventDefault();
        const index = sortedStations.findIndex(({ station_id }) => station_id === selectedStationId);
        const delta = event.shiftKey ? -1 : 1;
        const next = sortedStations[(index + delta + sortedStations.length) % sortedStations.length];
        openStation(next.station_id);
      }
      if (view === "dispatch" && map?.suggestions.length && ["[", "]"].includes(event.key)) {
        event.preventDefault();
        const index = map.suggestions.findIndex(({ suggestion_id }) => suggestion_id === selectedSuggestionId);
        const delta = event.key === "]" ? 1 : -1;
        const next = map.suggestions[(index + delta + map.suggestions.length) % map.suggestions.length];
        setSelectedSuggestionId(next.suggestion_id);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closePanel, closeStation, map?.suggestions, openStation, panel, selectedStationId, selectedSuggestionId, sortedStations, view]);

  useEffect(() => {
    if (fatalError && !map) window.requestAnimationFrame(() => retryButtonRef.current?.focus());
  }, [fatalError, map]);

  useEffect(() => {
    if (!transientMessage) return;
    const timer = window.setTimeout(() => setTransientMessage(null), 4000);
    return () => window.clearTimeout(timer);
  }, [transientMessage]);

  const changeDate = (nextDate: string) => {
    const next = availability?.dates.find(({ service_date }) => service_date === nextDate);
    if (!next) {
      setTransientMessage("该日期不可用");
      return;
    }
    setPlaying(false);
    setServiceDate(nextDate);
    setHour(next.hours[0]);
    setReplayActivity((value) => value + 1);
    void loadMap("replay", { serviceDate: nextDate, hour: next.hours[0] });
  };

  const retry = () => {
    if (mode === "live") void loadMap("live", undefined, true);
    else if (serviceDate) void loadMap("replay", { serviceDate, hour }, true);
    else void toggleMode(true);
  };

  const status = (() => {
    if (loading && !map) return { text: "加载中", kind: "loading" };
    if (fatalError && !map) return { text: errorLabels[fatalError.status] ?? "暂时无法连接", kind: "error" };
    if (transientMessage) return { text: transientMessage, kind: "warning" };
    if (refreshError) return { text: `刷新失败 · ${formatNewYorkTime(map?.observed_at_utc ?? null)}`, kind: "warning" };
    if (expired) return { text: "数据已过期", kind: "error" };
    if (map?.mode === "live" && map.stations.length === 0) return { text: "暂无站点", kind: "empty" };
    if (map?.mode === "replay" && map.flows.length === 0) return { text: "本小时无流量", kind: "empty" };
    if (!map && mode === "replay" && availability?.dates.length === 0) return { text: "暂无可用历史", kind: "empty" };
    if (!map) return { text: "加载中", kind: "loading" };
    const source = originLabels[map.data_origin];
    return map.mode === "replay"
      ? { text: `回放 · ${source} · ${map.service_date} ${String(map.hour).padStart(2, "0")}:00`, kind: "replay" }
      : { text: `${view === "forecast" ? "+1h · " : view === "dispatch" ? "调度 · " : ""}${source} · ${formatNewYorkTime(map.observed_at_utc)}`, kind: "live" };
  })();

  const hiddenFlows = map?.mode === "replay"
    ? map.flows.filter((flow) => !positions.has(flow.from_station_id) || !positions.has(flow.to_station_id))
    : [];
  const selectedPosition = selectedStation ? positions.get(selectedStation.station_id) : null;
  const lensStyle = selectedPosition
    ? ({
        left: `${(selectedPosition.x / projection.width) * 100}%`,
        top: `${(selectedPosition.y / projection.height) * 100}%`,
        transform: `${selectedPosition.x < projection.width / 2 ? "translate(36px, -50%)" : "translate(calc(-100% - 36px), -50%)"}`,
      } as CSSProperties)
    : undefined;

  const handleMapBackground = () => {
    if (selectedStationId) closeStation();
    else if (panel) closePanel(false);
    else if (selectedSuggestionId) setSelectedSuggestionId(null);
  };

  return (
    <main className={`app mode-${mode}`}>
      <div className="map-stage" onClick={handleMapBackground}>
        <svg className="fallback-map" viewBox="0 0 1000 700" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><MapBackdrop /></svg>
        <MapContainer
          className="leaflet-map"
          center={[40.7, -74]}
          zoom={14}
          minZoom={10}
          maxZoom={20}
          zoomControl={false}
          scrollWheelZoom
          keyboard
          worldCopyJump
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            opacity={0.82}
          />
          <MapSync
            stations={map?.stations ?? []}
            onProjection={setProjection}
            onMapClick={handleMapBackground}
          />
        </MapContainer>
        <MapScene
          map={map}
          view={view}
          expired={expired}
          positions={positions}
          selectedStationId={selectedStationId}
          hoveredStationId={hoveredStationId}
          selectedSuggestionId={selectedSuggestionId}
          onSelectSuggestion={(id) => setSelectedSuggestionId(id)}
          width={projection.width}
          height={projection.height}
        />

        {map?.stations.map((station) => {
          const position = positions.get(station.station_id);
          if (!position) return null;
          const label = `${station.station_name || "未命名站点"}，${station.station_id}，${statusLabels[mode === "replay" ? "NOT_APPLICABLE" : view === "forecast" ? station.forecast_status : station.current_status] ?? "状态未知"}`;
          return (
            <button
              key={station.station_id}
              ref={(element) => {
                if (element) stationRefs.current.set(station.station_id, element);
                else stationRefs.current.delete(station.station_id);
              }}
              className="station-hit"
              style={{ left: `${(position.x / projection.width) * 100}%`, top: `${(position.y / projection.height) * 100}%` }}
              type="button"
              aria-label={label}
              data-selected={selectedStationId === station.station_id}
              onClick={(event) => { event.stopPropagation(); openStation(station.station_id); }}
              onFocus={() => setHoveredStationId(station.station_id)}
              onBlur={() => setHoveredStationId(null)}
              onMouseEnter={() => setHoveredStationId(station.station_id)}
              onMouseLeave={() => setHoveredStationId(null)}
            />
          );
        })}

        <nav className="global-controls" aria-label="地图工具" onClick={(event) => event.stopPropagation()}>
          <IconButton
            buttonRef={searchButtonRef}
            label="搜索站点"
            icon="search"
            active={panel === "search"}
            expanded={panel === "search"}
            onClick={() => panel === "search" ? closePanel() : openPanel("search", searchButtonRef.current)}
          />
          <IconButton
            buttonRef={modeButtonRef}
            label={mode === "live" ? "进入历史回放" : "返回实时地图"}
            icon="clock"
            active={mode === "replay"}
            onClick={() => void toggleMode()}
          />
          <IconButton
            buttonRef={viewButtonRef}
            label={mode === "replay" ? "回放中不提供实时视角" : `地图视角：${view === "current" ? "当前" : view === "forecast" ? "+1h" : "调度"}`}
            icon="layers"
            active={panel === "view" || view !== "current"}
            disabled={mode === "replay"}
            expanded={panel === "view"}
            onClick={() => panel === "view" ? closePanel() : openPanel("view", viewButtonRef.current)}
          />
        </nav>

        <div className="status-area" onClick={(event) => event.stopPropagation()}>
          <button
            ref={statusButtonRef}
            type="button"
            className={`status-capsule ${status.kind}`}
            aria-expanded={panel === "status"}
            onClick={() => panel === "status" ? closePanel() : openPanel("status", statusButtonRef.current)}
          >
            <span className="status-dot" aria-hidden="true" />
            <span>{status.text}</span>
          </button>
          {panel === "status" && (
            <section className="status-panel surface" aria-label="数据状态详情">
              <strong>{status.text}</strong>
              <span>{map ? `契约 ${map.contract_version} · ${originLabels[map.data_origin]}` : "尚未取得可用响应"}</span>
              {map?.observed_at_utc && <span>最后观测 {formatNewYorkTime(map.observed_at_utc)}</span>}
              {hiddenFlows.length > 0 && <span>未绘制 {hiddenFlows.length} 条 OD，共 {hiddenFlows.reduce((sum, flow) => sum + flow.ride_count, 0)} 次骑行</span>}
              {(refreshError || expired || fatalError) && <button type="button" className="primary-button" onClick={retry}>重试</button>}
              {!refreshError && !expired && !fatalError && mode === "live" && <button type="button" className="quiet-button" onClick={retry}>立即刷新</button>}
            </section>
          )}
        </div>

        <div className="sr-live" aria-live="polite">{status.text}</div>

        {panel === "search" && (
          <section className="search-panel surface" aria-label="搜索站点" onClick={(event) => event.stopPropagation()}>
            <label className="search-field">
              <svg viewBox="0 0 24 24" aria-hidden="true"><Icon name="search" /></svg>
              <span className="sr-only">按站名或站点 ID 搜索</span>
              <input
                autoFocus
                value={query}
                placeholder="站名或站点 ID"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setSearchIndex((index) => Math.min(index + 1, searchResults.length - 1));
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setSearchIndex((index) => Math.max(index - 1, 0));
                  } else if (event.key === "Enter" && searchResults[searchIndex]) {
                    event.preventDefault();
                    openStation(searchResults[searchIndex].station_id);
                  }
                }}
              />
              {query && <button type="button" aria-label="清空搜索" onClick={() => setQuery("")}><svg viewBox="0 0 24 24" aria-hidden="true"><Icon name="close" /></svg></button>}
            </label>
            <div className="search-results" role="listbox" aria-label="站点结果">
              {searchResults.length ? searchResults.map((station, index) => {
                const stationStatus = mode === "replay" ? "NOT_APPLICABLE" : view === "forecast" ? station.forecast_status : station.current_status;
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={index === searchIndex}
                    className={index === searchIndex ? "selected" : ""}
                    key={station.station_id}
                    onMouseEnter={() => setSearchIndex(index)}
                    onClick={() => openStation(station.station_id)}
                  >
                    <span className={`result-marker ${statusClass(stationStatus)}`} />
                    <span><strong>{station.station_name || "未命名站点"}</strong><small>{station.station_id}</small></span>
                    <span className="result-status">{station.lat === null || station.lon === null ? "无法定位" : statusLabels[stationStatus] ?? "状态未知"}</span>
                  </button>
                );
              }) : <p className="empty-copy">没有匹配站点</p>}
            </div>
            <footer>↑↓ 选择 · Enter 打开 · Esc 关闭 · N 巡览</footer>
          </section>
        )}

        {panel === "view" && (
          <div className="view-dial surface" role="menu" aria-label="地图视角" onClick={(event) => event.stopPropagation()}>
            {(["current", "forecast", "dispatch"] as View[]).map((item, index) => (
              <button
                key={item}
                type="button"
                role="menuitemradio"
                aria-checked={view === item}
                autoFocus={index === 0}
                className={view === item ? "selected" : ""}
                onClick={() => {
                  setView(item);
                  if (item !== "dispatch") setSelectedSuggestionId(null);
                  closePanel();
                }}
              >
                {item === "current" ? "当前" : item === "forecast" ? "+1h" : `调度 ${map?.suggestions.length ?? 0}`}
              </button>
            ))}
          </div>
        )}

        {selectedStation && (
          <section
            ref={lensRef}
            className={`station-lens surface ${selectedPosition ? `anchored ${selectedPosition.x < projection.width / 2 ? "on-right" : "on-left"}` : "unlocated"}`}
            style={lensStyle}
            role="region"
            aria-label={`${selectedStation.station_name || selectedStation.station_id}站点详情`}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <button type="button" className="lens-close" aria-label="关闭站点详情" onClick={closeStation}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><Icon name="close" /></svg>
            </button>
            <header>
              <h1>{selectedStation.station_name || "未命名站点"}</h1>
              <span className={`station-state ${statusClass(mode === "replay" ? "NOT_APPLICABLE" : view === "forecast" ? selectedStation.forecast_status : selectedStation.current_status)}`}>
                {statusLabels[mode === "replay" ? "NOT_APPLICABLE" : view === "forecast" ? selectedStation.forecast_status : selectedStation.current_status]}
              </span>
              <p>{selectedStation.station_id}{!selectedPosition && " · 无法定位"}</p>
            </header>
            {mode === "live" && (
              <div className="lens-numbers">
                <strong>{canDrawInventory(selectedStation, expired) ? selectedStation.num_bikes_available : "—"}</strong>
                <span>→</span><span>+1h</span>
                <strong>{selectedStation.projected_bikes_1h ?? "—"}</strong>
                <i />
                <span>空桩</span><strong>{selectedStation.num_docks_available ?? "—"}</strong>
              </div>
            )}
            {(selectedStation.current_reason || selectedStation.forecast_reason) && (
              <p className="reason-copy">{reasonLabels[(view === "forecast" ? selectedStation.forecast_reason : selectedStation.current_reason) ?? ""] ?? "数据暂不可用"}</p>
            )}
            {historyLoading && <p className="lens-loading">正在加载 24 小时曲线…</p>}
            {!historyLoading && history && <>
              {history.profile.length > 0 ? <FlowChart history={history} replay={mode === "replay"} /> : <p className="empty-copy lens-empty">暂无历史基线</p>}
              {mode === "replay" && history.actual.length === 0 && <p className="actual-empty">当日无活动</p>}
              <footer>
                {history.profile.length > 0 && <span>{history.profile[0].sample_days} 日样本</span>}
                {history.profile_start_date && <span>{history.profile_start_date} — {history.profile_end_date}</span>}
              </footer>
            </>}
          </section>
        )}

        {mode === "replay" && availability?.dates.length ? panel === "replay" ? (
          <div
            ref={replayRef}
            className="replay-controller surface"
            aria-label="历史回放控制器"
            onClick={(event) => { event.stopPropagation(); setReplayActivity((value) => value + 1); }}
            onFocusCapture={() => setReplayActivity((value) => value + 1)}
          >
            <label className="date-control">
              <span className="sr-only">回放日期</span>
              <input
                type="date"
                value={serviceDate}
                min={availability.dates[0]?.service_date}
                max={availability.dates.at(-1)?.service_date}
                onChange={(event) => changeDate(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="play-button"
              aria-label={playing ? "暂停回放" : "播放回放"}
              onClick={() => setPlaying((value) => !value)}
            >{playing ? "Ⅱ" : "▶"}</button>
            <label className="timeline-control">
              <span className="sr-only">回放小时</span>
              <input
                type="range"
                min="0"
                max="23"
                step="1"
                value={hour}
                list="available-hours"
                onChange={(event) => selectHour(Number(event.target.value))}
              />
              <datalist id="available-hours">
                {availableHours.map((availableHour) => <option key={availableHour} value={availableHour} />)}
              </datalist>
              <span className="timeline-labels"><span>00</span><strong>{String(hour).padStart(2, "0")}</strong><span>23</span></span>
            </label>
            <strong className="current-hour">{String(hour).padStart(2, "0")}:00</strong>
            <button
              type="button"
              className="speed-button"
              aria-label={`回放速度 ${speed} 倍，点击切换`}
              onClick={() => setSpeed(speeds[(speeds.indexOf(speed) + 1) % speeds.length])}
              onKeyDown={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
                if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
                event.preventDefault();
                const delta = event.key === "ArrowRight" ? 1 : -1;
                setSpeed(speeds[(speeds.indexOf(speed) + delta + speeds.length) % speeds.length]);
              }}
            >{speed}×</button>
          </div>
        ) : (
          <button
            type="button"
            className="replay-compact surface"
            aria-label={`展开回放控制器，当前 ${String(hour).padStart(2, "0")}:00，${speed} 倍速`}
            onClick={(event) => {
              event.stopPropagation();
              panelTriggerRef.current = event.currentTarget;
              setPanel("replay");
            }}
            onFocus={() => setPanel("replay")}
          >{String(hour).padStart(2, "0")}:00 · {speed}×</button>
        ) : null}

        {fatalError && !map && (
          <section className="fatal-card surface" role="alert" onClick={(event) => event.stopPropagation()}>
            <h1>{errorLabels[fatalError.status] ?? "暂时无法连接"}</h1>
            <p>没有可显示的业务数据。</p>
            <button ref={retryButtonRef} type="button" className="primary-button" onClick={retry}>重试</button>
          </section>
        )}
      </div>
    </main>
  );
}
