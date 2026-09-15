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
  canDispatchStation,
  canDrawForecast,
  canDrawInventory,
  isExpiredAt,
  isMapExpired,
  isTraversalStatus,
  nextAvailableHour,
  particleOffsets,
  playbackDelay,
  sameReplaySelection,
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
  baseline_dataset_id: string | null;
  snapshot_id: string | null;
  metadata_version: string | null;
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

const delay = (signal: AbortSignal, milliseconds = 90) =>
  new Promise<void>((resolve, reject) => {
    const id = window.setTimeout(resolve, milliseconds);
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

  await delay(signal, refresh && fixtureScenario === "refresh_failure" ? 650 : 90);
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
    }
    if (fixtureScenario === "expired_suggestion" && response.suggestions[0]) {
      response.suggestions[0].expires_at_utc = response.as_of_utc;
    }
    return response;
  }

  const response = cloneExample<MapResponse>("replay");
  if (fixtureScenario === "no_coordinates" && response.stations[0]) {
    response.stations[0].lat = null;
    response.stations[0].lon = null;
  }
  if (selection) {
    const isExampleHour =
      selection.serviceDate === response.service_date && selection.hour === response.hour;
    response.service_date = selection.serviceDate;
    response.hour = selection.hour;
    if (!isExampleHour) response.flows = [];
    if (fixtureScenario === "replay_not_found" && selection.hour === 1) {
      throw new ApiFailure(404, "NOT_FOUND", "fixture replay date/hour unavailable");
    }
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
  if (fixtureScenario === "history_failure") {
    throw new ApiFailure(503, "DATA_UNAVAILABLE", "fixture history failure");
  }
  const hasProfile = station.station_id === "5484.09" && fixtureScenario !== "no_baseline";
  const response = cloneExample<HistoryResponse>(hasProfile ? "history" : "empty_history");
  const exampleDate = response.service_date;
  response.station_id = station.station_id;
  response.station_name = station.station_name;
  response.service_date = serviceDate;
  if (!serviceDate || serviceDate !== exampleDate) response.actual = [];
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

const transientDetails: Record<string, string> = {
  "查询范围过大": "请缩小查询范围后重试。",
  "暂无可用历史": "当前没有可回放的日期。",
  "该小时不可用": "已保留当前合法小时。",
  "该日期不可用": "已保留当前合法日期。",
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

function formatNewYorkDateTime(value: string | null) {
  if (!value) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "America/New_York",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function newYorkHour(value: string | null) {
  if (!value) return null;
  return Number(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value)));
}

function compactLabel(value: string, limit = 10) {
  const characters = Array.from(value);
  return characters.length > limit
    ? `${characters.slice(0, limit - 1).join("")}…`
    : value;
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

function displayStatus(station: Station, mode: Mode, view: View, expired: boolean) {
  if (mode === "replay") return "NOT_APPLICABLE";
  if (expired) return "STALE_DATA";
  return view === "current" ? station.current_status : station.forecast_status;
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
        <marker id="dispatch-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" orient="auto-start-reverse">
          <path d="M0 0 10 5 0 10Z" fill="#f5a000" />
        </marker>
        <marker id="replay-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" orient="auto-start-reverse">
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
        map.fitBounds(located.map(({ lat, lon }) => [lat!, lon!]), {
          paddingTopLeft: [180, 110],
          paddingBottomRight: [180, 130],
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
  suggestions,
  elapsedMs,
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
  suggestions: Suggestion[];
  elapsedMs: number;
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
    map.mode === "live" && view === "dispatch" && !expired ? suggestions : [];
  const stationById = new Map(map.stations.map((station) => [station.station_id, station]));
  const selectedSuggestion = dispatches.find(
    ({ suggestion_id }) => suggestion_id === selectedSuggestionId,
  );
  const renderDispatch = (suggestion: Suggestion, overlay = false) => {
    const from = positions.get(suggestion.from_station_id);
    const to = positions.get(suggestion.to_station_id);
    if (!from || !to) return null;
    const selected = suggestion.suggestion_id === selectedSuggestionId;
    const path = curvePath(from, to, suggestion.suggestion_id);
    const routeWidth = Math.min(7, 2.5 + suggestion.move_bikes * 0.25);
    const labelWidth = 320;
    const labelX = Math.min(width - labelWidth - 16, Math.max(16, (from.x + to.x) / 2 - labelWidth / 2));
    const labelY = Math.min(height - 72, Math.max(16, (from.y + to.y) / 2 - 96));
    const fromStation = stationById.get(suggestion.from_station_id);
    const toStation = stationById.get(suggestion.to_station_id);
    const fromName = fromStation?.station_name || suggestion.from_station_id;
    const toName = toStation?.station_name || suggestion.to_station_id;
    const routeReason = `依据：+1h ${statusLabels[fromStation?.forecast_status ?? ""] ?? "可调出"} → ${statusLabels[toStation?.forecast_status ?? ""] ?? "需补车"}`;
    return (
      <g
        key={`${suggestion.suggestion_id}:${overlay ? "overlay" : "base"}`}
        className={`${selected ? "route selected" : suggestion.priority <= 5 ? "route top" : "route"} ${overlay ? "route-overlay" : ""}`}
        aria-hidden={overlay || undefined}
      >
        <path
          className="route-line"
          d={path}
          markerEnd="url(#dispatch-arrow)"
          style={{ strokeWidth: selected ? routeWidth + 2 : routeWidth }}
        />
        {!overlay && (
          <path
            className="route-hit"
            d={path}
            role="button"
            tabIndex={0}
            aria-label={`${fromName}（${suggestion.from_station_id}）到 ${toName}（${suggestion.to_station_id}），调度 ${suggestion.move_bikes} 辆，${suggestion.distance_meters} 米；${routeReason}；按方括号键巡览建议`}
            onClick={(event) => { event.stopPropagation(); onSelectSuggestion(suggestion.suggestion_id); }}
            onFocus={() => onSelectSuggestion(suggestion.suggestion_id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelectSuggestion(suggestion.suggestion_id);
              }
            }}
          />
        )}
        {overlay && (
          <g className="route-label" transform={`translate(${labelX} ${labelY})`}>
            <rect width={labelWidth} height="56" rx="12" />
            <text x="14" y="22">{compactLabel(fromName)} → {compactLabel(toName)} · {suggestion.move_bikes} 辆 · {suggestion.distance_meters} m</text>
            <text className="route-reason" x="14" y="43">{routeReason}</text>
          </g>
        )}
      </g>
    );
  };
  return (
    <svg className="map-svg" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="运营地图数据图层">
      <MapBackdrop background={false} />

      <g className="routes">
        {dispatches.map((suggestion) => renderDispatch(suggestion))}

        {map.mode === "replay" && map.flows.map((flow) => {
          const from = positions.get(flow.from_station_id);
          const to = positions.get(flow.to_station_id);
          if (!from || !to) return null;
          const key = `${flow.from_station_id}:${flow.to_station_id}`;
          const path = curvePath(from, to, key);
          const pathId = `flow-${stableHash(key)}`;
          return (
            <g key={key} className="flow-route" role="img" aria-label={`${flow.from_station_id} 到 ${flow.to_station_id}，${flow.ride_count} 次骑行`}>
              <path id={pathId} d={path} markerEnd="url(#replay-arrow)" style={{ strokeWidth: Math.min(8, 2.5 + Math.sqrt(flow.ride_count)) }} />
              {[34, 68].map((offset) => (
                <text key={offset} className="flow-arrow" aria-hidden="true"><textPath href={`#${pathId}`} startOffset={`${offset}%`}>›</textPath></text>
              ))}
            </g>
          );
        })}
      </g>

      <g className="stations" aria-hidden="true">
        {map.stations.map((station) => {
          const position = positions.get(station.station_id);
          if (!position) return null;
          const stationExpired = expired || isExpiredAt(map, station.expires_at_utc, elapsedMs);
          const status = displayStatus(station, map.mode, view, stationExpired);
          const kind = statusClass(status);
          const showInventory = map.mode === "live" && canDrawInventory(station, stationExpired);
          const routeEndpoint = selectedSuggestion && [
            selectedSuggestion.from_station_id,
            selectedSuggestion.to_station_id,
          ].includes(station.station_id);
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
              {routeEndpoint && <circle className="route-endpoint-ring" r="18" />}
              {kind === "overflow" ? <rect className="station-anchor" x="-9" y="-9" width="18" height="18" rx="3" /> : <circle className="station-anchor" r="9" />}
            </g>
          );
        })}
      </g>

      {selectedSuggestion && renderDispatch(selectedSuggestion, true)}

      <g className="map-labels" aria-hidden="true">
        {map.mode === "replay" && map.flows.map((flow) => {
          const from = positions.get(flow.from_station_id);
          const to = positions.get(flow.to_station_id);
          if (!from || !to) return null;
          return (
            <g key={`${flow.from_station_id}:${flow.to_station_id}`} className="flow-label" transform={`translate(${(from.x + to.x) / 2 - 39} ${(from.y + to.y) / 2 - 36})`}>
              <rect width="78" height="34" rx="10" />
              <text x="39" y="22" textAnchor="middle">{flow.ride_count} 次骑行</text>
            </g>
          );
        })}
        {map.stations.map((station) => {
          const position = positions.get(station.station_id);
          const showLabel = station.station_id === selectedStationId || station.station_id === hoveredStationId;
          if (!position || !showLabel) return null;
          return (
            <g key={station.station_id} className="station-label" transform={`translate(${position.x - 78} ${position.y - 64})`}>
              <rect width="156" height="40" rx="10" />
              <text x="12" y="25">{station.station_name || station.station_id}</text>
            </g>
          );
        })}
      </g>
    </svg>
  );
}

function FlowChart({ history, replay, currentHour }: { history: HistoryResponse; replay: boolean; currentHour: number | null }) {
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
      <svg className="flow-chart" viewBox="0 0 340 105" role="img" aria-label={`24 小时流入、流出和净流量曲线${currentHour === null ? "" : `，当前 ${String(currentHour).padStart(2, "0")} 时`}`}>
        <path className="chart-axis" d="M24 25V78H322M24 61H322" />
        {currentHour !== null && <>
          <path className="chart-current" d={`M${24 + (currentHour / 23) * 292} 25V78`} />
          <text className="chart-current-label" x={24 + (currentHour / 23) * 292} y="18" textAnchor="middle">当前</text>
        </>}
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
  const [historyError, setHistoryError] = useState<ApiFailure | null>(null);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fatalError, setFatalError] = useState<ApiFailure | null>(null);
  const [refreshError, setRefreshError] = useState<ApiFailure | null>(null);
  const [transientMessage, setTransientMessage] = useState<string | null>(null);
  const [statusHeld, setStatusHeld] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof speeds)[number]>(1);
  const [replayActivity, setReplayActivity] = useState(0);
  const [replayHeld, setReplayHeld] = useState(false);
  const [clockElapsedMs, setClockElapsedMs] = useState(0);

  const mapRef = useRef<MapResponse | null>(null);
  const mapAbortRef = useRef<AbortController | null>(null);
  const mapRequestRef = useRef(0);
  const modeRequestRef = useRef(0);
  const historyAbortRef = useRef<AbortController | null>(null);
  const replayCacheRef = useRef(new Map<string, MapResponse>());
  const replayDatasetRef = useRef<string | null>(null);
  const mapReceivedAtRef = useRef(performance.now());
  const panelTriggerRef = useRef<HTMLButtonElement | null>(null);
  const searchButtonRef = useRef<HTMLButtonElement>(null);
  const modeButtonRef = useRef<HTMLButtonElement>(null);
  const viewButtonRef = useRef<HTMLButtonElement>(null);
  const statusButtonRef = useRef<HTMLButtonElement>(null);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const lensRef = useRef<HTMLElement>(null);
  const replayRef = useRef<HTMLDivElement>(null);
  const replayDateRef = useRef<HTMLInputElement>(null);
  const stationRefs = useRef(new Map<string, HTMLButtonElement>());

  const applyAvailability = useCallback((response: AvailabilityResponse) => {
    if (replayDatasetRef.current !== response.dataset_id) replayCacheRef.current.clear();
    replayDatasetRef.current = response.dataset_id;
    setAvailability(response);
  }, []);

  const loadMap = useCallback(async (
    targetMode: Mode,
    selection?: { serviceDate: string; hour: number },
    refresh = false,
  ) => {
    const request = ++mapRequestRef.current;
    mapAbortRef.current?.abort();
    const controller = new AbortController();
    mapAbortRef.current = controller;
    const previous = mapRef.current;
    const keepsMap = previous?.mode === targetMode;
    if (!keepsMap) setLoading(true);
    setRefreshing(keepsMap);
    setTransientMessage(null);
    try {
      const cacheKey = targetMode === "replay" && selection
        ? `${replayDatasetRef.current}:${selection.serviceDate}:${selection.hour}`
        : null;
      const cached = !refresh && cacheKey ? replayCacheRef.current.get(cacheKey) : undefined;
      const response = cached ?? await getMap(targetMode, controller.signal, selection, refresh);
      assertMapSelection(response, targetMode, selection);
      if (request !== mapRequestRef.current) return;
      if (cacheKey) replayCacheRef.current.set(cacheKey, response);
      mapRef.current = response;
      mapReceivedAtRef.current = performance.now();
      setMap(response);
      setClockElapsedMs(0);
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
      if (previous && previous.mode === targetMode) {
        if (targetMode === "replay") setPlaying(false);
        const changedReplaySelection = targetMode === "replay" && selection &&
          !sameReplaySelection(previous, selection);
        if (targetMode === "replay" && selection) {
          setServiceDate(previous.service_date);
          setHour(previous.hour ?? 0);
          if (changedReplaySelection) setSelectedStationId(null);
        }
        if (targetMode === "replay" && selection && [400, 404, 422].includes(failure.status)) {
          setTransientMessage(errorLabels[failure.status]);
          setRefreshError(null);
          setSelectedStationId(null);
          panelTriggerRef.current = statusButtonRef.current;
          setPanel("status");
          if (failure.status === 404) {
            try {
              const currentAvailability = await getAvailability(controller.signal);
              if (request === mapRequestRef.current) applyAvailability(currentAvailability);
            } catch {
              // Keep the last known availability; the restored selection is still legal.
            }
          }
        } else {
          setRefreshError(failure);
        }
      } else {
        setPanel(null);
        setFatalError(failure);
      }
    } finally {
      if (request === mapRequestRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [applyAvailability]);

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
    setReplayHeld(false);
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
    setAvailability(null);
    setServiceDate("");
    setHour(0);
    setLoading(true);
    const controller = new AbortController();
    mapAbortRef.current = controller;
    try {
      const response = await getAvailability(controller.signal);
      if (request !== modeRequestRef.current) return;
      applyAvailability(response);
      if (!response.dates.length) {
        setLoading(false);
        setTransientMessage("暂无可用历史");
        return;
      }
      const date = response.dates.at(-1)!;
      const selectedHour = date.hours[0];
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
    if (pause) setPlaying(false);
    if (!serviceDate || !availableHours.includes(nextHour)) {
      setTransientMessage("该小时不可用");
      panelTriggerRef.current = statusButtonRef.current;
      setPanel("status");
      return;
    }
    setHour(nextHour);
    if (pause) setReplayActivity((value) => value + 1);
    void loadMap("replay", { serviceDate, hour: nextHour });
  }, [availableHours, loadMap, serviceDate]);

  useEffect(() => {
    if (!playing || mode !== "replay") return;
    const timer = window.setTimeout(() => {
      const next = nextAvailableHour(availableHours, hour);
      if (next === null) {
        setPlaying(false);
        setSelectedStationId(null);
        setPanel("replay");
        return;
      }
      selectHour(next, false);
    }, playbackDelay(speed));
    return () => window.clearTimeout(timer);
  }, [availableHours, hour, mode, playing, selectHour, speed]);

  useEffect(() => {
    if (!playing || panel !== "replay" || replayHeld) return;
    const timer = window.setTimeout(() => {
      if (!replayRef.current?.contains(document.activeElement)) setPanel(null);
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [panel, playing, replayActivity, replayHeld]);

  const selectedStation = map?.stations.find(({ station_id }) => station_id === selectedStationId) ?? null;
  useEffect(() => {
    historyAbortRef.current?.abort();
    setHistory(null);
    setHistoryError(null);
    if (!selectedStation) {
      setHistoryLoading(false);
      return;
    }
    const controller = new AbortController();
    historyAbortRef.current = controller;
    setHistoryLoading(true);
    const date = mode === "replay" ? serviceDate : null;
    void getHistory(selectedStation, isoWeekday(date ?? map?.service_date ?? "2025-01-01"), date, controller.signal)
      .then((response) => {
        if (!controller.signal.aborted) setHistory(response);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setHistoryError(error instanceof ApiFailure
            ? error
            : new ApiFailure(0, "NETWORK_ERROR", "network failure"));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    window.requestAnimationFrame(() => lensRef.current?.focus());
    return () => controller.abort();
  }, [historyRetry, map?.service_date, mode, selectedStation, serviceDate]);

  const positions = projection.positions;
  const expired = isMapExpired(map, clockElapsedMs);
  const suggestions = useMemo(() => {
    if (!map || map.mode !== "live" || expired) return [];
    const stations = new Map(map.stations.map((station) => [station.station_id, station]));
    return map.suggestions.filter((suggestion) => {
      const from = stations.get(suggestion.from_station_id);
      const to = stations.get(suggestion.to_station_id);
      return from && to &&
        !isExpiredAt(map, suggestion.expires_at_utc, clockElapsedMs) &&
        !isExpiredAt(map, from.expires_at_utc, clockElapsedMs) &&
        !isExpiredAt(map, to.expires_at_utc, clockElapsedMs) &&
        canDispatchStation(from) && canDispatchStation(to);
    });
  }, [clockElapsedMs, expired, map]);

  useEffect(() => {
    if (selectedSuggestionId && !suggestions.some(
      ({ suggestion_id }) => suggestion_id === selectedSuggestionId,
    )) setSelectedSuggestionId(null);
  }, [selectedSuggestionId, suggestions]);

  useEffect(() => {
    if (!map || map.clock_mode !== "wall") return;
    const businessTime = Date.parse(map.as_of_utc) + clockElapsedMs;
    const nextExpiry = Math.min(
      ...[map.expires_at_utc, ...map.stations.map(({ expires_at_utc }) => expires_at_utc), ...map.suggestions.map(({ expires_at_utc }) => expires_at_utc)]
        .filter((value): value is string => Boolean(value))
        .map(Date.parse)
        .filter((value) => Number.isFinite(value) && value > businessTime),
    );
    if (!Number.isFinite(nextExpiry)) return;
    const timer = window.setTimeout(
      () => setClockElapsedMs(Math.max(0, performance.now() - mapReceivedAtRef.current)),
      Math.max(0, nextExpiry - businessTime + 20),
    );
    return () => window.clearTimeout(timer);
  }, [clockElapsedMs, map]);

  const sortedStations = useMemo(
    () => sortStations(
      map?.stations ?? [],
      view,
      (station: Station) => displayStatus(
        station,
        mode,
        view,
        expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs),
      ),
    ) as Station[],
    [clockElapsedMs, expired, map, mode, view],
  );
  const traversalStations = useMemo(
    () => sortedStations.filter((station) => isTraversalStatus(
      displayStatus(
        station,
        mode,
        view,
        expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs),
      ),
      mode,
    )),
    [clockElapsedMs, expired, map, mode, sortedStations, view],
  );
  const searchResults = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return sortedStations
      .filter((station) => !needle || `${station.station_name ?? ""} ${station.station_id}`.toLocaleLowerCase("zh-CN").includes(needle))
      .slice(0, 8);
  }, [query, sortedStations]);

  useEffect(() => setSearchIndex(0), [query]);
  useEffect(() => setSearchIndex((index) => Math.min(index, Math.max(0, searchResults.length - 1))), [searchResults.length]);

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
      if (!event.altKey && !event.ctrlKey && !event.metaKey && event.key.toLocaleLowerCase() === "n" && traversalStations.length) {
        event.preventDefault();
        const index = traversalStations.findIndex(({ station_id }) => station_id === selectedStationId);
        const delta = event.shiftKey ? -1 : 1;
        const next = traversalStations[index < 0
          ? delta > 0 ? 0 : traversalStations.length - 1
          : (index + delta + traversalStations.length) % traversalStations.length];
        openStation(next.station_id);
      }
      if (!event.altKey && !event.ctrlKey && !event.metaKey && view === "dispatch" && suggestions.length && ["[", "]"].includes(event.key)) {
        event.preventDefault();
        const index = suggestions.findIndex(({ suggestion_id }) => suggestion_id === selectedSuggestionId);
        const delta = event.key === "]" ? 1 : -1;
        const next = suggestions[index < 0
          ? delta > 0 ? 0 : suggestions.length - 1
          : (index + delta + suggestions.length) % suggestions.length];
        setSelectedSuggestionId(next.suggestion_id);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closePanel, closeStation, openStation, panel, selectedStationId, selectedSuggestionId, suggestions, traversalStations, view]);

  useEffect(() => {
    if (fatalError && !map) window.requestAnimationFrame(() => retryButtonRef.current?.focus());
  }, [fatalError, map]);

  useEffect(() => {
    if (!transientMessage || statusHeld) return;
    const timer = window.setTimeout(() => {
      setTransientMessage(null);
      setPanel((current) => current === "status" ? null : current);
    }, 4000);
    return () => window.clearTimeout(timer);
  }, [statusHeld, transientMessage]);

  const changeDate = (nextDate: string) => {
    const next = availability?.dates.find(({ service_date }) => service_date === nextDate);
    if (!next) {
      setPlaying(false);
      setTransientMessage("该日期不可用");
      panelTriggerRef.current = statusButtonRef.current;
      setPanel("status");
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

  const locatedStationIds = new Set(
    map?.stations
      .filter(({ lat, lon }) => Number.isFinite(lat) && Number.isFinite(lon))
      .map(({ station_id }) => station_id) ?? [],
  );
  const hiddenFlows = map?.mode === "replay"
    ? map.flows.filter((flow) => !locatedStationIds.has(flow.from_station_id) || !locatedStationIds.has(flow.to_station_id))
    : [];
  const hiddenFlowRides = hiddenFlows.reduce((sum, flow) => sum + flow.ride_count, 0);
  const status = (() => {
    if (loading && !map) return { text: "加载中", kind: "loading" };
    if (fatalError && !map) return { text: errorLabels[fatalError.status] ?? "暂时无法连接", kind: "error" };
    if (transientMessage) return { text: transientMessage, kind: "warning" };
    if (refreshing && map?.mode === "replay" && !refreshError) return { text: `加载回放 · ${serviceDate} ${String(hour).padStart(2, "0")}:00`, kind: "loading" };
    if (expired) return { text: "数据已过期", kind: "error" };
    if (refreshError) return map?.mode === "replay"
      ? { text: `回放加载失败 · 保留 ${map.service_date} ${String(map.hour).padStart(2, "0")}:00`, kind: "warning" }
      : { text: `刷新失败 · ${formatNewYorkTime(map?.observed_at_utc ?? null)}`, kind: "warning" };
    if (map?.mode === "live" && map.stations.length === 0) return { text: "暂无站点", kind: "empty" };
    if (map?.mode === "live" && view === "dispatch" && suggestions.length === 0) return { text: "暂无可行建议", kind: "empty" };
    if (map?.mode === "replay" && map.flows.length === 0) return { text: "本小时无流量", kind: "empty" };
    if (!map && mode === "replay" && availability?.dates.length === 0) return { text: "暂无可用历史", kind: "empty" };
    if (!map) return { text: "加载中", kind: "loading" };
    const source = originLabels[map.data_origin];
    return map.mode === "replay"
      ? { text: `回放 · ${source} · ${map.service_date} ${String(map.hour).padStart(2, "0")}:00${hiddenFlows.length ? ` · 未绘制 ${hiddenFlows.length} 条/${hiddenFlowRides} 次` : ""}`, kind: "replay" }
      : { text: `${view === "forecast" ? "+1h · " : view === "dispatch" ? "调度 · " : ""}${source} · ${formatNewYorkTime(map.observed_at_utc)}`, kind: "live" };
  })();

  const selectedStationExpired = Boolean(selectedStation && (
    expired || isExpiredAt(map, selectedStation.expires_at_utc, clockElapsedMs)
  ));
  const selectedInventoryAvailable = Boolean(
    selectedStation && canDrawInventory(selectedStation, selectedStationExpired),
  );
  const selectedForecastAvailable = Boolean(selectedStation && canDrawForecast(selectedStation, selectedStationExpired));
  const chartHour = mode === "replay" ? hour : newYorkHour(map?.observed_at_utc ?? null);
  const selectedPosition = selectedStation ? positions.get(selectedStation.station_id) : null;
  const lensStyle = selectedPosition
    ? (() => {
        const centerY = Math.min(projection.height - 224, Math.max(224, selectedPosition.y));
        return {
          left: `${(selectedPosition.x / projection.width) * 100}%`,
          top: `${(centerY / projection.height) * 100}%`,
          "--pointer-y": `${Math.min(376, Math.max(24, selectedPosition.y - centerY + 200))}px`,
          transform: `${selectedPosition.x < projection.width / 2 ? "translate(36px, -50%)" : "translate(calc(-100% - 36px), -50%)"}`,
        } as CSSProperties;
      })()
    : undefined;

  const handleMapBackground = () => {
    if (selectedStationId) closeStation();
    else if (panel) closePanel();
    else if (selectedSuggestionId) setSelectedSuggestionId(null);
  };

  return (
    <main className={`app mode-${mode} view-${view}`}>
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
          suggestions={suggestions}
          elapsedMs={clockElapsedMs}
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
          const stationExpired = expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs);
          const label = `${station.station_name || "未命名站点"}，${station.station_id}，${statusLabels[displayStatus(station, mode, view, stationExpired)] ?? "状态未知"}`;
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
            label="搜索站点 · N/Shift+N 巡览"
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
            label={mode === "replay" ? "回放中不提供实时视角" : `地图视角：${view === "current" ? "当前" : view === "forecast" ? "+1h" : "调度"} · [/] 巡览调度`}
            icon="layers"
            active={panel === "view" || view !== "current"}
            disabled={mode === "replay"}
            expanded={panel === "view"}
            onClick={() => panel === "view" ? closePanel() : openPanel("view", viewButtonRef.current)}
          />
        </nav>

        <div
          className="status-area"
          onClick={(event) => event.stopPropagation()}
          onMouseEnter={() => setStatusHeld(true)}
          onMouseLeave={() => setStatusHeld(false)}
          onFocusCapture={() => setStatusHeld(true)}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setStatusHeld(false);
          }}
        >
          <button
            ref={statusButtonRef}
            type="button"
            className={`status-capsule ${status.kind}`}
            aria-expanded={panel === "status"}
            disabled={Boolean(fatalError && !map)}
            onClick={() => panel === "status" ? closePanel() : openPanel("status", statusButtonRef.current)}
          >
            <span className="status-dot" aria-hidden="true" />
            <span>{status.text}</span>
          </button>
          {panel === "status" && (
            <section className="status-panel surface" aria-label="数据状态详情">
              <strong>{status.text}</strong>
              <span>{map ? `契约 ${map.contract_version} · ${originLabels[map.data_origin]}` : "尚未取得可用响应"}</span>
              {!map && mode === "replay" && availability?.dates.length === 0 && <span>当前没有可回放的日期。</span>}
              {map?.observed_at_utc && <span>最后观测 {formatNewYorkTime(map.observed_at_utc)}</span>}
              {map?.dataset_id && <span className="lineage">数据集 <code>{map.dataset_id}</code></span>}
              {map?.baseline_dataset_id && <span className="lineage">基线 <code>{map.baseline_dataset_id}</code></span>}
              {map?.snapshot_id && <span className="lineage">快照 <code>{map.snapshot_id}</code></span>}
              {hiddenFlows.length > 0 && <span>未绘制 {hiddenFlows.length} 条 OD，共 {hiddenFlowRides} 次骑行</span>}
              {transientMessage && <span>{transientDetails[transientMessage] ?? "已恢复上一个合法选择。"}</span>}
              {refreshError && !expired && <span>{map?.mode === "replay" ? "已恢复上一个可用日期和小时。" : "保留上次仍有效的地图数据。"}</span>}
              {(refreshError || expired || fatalError) && <button type="button" className="primary-button" disabled={refreshing || loading} onClick={retry}>{refreshing || loading ? "重试中…" : "重试"}</button>}
              {!refreshError && !expired && !fatalError && !transientMessage && mode === "live" && <button type="button" className="quiet-button" disabled={refreshing} onClick={retry}>{refreshing ? "刷新中…" : "立即刷新"}</button>}
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
                role="combobox"
                aria-expanded="true"
                aria-autocomplete="list"
                aria-controls="station-search-results"
                aria-activedescendant={searchResults[searchIndex] ? `station-option-${stableHash(searchResults[searchIndex].station_id)}` : undefined}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    if (searchResults.length) setSearchIndex((index) => Math.min(index + 1, searchResults.length - 1));
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    if (searchResults.length) setSearchIndex((index) => Math.max(index - 1, 0));
                  } else if (event.key === "Enter" && searchResults[searchIndex]) {
                    event.preventDefault();
                    openStation(searchResults[searchIndex].station_id);
                  }
                }}
              />
              {query && <button type="button" aria-label="清空搜索" onClick={() => setQuery("")}><svg viewBox="0 0 24 24" aria-hidden="true"><Icon name="close" /></svg></button>}
            </label>
            <div id="station-search-results" className="search-results" role="listbox" aria-label="站点结果">
              {searchResults.length ? searchResults.map((station, index) => {
                const stationExpired = expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs);
                const stationStatus = displayStatus(station, mode, view, stationExpired);
                return (
                  <button
                    type="button"
                    role="option"
                    id={`station-option-${stableHash(station.station_id)}`}
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
            <footer>↑↓ 选择 · Enter 打开 · Esc 关闭 · N/Shift+N 站点 · [/] 建议</footer>
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
                {item === "current" ? "当前" : item === "forecast" ? "+1h" : `调度 ${suggestions.length}`}
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
              <span className={`station-state ${statusClass(displayStatus(selectedStation, mode, view, selectedStationExpired))}`}>
                {statusLabels[displayStatus(selectedStation, mode, view, selectedStationExpired)]}
              </span>
              <p>{selectedStation.station_id}{!selectedPosition && " · 无法定位"}</p>
            </header>
            {mode === "live" && (
              <div className="lens-numbers">
                <span
                  className="metric-value"
                  tabIndex={0}
                  aria-label={`当前车辆 ${selectedInventoryAvailable ? selectedStation.num_bikes_available : "不可用"}，观测时间 ${formatNewYorkDateTime(selectedStation.observed_at_utc ?? map?.observed_at_utc ?? null)}`}
                  data-tooltip={`观测 ${formatNewYorkDateTime(selectedStation.observed_at_utc ?? map?.observed_at_utc ?? null)}`}
                ><strong>{selectedInventoryAvailable ? selectedStation.num_bikes_available : "—"}</strong></span>
                <span>→</span><span>+1h</span>
                <span
                  className="metric-value"
                  tabIndex={0}
                  aria-label={`一小时估计 ${selectedForecastAvailable ? selectedStation.projected_bikes_1h : "不可用"}，目标时间 ${formatNewYorkDateTime(selectedStation.forecast_for_utc)}`}
                  data-tooltip={`目标 ${formatNewYorkDateTime(selectedStation.forecast_for_utc)}`}
                ><strong>{selectedForecastAvailable ? selectedStation.projected_bikes_1h : "—"}</strong></span>
                <i />
                <span>空桩</span><strong>{selectedInventoryAvailable ? selectedStation.num_docks_available ?? "—" : "—"}</strong>
              </div>
            )}
            {(selectedStationExpired || selectedStation.current_reason || selectedStation.forecast_reason) && (
              <p className="reason-copy">{selectedStationExpired ? "数据已过期" : reasonLabels[(view === "current" ? selectedStation.current_reason : selectedStation.forecast_reason) ?? ""] ?? "数据暂不可用"}</p>
            )}
            {historyLoading && <p className="lens-loading">正在加载 24 小时曲线…</p>}
            {!historyLoading && historyError && (
              <p className="lens-history-error" role="alert">
                <span>{errorLabels[historyError.status] ?? "历史数据加载失败"}</span>
                <button type="button" onClick={() => setHistoryRetry((value) => value + 1)}>重试</button>
              </p>
            )}
            {!historyLoading && history && <>
              {history.profile.length > 0 ? <FlowChart history={history} replay={mode === "replay"} currentHour={chartHour} /> : <p className="empty-copy lens-empty">暂无历史基线</p>}
              {mode === "replay" && history.actual.length === 0 && <p className="actual-empty">当日无活动</p>}
            </>}
            <footer>
              {history?.profile.length ? <span>{history.profile[0].sample_days} 日样本</span> : null}
              {history?.profile_start_date && <span>{history.profile_start_date} — {history.profile_end_date}</span>}
              {mode === "live" && (selectedStation.observed_at_utc || map?.observed_at_utc) && <span>观测 {formatNewYorkTime(selectedStation.observed_at_utc ?? map?.observed_at_utc ?? null)}</span>}
              {mode === "live" && selectedStation.forecast_for_utc && <span>预测至 {formatNewYorkTime(selectedStation.forecast_for_utc)}</span>}
            </footer>
          </section>
        )}

        {mode === "replay" && availability?.dates.length ? panel === "replay" ? (
          <div
            ref={replayRef}
            className="replay-controller surface"
            aria-label="历史回放控制器"
            onClick={(event) => { event.stopPropagation(); setReplayActivity((value) => value + 1); }}
            onPointerEnter={() => { setReplayHeld(true); setReplayActivity((value) => value + 1); }}
            onPointerLeave={() => setReplayHeld(false)}
            onFocusCapture={() => { setReplayHeld(true); setReplayActivity((value) => value + 1); }}
            onBlurCapture={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setReplayHeld(false);
            }}
          >
            <label className="date-control">
              <span className="sr-only">回放日期</span>
              <input
                ref={replayDateRef}
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
            onPointerEnter={(event) => {
              setReplayHeld(true);
              openPanel("replay", event.currentTarget);
            }}
            onClick={(event) => {
              event.stopPropagation();
              openPanel("replay", event.currentTarget);
              window.requestAnimationFrame(() => replayDateRef.current?.focus());
            }}
            onFocus={(event) => {
              openPanel("replay", event.currentTarget);
              window.requestAnimationFrame(() => replayDateRef.current?.focus());
            }}
          >{String(hour).padStart(2, "0")}:00 · {speed}×</button>
        ) : null}

        {fatalError && !map && (
          <section className="fatal-card surface" role="alert" onClick={(event) => event.stopPropagation()}>
            <h1>{errorLabels[fatalError.status] ?? "暂时无法连接"}</h1>
            <p>没有可显示的业务数据。</p>
            <button ref={retryButtonRef} type="button" className="primary-button" disabled={loading} onClick={retry}>{loading ? "重试中…" : "重试"}</button>
          </section>
        )}
      </div>
    </main>
  );
}
