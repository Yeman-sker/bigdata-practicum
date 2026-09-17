import {
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { parseMap, parseAvailability, parseHistory } from "./parse";
import { PrismMap } from "./PrismMap";
import { buildScene, replayReady } from "./scene";
import type {
  Mode,
  View,
  Station,
  MapResponse,
  AvailabilityResponse,
  HistoryResponse,
} from "./domain";
import examplesJson from "../../fixtures/day2/http-examples.json";
import {
  canDispatchStation,
  canDrawForecast,
  canDrawInventory,
  isExpiredAt,
  isMapExpired,
  isTraversalStatus,
  nextAvailableHour,
  playbackDelay,
  sameReplaySelection,
  sortStations,
  stableHash,
} from "./model.mjs";

type Panel = "status" | null;

type Example = { value: unknown; "x-status"?: number };
const examples = examplesJson as Record<string, Example>;
const pageQuery = new URLSearchParams(window.location.search);
const fixtureParameter = pageQuery.get("fixture");
const usesFixture =
  fixtureParameter !== null || (import.meta.env.DEV && !pageQuery.has("api"));
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

async function requestJson<T>(
  path: string,
  signal: AbortSignal,
  parse: (value: unknown) => T,
): Promise<T> {
  const response = await fetch(path, {
    signal,
    headers: { Accept: "application/json" },
  });
  const body = (await response.json().catch(() => null)) as {
    error?: { code?: string; message?: string };
  } | null;
  if (!response.ok) {
    throw new ApiFailure(
      response.status,
      body?.error?.code ?? "INTERNAL_ERROR",
      body?.error?.message ?? `HTTP ${response.status}`,
    );
  }
  try {
    return parse(body);
  } catch {
    throw new ApiFailure(500, "INVALID_RESPONSE", "接口响应格式无效");
  }
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
    return requestJson(`/api/v1/map?${query}`, signal, parseMap);
  }

  await delay(
    signal,
    refresh && fixtureScenario === "refresh_failure" ? 650 : 90,
  );
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
    const failure = cloneExample<{ error: { code: string; message: string } }>(
      fixtureScenario,
    );
    throw new ApiFailure(
      failures[fixtureScenario],
      failure.error.code,
      failure.error.message,
    );
  }
  if (mode === "live") {
    const source = ["no_baseline", "stale", "empty_live"].includes(
      fixtureScenario,
    )
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
      selection.serviceDate === response.service_date &&
      selection.hour === response.hour;
    response.service_date = selection.serviceDate;
    response.hour = selection.hour;
    if (!isExampleHour) response.flows = [];
    if (fixtureScenario === "replay_not_found" && selection.hour === 1) {
      throw new ApiFailure(
        404,
        "NOT_FOUND",
        "fixture replay date/hour unavailable",
      );
    }
  }
  return response;
}

async function getAvailability(
  signal: AbortSignal,
): Promise<AvailabilityResponse> {
  if (!usesFixture)
    return requestJson(
      "/api/v1/history/availability",
      signal,
      parseAvailability,
    );
  await delay(signal);
  if (fixtureScenario === "availability_failure")
    throw new ApiFailure(
      503,
      "DATA_UNAVAILABLE",
      "fixture availability failure",
    );
  return cloneExample<AvailabilityResponse>(
    fixtureScenario === "empty_availability"
      ? "empty_availability"
      : "availability",
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
      (value) =>
        parseHistory(value, {
          stationId: station.station_id,
          dayOfWeek,
          serviceDate,
        }),
    );
  }

  await delay(signal);
  if (fixtureScenario === "history_failure") {
    throw new ApiFailure(503, "DATA_UNAVAILABLE", "fixture history failure");
  }
  const hasProfile =
    station.station_id === "5484.09" && fixtureScenario !== "no_baseline";
  const response = cloneExample<HistoryResponse>(
    hasProfile ? "history" : "empty_history",
  );
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
      (response.service_date !== selection.serviceDate ||
        response.hour !== selection.hour))
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
  查询范围过大: "请缩小查询范围后重试。",
  暂无可用历史: "当前没有可回放的日期。",
  该小时不可用: "已保留当前合法小时。",
  该日期不可用: "已保留当前合法日期。",
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
  return Number(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value)),
  );
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

function displayStatus(
  station: Station,
  mode: Mode,
  view: View,
  expired: boolean,
) {
  if (mode === "replay") return "NOT_APPLICABLE";
  if (expired) return "STALE_DATA";
  return view === "current" ? station.current_status : station.forecast_status;
}

function FlowChart({
  history,
  replay,
  currentHour,
}: {
  history: HistoryResponse;
  replay: boolean;
  currentHour: number | null;
}) {
  const values = [
    ...history.profile.flatMap((item) => [
      item.avg_inbound,
      item.avg_outbound,
      item.avg_net_flow,
    ]),
    ...history.actual.map((item) => item.net_flow),
  ];
  const extent = Math.max(2, ...values.map((value) => Math.abs(value)));
  const points = (items: { hour: number; value: number }[]) => {
    let previous = -2;
    return [...items]
      .sort((a, b) => a.hour - b.hour)
      .map(({ hour, value }) => {
        const command = hour === previous + 1 ? "L" : "M";
        previous = hour;
        const x = 24 + (hour / 23) * 292,
          y = 52 - (value / extent) * 27;
        return `${command}${x},${y}l0.01,0`;
      })
      .join(" ");
  };
  return (
    <div className="chart-wrap">
      <div className="chart-legend" aria-hidden="true">
        <span className="inbound">入</span>
        <span className="outbound">出</span>
        <span className="net">净</span>
        {replay && <span className="actual">当日净</span>}
      </div>
      <svg
        className="flow-chart"
        viewBox="0 0 340 105"
        role="img"
        aria-label={`24 小时流入、流出和净流量曲线${currentHour === null ? "" : `，当前 ${String(currentHour).padStart(2, "0")} 时`}`}
      >
        <path className="chart-axis" d="M24 25V82H322M24 52H322" />
        {currentHour !== null && (
          <>
            <path
              className="chart-current"
              d={`M${24 + (currentHour / 23) * 292} 25V82`}
            />
            <text
              className="chart-current-label"
              x={24 + (currentHour / 23) * 292}
              y="18"
              textAnchor="middle"
            >
              当前
            </text>
          </>
        )}
        {[0, 8, 16, 23].map((hour) => (
          <text
            key={hour}
            x={24 + (hour / 23) * 292}
            y="98"
            textAnchor="middle"
          >
            {String(hour).padStart(2, "0")}
          </text>
        ))}
        {history.profile.length > 0 && (
          <>
            <path
              className="chart-line inbound"
              d={points(
                history.profile.map((item) => ({
                  hour: item.hour,
                  value: item.avg_inbound,
                })),
              )}
            />
            <path
              className="chart-line outbound"
              d={points(
                history.profile.map((item) => ({
                  hour: item.hour,
                  value: item.avg_outbound,
                })),
              )}
            />
            <path
              className="chart-line net"
              d={points(
                history.profile.map((item) => ({
                  hour: item.hour,
                  value: item.avg_net_flow,
                })),
              )}
            />
          </>
        )}
        {replay && history.actual.length > 0 && (
          <path
            className="chart-line actual"
            d={points(
              history.actual.map((item) => ({
                hour: item.hour,
                value: item.net_flow,
              })),
            )}
          />
        )}
      </svg>
      <details className="history-values">
        <summary>查看小时数值</summary>
        <div className="history-table-wrap">
          <table>
            <caption>基线平均值{replay ? " / 当日实际值" : ""}</caption>
            <thead>
              <tr>
                <th>小时</th>
                <th>流入</th>
                <th>流出</th>
                <th>净流</th>
                <th>样本</th>
              </tr>
            </thead>
            <tbody>
              {history.profile.map((row) => (
                <tr key={`profile-${row.hour}`}>
                  <th>{String(row.hour).padStart(2, "0")}</th>
                  <td>{row.avg_inbound}</td>
                  <td>{row.avg_outbound}</td>
                  <td>{row.avg_net_flow}</td>
                  <td>{row.sample_days} 日</td>
                </tr>
              ))}
              {replay &&
                history.actual.map((row) => (
                  <tr key={`actual-${row.hour}`}>
                    <th>{String(row.hour).padStart(2, "0")} 实际</th>
                    <td>{row.inbound_rides}</td>
                    <td>{row.outbound_rides}</td>
                    <td>{row.net_flow}</td>
                    <td>当日</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

export default function App() {
  const [mode, setMode] = useState<Mode>("live");
  const [view, setView] = useState<View>("current");
  const [map, setMap] = useState<MapResponse | null>(null);
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(
    null,
  );
  const [serviceDate, setServiceDate] = useState<string>("");
  const [hour, setHour] = useState(0);
  const [panel, setPanel] = useState<Panel>(null);
  const [query, setQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [selectedStationId, setSelectedStationId] = useState<string | null>(
    null,
  );
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<
    string | null
  >(null);
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
  const searchInputRef = useRef<HTMLInputElement>(null);
  const statusButtonRef = useRef<HTMLButtonElement>(null);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const stationDetailRef = useRef<HTMLElement>(null);
  const stationTriggerRef = useRef<HTMLElement | null>(null);

  const applyAvailability = useCallback((response: AvailabilityResponse) => {
    if (replayDatasetRef.current !== response.dataset_id)
      replayCacheRef.current.clear();
    replayDatasetRef.current = response.dataset_id;
    const available = {
      ...response,
      dates: response.dates.filter((date) => date.hours.length > 0),
    };
    setAvailability(available);
    return available;
  }, []);

  const loadMap = useCallback(
    async (
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
        const cacheKey =
          targetMode === "replay" && selection
            ? `${replayDatasetRef.current}:${selection.serviceDate}:${selection.hour}`
            : null;
        const cached =
          !refresh && cacheKey
            ? replayCacheRef.current.get(cacheKey)
            : undefined;
        const response =
          cached ??
          (await getMap(targetMode, controller.signal, selection, refresh));
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
          current &&
          response.suggestions.some(
            ({ suggestion_id }) => suggestion_id === current,
          )
            ? current
            : null,
        );
        setSelectedStationId((current) =>
          current &&
          response.stations.some(({ station_id }) => station_id === current)
            ? current
            : null,
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError")
          return;
        if (request !== mapRequestRef.current) return;
        const failure =
          error instanceof ApiFailure
            ? error
            : new ApiFailure(0, "NETWORK_ERROR", "network failure");
        if (previous && previous.mode === targetMode) {
          if (targetMode === "replay") setPlaying(false);
          const changedReplaySelection =
            targetMode === "replay" &&
            selection &&
            !sameReplaySelection(previous, selection);
          if (targetMode === "replay" && selection) {
            setServiceDate(previous.service_date);
            setHour(previous.hour ?? 0);
            if (changedReplaySelection) setSelectedStationId(null);
          }
          if (
            targetMode === "replay" &&
            selection &&
            [400, 404, 422].includes(failure.status)
          ) {
            setTransientMessage(errorLabels[failure.status]);
            setRefreshError(null);
            setSelectedStationId(null);
            panelTriggerRef.current = statusButtonRef.current;
            setPanel("status");
            if (failure.status === 404) {
              try {
                const currentAvailability = await getAvailability(
                  controller.signal,
                );
                if (request === mapRequestRef.current)
                  applyAvailability(currentAvailability);
              } catch {}
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
    },
    [applyAvailability],
  );

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
    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loadMap, mode]);

  const closePanel = useCallback((restoreFocus = true) => {
    setPanel(null);
    setStatusHeld(false);
    if (restoreFocus)
      window.requestAnimationFrame(() => panelTriggerRef.current?.focus());
  }, []);

  const openPanel = (
    next: Exclude<Panel, null>,
    trigger: HTMLButtonElement | null,
  ) => {
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
      const available = applyAvailability(response);
      if (!available.dates.length) {
        setLoading(false);
        setTransientMessage("暂无可用历史");
        return;
      }
      const date = available.dates.at(-1)!;
      const selectedHour = date.hours[0];
      setServiceDate(date.service_date);
      setHour(selectedHour);
      await loadMap("replay", {
        serviceDate: date.service_date,
        hour: selectedHour,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (request !== modeRequestRef.current) return;
      setFatalError(
        error instanceof ApiFailure
          ? error
          : new ApiFailure(0, "NETWORK_ERROR", "network failure"),
      );
      setLoading(false);
    }
  };

  const currentDate = availability?.dates.find(
    ({ service_date }) => service_date === serviceDate,
  );
  const availableHours = currentDate?.hours ?? [];

  const selectHour = useCallback(
    (nextHour: number, pause = true) => {
      if (pause) setPlaying(false);
      if (!serviceDate || !availableHours.includes(nextHour)) {
        setTransientMessage("该小时不可用");
        panelTriggerRef.current = statusButtonRef.current;
        setPanel("status");
        return;
      }
      setHour(nextHour);
      void loadMap("replay", { serviceDate, hour: nextHour });
    },
    [availableHours, loadMap, serviceDate],
  );

  useEffect(() => {
    if (
      !playing ||
      mode !== "replay" ||
      !replayReady(map, { serviceDate, hour }, loading || refreshing)
    )
      return;
    const timer = window.setTimeout(() => {
      const next = nextAvailableHour(availableHours, hour);
      if (next === null) {
        setPlaying(false);
        setSelectedStationId(null);
        return;
      }
      selectHour(next, false);
    }, playbackDelay(speed));
    return () => window.clearTimeout(timer);
  }, [
    availableHours,
    hour,
    mode,
    playing,
    selectHour,
    speed,
    map,
    serviceDate,
    loading,
    refreshing,
  ]);

  const selectedStation =
    map?.stations.find(({ station_id }) => station_id === selectedStationId) ??
    null;
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
    const date = mode === "replay" ? (map?.service_date ?? null) : null;
    void getHistory(
      selectedStation,
      isoWeekday(date ?? map?.service_date ?? "2025-01-01"),
      date,
      controller.signal,
    )
      .then((response) => {
        if (!controller.signal.aborted) setHistory(response);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setHistoryError(
            error instanceof ApiFailure
              ? error
              : new ApiFailure(0, "NETWORK_ERROR", "network failure"),
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [historyRetry, map?.service_date, mode, selectedStation]);

  const expired = isMapExpired(map, clockElapsedMs);
  const suggestions = useMemo(() => {
    if (!map || map.mode !== "live" || expired) return [];
    const stations = new Map(
      map.stations.map((station) => [station.station_id, station]),
    );
    return map.suggestions.filter((suggestion) => {
      const from = stations.get(suggestion.from_station_id);
      const to = stations.get(suggestion.to_station_id);
      return (
        from &&
        to &&
        !isExpiredAt(map, suggestion.expires_at_utc, clockElapsedMs) &&
        !isExpiredAt(map, from.expires_at_utc, clockElapsedMs) &&
        !isExpiredAt(map, to.expires_at_utc, clockElapsedMs) &&
        canDispatchStation(from) &&
        canDispatchStation(to)
      );
    });
  }, [clockElapsedMs, expired, map]);

  useEffect(() => {
    if (
      selectedSuggestionId &&
      !suggestions.some(
        ({ suggestion_id }) => suggestion_id === selectedSuggestionId,
      )
    )
      setSelectedSuggestionId(null);
  }, [selectedSuggestionId, suggestions]);

  useEffect(() => {
    if (!map || map.clock_mode !== "wall") return;
    const businessTime = Date.parse(map.as_of_utc) + clockElapsedMs;
    const nextExpiry = Math.min(
      ...[
        map.expires_at_utc,
        ...map.stations.map(({ expires_at_utc }) => expires_at_utc),
        ...map.suggestions.map(({ expires_at_utc }) => expires_at_utc),
      ]
        .filter((value): value is string => Boolean(value))
        .map(Date.parse)
        .filter((value) => Number.isFinite(value) && value > businessTime),
    );
    if (!Number.isFinite(nextExpiry)) return;
    const timer = window.setTimeout(
      () =>
        setClockElapsedMs(
          Math.max(0, performance.now() - mapReceivedAtRef.current),
        ),
      Math.max(0, nextExpiry - businessTime + 20),
    );
    return () => window.clearTimeout(timer);
  }, [clockElapsedMs, map]);

  const sortedStations = useMemo(
    () =>
      sortStations(map?.stations ?? [], view, (station: Station) =>
        displayStatus(
          station,
          mode,
          view,
          expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs),
        ),
      ) as Station[],
    [clockElapsedMs, expired, map, mode, view],
  );
  const traversalStations = useMemo(
    () =>
      sortedStations.filter((station) =>
        isTraversalStatus(
          displayStatus(
            station,
            mode,
            view,
            expired || isExpiredAt(map, station.expires_at_utc, clockElapsedMs),
          ),
          mode,
        ),
      ),
    [clockElapsedMs, expired, map, mode, sortedStations, view],
  );
  const searchResults = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    return sortedStations.filter(
      (station) =>
        !needle ||
        `${station.station_name ?? ""} ${station.station_id}`
          .toLocaleLowerCase("zh-CN")
          .includes(needle),
    );
  }, [query, sortedStations]);

  useEffect(() => setSearchIndex(0), [query]);
  useEffect(
    () =>
      setSearchIndex((index) =>
        Math.min(index, Math.max(0, searchResults.length - 1)),
      ),
    [searchResults.length],
  );

  const openStation = useCallback((stationId: string) => {
    stationTriggerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setPanel(null);
    setSelectedStationId(stationId);
    setTransientMessage(null);
    window.requestAnimationFrame(() => stationDetailRef.current?.focus());
  }, []);

  const closeStation = useCallback(() => {
    setSelectedStationId(null);
    window.requestAnimationFrame(() => {
      const trigger = stationTriggerRef.current;
      if (trigger?.isConnected) trigger.focus();
      else if (trigger?.id && document.getElementById(trigger.id))
        document.getElementById(trigger.id)?.focus();
      else searchInputRef.current?.focus();
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
      if (
        !event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        event.key.toLocaleLowerCase() === "n" &&
        traversalStations.length
      ) {
        event.preventDefault();
        const index = traversalStations.findIndex(
          ({ station_id }) => station_id === selectedStationId,
        );
        const delta = event.shiftKey ? -1 : 1;
        const next =
          traversalStations[
            index < 0
              ? delta > 0
                ? 0
                : traversalStations.length - 1
              : (index + delta + traversalStations.length) %
                traversalStations.length
          ];
        openStation(next.station_id);
      }
      if (
        !event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        view === "dispatch" &&
        suggestions.length &&
        ["[", "]"].includes(event.key)
      ) {
        event.preventDefault();
        const index = suggestions.findIndex(
          ({ suggestion_id }) => suggestion_id === selectedSuggestionId,
        );
        const delta = event.key === "]" ? 1 : -1;
        const next =
          suggestions[
            index < 0
              ? delta > 0
                ? 0
                : suggestions.length - 1
              : (index + delta + suggestions.length) % suggestions.length
          ];
        setSelectedStationId(null);
        setSelectedSuggestionId(next.suggestion_id);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    closePanel,
    closeStation,
    openStation,
    panel,
    selectedStationId,
    selectedSuggestionId,
    suggestions,
    traversalStations,
    view,
  ]);

  useEffect(() => {
    if (fatalError && !map)
      window.requestAnimationFrame(() => retryButtonRef.current?.focus());
  }, [fatalError, map]);

  useEffect(() => {
    if (!transientMessage || statusHeld) return;
    const timer = window.setTimeout(() => {
      setTransientMessage(null);
      setPanel((current) => (current === "status" ? null : current));
    }, 4000);
    return () => window.clearTimeout(timer);
  }, [statusHeld, transientMessage]);

  const changeDate = (nextDate: string) => {
    const next = availability?.dates.find(
      ({ service_date }) => service_date === nextDate,
    );
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
  const hiddenFlows =
    map?.mode === "replay"
      ? map.flows.filter(
          (flow) =>
            !locatedStationIds.has(flow.from_station_id) ||
            !locatedStationIds.has(flow.to_station_id),
        )
      : [];
  const hiddenFlowRides = hiddenFlows.reduce(
    (sum, flow) => sum + flow.ride_count,
    0,
  );
  const status = (() => {
    if (loading && !map) return { text: "加载中", kind: "loading" };
    if (fatalError && !map)
      return {
        text: errorLabels[fatalError.status] ?? "暂时无法连接",
        kind: "error",
      };
    if (transientMessage) return { text: transientMessage, kind: "warning" };
    if (refreshing && map?.mode === "replay" && !refreshError)
      return {
        text: `加载回放 · ${serviceDate} ${String(hour).padStart(2, "0")}:00`,
        kind: "loading",
      };
    if (expired) return { text: "数据已过期", kind: "error" };
    if (refreshError)
      return map?.mode === "replay"
        ? {
            text: `回放加载失败 · 保留 ${map.service_date} ${String(map.hour).padStart(2, "0")}:00`,
            kind: "warning",
          }
        : {
            text: `刷新失败 · ${formatNewYorkTime(map?.observed_at_utc ?? null)}`,
            kind: "warning",
          };
    if (map?.mode === "live" && map.stations.length === 0)
      return { text: "暂无站点", kind: "empty" };
    if (map?.mode === "live" && view === "dispatch" && suggestions.length === 0)
      return { text: "暂无可行建议", kind: "empty" };
    if (map?.mode === "replay" && map.flows.length === 0)
      return { text: "本小时无流量", kind: "empty" };
    if (!map && mode === "replay" && availability?.dates.length === 0)
      return { text: "暂无可用历史", kind: "empty" };
    if (!map) return { text: "加载中", kind: "loading" };
    const source = originLabels[map.data_origin];
    return map.mode === "replay"
      ? {
          text: `回放 · ${source} · ${map.service_date} ${String(map.hour).padStart(2, "0")}:00${hiddenFlows.length ? ` · 未绘制 ${hiddenFlows.length} 条/${hiddenFlowRides} 次` : ""}`,
          kind: "replay",
        }
      : {
          text: `${view === "forecast" ? "+1h · " : view === "dispatch" ? "调度 · " : ""}${source} · ${formatNewYorkTime(map.observed_at_utc)}`,
          kind: "live",
        };
  })();

  const selectedStationExpired = Boolean(
    selectedStation &&
    (expired ||
      isExpiredAt(map, selectedStation.expires_at_utc, clockElapsedMs)),
  );
  const selectedInventoryAvailable = Boolean(
    selectedStation &&
    canDrawInventory(selectedStation, selectedStationExpired),
  );
  const selectedForecastAvailable = Boolean(
    selectedStation && canDrawForecast(selectedStation, selectedStationExpired),
  );
  const chartHour =
    mode === "replay"
      ? (map?.hour ?? null)
      : newYorkHour(map?.observed_at_utc ?? null);
  const selectedPosition =
    selectedStation &&
    Number.isFinite(selectedStation.lat) &&
    Number.isFinite(selectedStation.lon);
  const scene = useMemo(
    () => buildScene(map, view, clockElapsedMs, suggestions),
    [map, view, clockElapsedMs, suggestions],
  );
  const handleMapBackground = () => {
    if (selectedStationId) closeStation();
    else if (panel) closePanel();
    else if (selectedSuggestionId) setSelectedSuggestionId(null);
  };

  const viewLabel =
    view === "current"
      ? "当前库存"
      : view === "forecast"
        ? "一小时预测"
        : "调度建议";
  const selectedSuggestion = suggestions.find(
    ({ suggestion_id }) => suggestion_id === selectedSuggestionId,
  );
  const stationName = (id: string) =>
    map?.stations.find(({ station_id }) => station_id === id)?.station_name ||
    id;

  return (
    <main className={`app mode-${mode} view-${view}`}>
      <PrismMap
        scene={scene}
        selectedStationId={selectedStationId}
        selectedSuggestionId={selectedSuggestionId}
        onSelectStation={openStation}
        onBackground={handleMapBackground}
      />
      <header className="app-header">
        <div className="brand">
          <span className="brand-symbol" aria-hidden="true">
            ◈
          </span>
          <strong>
            PRISM <span>/ NYC</span>
          </strong>
        </div>
        <div className="source-summary">
          <strong>
            {usesFixture
              ? "FIXTURE · 开发样例"
              : map
                ? `${map.data_origin} · ${originLabels[map.data_origin]}`
                : "业务接口"}
            {map?.clock_mode === "recorded" ? " · 录制时钟" : ""}
          </strong>
          <span>
            {map?.mode === "replay"
              ? `${map.service_date} · ${String(map.hour).padStart(2, "0")}:00`
              : `观测 ${formatNewYorkDateTime(map?.observed_at_utc ?? null)}`}{" "}
            · 纽约当地时间
          </span>
        </div>
      </header>
      <aside className="work-pane" aria-label="站点与建议">
        <div className="instrument-heading">
          <h1>棱镜空间</h1>
          <span>城市单车 / 01</span>
        </div>
        <nav className="workspace-nav" aria-label="运营模式与地图视角">
          <div className="mode-switch">
            <button
              aria-pressed={mode === "live"}
              onClick={() => {
                if (mode !== "live") void toggleMode();
              }}
            >
              实时
            </button>
            <button
              aria-pressed={mode === "replay"}
              onClick={() => {
                if (mode !== "replay") void toggleMode();
              }}
            >
              回放
            </button>
          </div>
          {mode === "live" ? (
            <div className="view-switch">
              {(["current", "forecast", "dispatch"] as const).map((item) => (
                <button
                  key={item}
                  aria-pressed={view === item}
                  onClick={() => {
                    setView(item);
                    setSelectedStationId(null);
                    setSelectedSuggestionId(null);
                  }}
                >
                  {item === "current"
                    ? "当前库存"
                    : item === "forecast"
                      ? "预测 +1h"
                      : "调度建议"}
                </button>
              ))}
            </div>
          ) : (
            <span className="nav-note">历史 OD · 纽约当地日期与小时</span>
          )}
        </nav>
        <div className={`status-strip ${status.kind}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          <span>{status.text}</span>
          {map?.expires_at_utc && (
            <small>快照到期 {formatNewYorkTime(map.expires_at_utc)}</small>
          )}
          {(refreshError || expired) && (
            <button
              className="quiet-button"
              disabled={refreshing || loading}
              onClick={retry}
            >
              {refreshing ? "重试中…" : "重试"}
            </button>
          )}
        </div>
        {mode === "replay" && Boolean(availability?.dates.length) && (
          <div className="replay-controller" aria-label="历史回放控制器">
            <label className="date-control">
              <span className="sr-only">回放日期</span>
              <input
                type="date"
                value={serviceDate}
                min={availability?.dates[0]?.service_date}
                max={availability?.dates.at(-1)?.service_date}
                onChange={(event) => changeDate(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="play-button"
              aria-label={playing ? "暂停回放" : "播放回放"}
              onClick={() => setPlaying((value) => !value)}
            >
              {playing ? "Ⅱ" : "▶"}
            </button>
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
                {availableHours.map((availableHour) => (
                  <option key={availableHour} value={availableHour} />
                ))}
              </datalist>
              <span className="timeline-labels">
                <span>00</span>
                <strong>{String(hour).padStart(2, "0")}</strong>
                <span>23</span>
              </span>
            </label>
            <strong className="current-hour">
              {String(map?.hour ?? hour).padStart(2, "0")}:00
            </strong>
            <button
              type="button"
              className="speed-button"
              aria-label={`回放速度 ${speed} 倍，点击切换`}
              onClick={() =>
                setSpeed(speeds[(speeds.indexOf(speed) + 1) % speeds.length])
              }
              onKeyDown={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
                if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
                event.preventDefault();
                const delta = event.key === "ArrowRight" ? 1 : -1;
                setSpeed(
                  speeds[
                    (speeds.indexOf(speed) + delta + speeds.length) %
                      speeds.length
                  ],
                );
              }}
            >
              {speed}×
            </button>
          </div>
        )}

        <button
          ref={statusButtonRef}
          className="data-status-button"
          aria-expanded={panel === "status"}
          onClick={() =>
            panel === "status"
              ? closePanel()
              : openPanel("status", statusButtonRef.current)
          }
        >
          数据状态 {panel === "status" ? "−" : "+"}
        </button>
        {fatalError && !map && (
          <section className="fatal-card" role="alert">
            <p>
              {errorLabels[fatalError.status] ?? "暂时无法连接"} ·
              没有可显示的业务数据。
            </p>
            <button
              ref={retryButtonRef}
              className="primary-button"
              disabled={loading}
              onClick={retry}
            >
              {loading ? "重试中…" : "重试业务数据"}
            </button>
          </section>
        )}
        {panel === "status" && (
          <>
            <section
              className="status-panel"
              aria-label="数据状态详情"
              onMouseEnter={() => setStatusHeld(true)}
              onMouseLeave={() => setStatusHeld(false)}
              onFocusCapture={() => setStatusHeld(true)}
              onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget))
                  setStatusHeld(false);
              }}
            >
              <strong>{status.text}</strong>
              <span>
                {map
                  ? `契约 ${map.contract_version} · ${originLabels[map.data_origin]}`
                  : "尚未取得可用响应"}
              </span>
              {!map &&
                mode === "replay" &&
                availability?.dates.length === 0 && (
                  <span>当前没有可回放的日期。</span>
                )}
              {map?.observed_at_utc && (
                <span>
                  最后观测 {formatNewYorkDateTime(map.observed_at_utc)}
                </span>
              )}
              {map?.expires_at_utc && (
                <span>
                  快照到期 {formatNewYorkDateTime(map.expires_at_utc)}
                </span>
              )}
              {map?.dataset_id && (
                <span className="lineage">
                  数据集 <code>{map.dataset_id}</code>
                </span>
              )}
              {map?.baseline_dataset_id && (
                <span className="lineage">
                  基线 <code>{map.baseline_dataset_id}</code>
                </span>
              )}
              {map?.snapshot_id && (
                <span className="lineage">
                  快照 <code>{map.snapshot_id}</code>
                </span>
              )}
              {hiddenFlows.length > 0 && (
                <span>
                  未绘制 {hiddenFlows.length} 条 OD，共 {hiddenFlowRides} 次骑行
                </span>
              )}
              {transientMessage && (
                <span>
                  {transientDetails[transientMessage] ??
                    "已恢复上一个合法选择。"}
                </span>
              )}
              {refreshError && !expired && (
                <span>
                  {map?.mode === "replay"
                    ? "已恢复上一个可用日期和小时。"
                    : "保留上次仍有效的地图数据。"}
                </span>
              )}
              {(refreshError || expired || fatalError) && (
                <button
                  type="button"
                  className="primary-button"
                  disabled={refreshing || loading}
                  onClick={retry}
                >
                  {refreshing || loading ? "重试中…" : "重试"}
                </button>
              )}
              {map &&
                !refreshError &&
                !expired &&
                !fatalError &&
                !transientMessage &&
                mode === "live" && (
                  <button
                    type="button"
                    className="quiet-button"
                    disabled={refreshing}
                    onClick={retry}
                  >
                    {refreshing ? "刷新中…" : "立即刷新"}
                  </button>
                )}
            </section>
            <button className="quiet-button" onClick={() => closePanel()}>
              收起数据状态
            </button>
          </>
        )}
        {!selectedStation && (
          <>
            <div className="pane-heading">
              <span className="eyebrow">
                {mode === "replay"
                  ? "HISTORICAL FLOWS"
                  : view === "dispatch"
                    ? "REBALANCING"
                    : "STATION INVENTORY"}
              </span>
              <h1>{mode === "replay" ? "历史站点" : viewLabel}</h1>
              <p>
                {map
                  ? `已载入快照 · ${map.stations.length} 个站点`
                  : status.text}
              </p>
            </div>
            {mode === "live" && view === "dispatch" && (
              <section className="dispatch-list" aria-label="调度建议列表">
                <p className="section-note">
                  {suggestions.length} 条有效建议 · [ / ] 巡览
                </p>
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion.suggestion_id}
                    className="dispatch-row"
                    aria-pressed={
                      selectedSuggestionId === suggestion.suggestion_id
                    }
                    onClick={() =>
                      setSelectedSuggestionId(suggestion.suggestion_id)
                    }
                  >
                    <span className="route-rank">{suggestion.priority}</span>
                    <span>
                      <strong>
                        {stationName(suggestion.from_station_id)} →{" "}
                        {stationName(suggestion.to_station_id)}
                      </strong>
                      <small>
                        {suggestion.move_bikes} 辆 ·{" "}
                        {suggestion.distance_meters} 米直线距离
                        {suggestion.priority <= 5 ? " · Top 5" : ""}
                      </small>
                    </span>
                  </button>
                ))}
                {!suggestions.length && (
                  <p className="empty-copy">
                    {map ? "暂无可行建议" : status.text}
                  </p>
                )}
                {selectedSuggestion && (
                  <div className="dispatch-detail">
                    <strong>
                      调度 {selectedSuggestion.move_bikes} 辆 ·{" "}
                      {selectedSuggestion.distance_meters} 米直线距离
                    </strong>
                    <p>
                      {stationName(selectedSuggestion.from_station_id)} →{" "}
                      {stationName(selectedSuggestion.to_station_id)}
                    </p>
                    <small>
                      依据一小时预测 · 有效至{" "}
                      {formatNewYorkTime(selectedSuggestion.expires_at_utc)}
                    </small>
                  </div>
                )}
              </section>
            )}
            <label className="search-field">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m19 19-3.5-3.5m1.5-5A6.5 6.5 0 1 1 4 10.5a6.5 6.5 0 0 1 13 0Z" />
              </svg>
              <span className="sr-only">按站名或站点 ID 搜索</span>
              <input
                ref={searchInputRef}
                value={query}
                placeholder="站名或站点 ID"
                role="combobox"
                aria-expanded="true"
                aria-autocomplete="list"
                aria-controls="station-search-results"
                aria-activedescendant={
                  searchResults[searchIndex]
                    ? `station-option-${stableHash(searchResults[searchIndex].station_id)}`
                    : undefined
                }
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    setSearchIndex((index) =>
                      Math.max(
                        0,
                        Math.min(
                          searchResults.length - 1,
                          index + (event.key === "ArrowDown" ? 1 : -1),
                        ),
                      ),
                    );
                  } else if (
                    event.key === "Enter" &&
                    searchResults[searchIndex]
                  ) {
                    event.preventDefault();
                    openStation(searchResults[searchIndex].station_id);
                  }
                }}
              />
              {query && (
                <button aria-label="清空搜索" onClick={() => setQuery("")}>
                  ×
                </button>
              )}
            </label>
            <div
              id="station-search-results"
              className="station-list"
              role="listbox"
              aria-label="站点结果"
            >
              {searchResults.map((station, index) => {
                const stationExpired =
                  expired ||
                  isExpiredAt(map, station.expires_at_utc, clockElapsedMs);
                const stationStatus = displayStatus(
                  station,
                  mode,
                  view,
                  stationExpired,
                );
                return (
                  <button
                    type="button"
                    role="option"
                    tabIndex={-1}
                    id={`station-option-${stableHash(station.station_id)}`}
                    aria-selected={index === searchIndex}
                    className="station-row"
                    key={station.station_id}
                    onMouseEnter={() => setSearchIndex(index)}
                    onClick={() => openStation(station.station_id)}
                  >
                    <span
                      className={`result-marker ${statusClass(stationStatus)}`}
                    />
                    <span className="station-row-name">
                      <strong>{station.station_name || "未命名站点"}</strong>
                      <small>{station.station_id}</small>
                      <span>
                        {statusLabels[stationStatus] ?? "状态未知"}
                        {station.lat === null || station.lon === null
                          ? " · 无法定位"
                          : ""}
                      </span>
                    </span>
                    {mode === "live" && (
                      <span className="station-row-value">
                        <strong>
                          {canDrawInventory(station, stationExpired)
                            ? station.num_bikes_available
                            : "—"}
                        </strong>
                        <small>当前车辆</small>
                      </span>
                    )}
                  </button>
                );
              })}
              {!searchResults.length && (
                <p className="empty-copy">
                  {!map
                    ? status.text
                    : map.stations.length
                      ? "没有匹配站点"
                      : "暂无站点"}
                </p>
              )}
            </div>
            <p className="keyboard-note">
              ↑↓ 选择 · Enter 查看历史
              <br />N / Shift+N 巡览站点 · Esc 返回
            </p>
          </>
        )}
        {selectedStation && (
          <section
            ref={stationDetailRef}
            className="station-detail"
            role="region"
            aria-label={`${selectedStation.station_name || selectedStation.station_id}站点详情`}
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="quiet-button"
              aria-label="关闭站点详情，返回列表"
              onClick={closeStation}
            >
              ← 返回列表
            </button>
            <header>
              <h2>{selectedStation.station_name || "未命名站点"}</h2>
              <span
                className={`station-state ${statusClass(displayStatus(selectedStation, mode, view, selectedStationExpired))}`}
              >
                {
                  statusLabels[
                    displayStatus(
                      selectedStation,
                      mode,
                      view,
                      selectedStationExpired,
                    )
                  ]
                }
              </span>
              <p>
                {selectedStation.station_id}
                {!selectedPosition && " · 无法定位"}
              </p>
            </header>
            {mode === "live" && (
              <div className="station-metrics">
                <span
                  className="metric-value"
                  tabIndex={0}
                  aria-label={`当前车辆 ${selectedInventoryAvailable ? selectedStation.num_bikes_available : "不可用"}，观测时间 ${formatNewYorkDateTime(map?.observed_at_utc ?? null)}`}
                  data-tooltip={`观测 ${formatNewYorkDateTime(map?.observed_at_utc ?? null)}`}
                >
                  <small>当前车辆</small>
                  <strong>
                    {selectedInventoryAvailable
                      ? selectedStation.num_bikes_available
                      : "—"}
                  </strong>
                </span>
                <span
                  className="metric-value"
                  tabIndex={0}
                  aria-label={`一小时估计 ${selectedForecastAvailable ? selectedStation.projected_bikes_1h : "不可用"}，目标时间 ${formatNewYorkDateTime(selectedStation.forecast_for_utc)}`}
                  data-tooltip={`目标 ${formatNewYorkDateTime(selectedStation.forecast_for_utc)}`}
                >
                  <small>一小时估计</small>
                  <strong>
                    {selectedForecastAvailable
                      ? selectedStation.projected_bikes_1h
                      : "—"}
                  </strong>
                </span>
                <span>
                  <small>可用空桩</small>
                  <strong>
                    {selectedInventoryAvailable
                      ? (selectedStation.num_docks_available ?? "—")
                      : "—"}
                  </strong>
                </span>
              </div>
            )}
            {(selectedStationExpired ||
              selectedStation.current_reason ||
              selectedStation.forecast_reason) && (
              <p className="reason-copy">
                {selectedStationExpired
                  ? "数据已过期"
                  : (reasonLabels[
                      (view === "current"
                        ? selectedStation.current_reason
                        : selectedStation.forecast_reason) ?? ""
                    ] ?? "数据暂不可用")}
              </p>
            )}
            {mode === "live" && (
              <dl className="detail-facts">
                <div>
                  <dt>容量</dt>
                  <dd>{selectedStation.capacity ?? "—"}</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>
                    {
                      statusLabels[
                        selectedStationExpired
                          ? "STALE_DATA"
                          : selectedStation.current_status
                      ]
                    }
                  </dd>
                </div>
                <div>
                  <dt>预测状态</dt>
                  <dd>
                    {
                      statusLabels[
                        selectedStationExpired
                          ? "STALE_DATA"
                          : selectedStation.forecast_status
                      ]
                    }
                  </dd>
                </div>
                <div>
                  <dt>基线样本</dt>
                  <dd>{selectedStation.sample_days ?? "—"} 日</dd>
                </div>
                <div>
                  <dt>{selectedStationExpired ? "上次观测" : "观测时间"}</dt>
                  <dd>{formatNewYorkDateTime(map?.observed_at_utc ?? null)}</dd>
                </div>
                <div>
                  <dt>预测目标</dt>
                  <dd>
                    {formatNewYorkDateTime(selectedStation.forecast_for_utc)}
                  </dd>
                </div>
              </dl>
            )}
            <h3 className="history-heading">24 小时供需</h3>
            {historyLoading && (
              <p className="history-loading">正在加载 24 小时曲线…</p>
            )}
            {!historyLoading && historyError && (
              <p className="history-error" role="alert">
                <span>
                  {errorLabels[historyError.status] ?? "历史数据加载失败"}
                </span>
                <button
                  type="button"
                  onClick={() => setHistoryRetry((value) => value + 1)}
                >
                  重试
                </button>
              </p>
            )}
            {!historyLoading && history && (
              <>
                {history.profile.length > 0 ||
                (mode === "replay" && history.actual.length > 0) ? (
                  <FlowChart
                    history={history}
                    replay={mode === "replay"}
                    currentHour={chartHour}
                  />
                ) : (
                  <p className="empty-copy history-empty">暂无历史基线</p>
                )}
                {history.profile.length === 0 && history.actual.length > 0 && (
                  <p className="actual-empty">
                    暂无历史基线 · 显示当日实际流量
                  </p>
                )}
                {mode === "replay" && history.actual.length === 0 && (
                  <p className="actual-empty">当日无活动</p>
                )}
              </>
            )}
            <footer>
              {history?.profile.length ? (
                <span>{history.profile[0].sample_days} 日样本</span>
              ) : null}
              {history?.profile_start_date && (
                <span>
                  {history.profile_start_date} — {history.profile_end_date}
                </span>
              )}
              {mode === "live" && map?.observed_at_utc && (
                <span>
                  观测 {formatNewYorkTime(map?.observed_at_utc ?? null)}
                </span>
              )}
              {mode === "live" && selectedStation.forecast_for_utc && (
                <span>
                  预测至 {formatNewYorkTime(selectedStation.forecast_for_utc)}
                </span>
              )}
            </footer>
          </section>
        )}
      </aside>
      <div className="map-legend" aria-label="地图图例">
        <strong>
          {mode === "replay" ? "历史 OD / 聚合骑行流向" : viewLabel}
        </strong>
        <span>
          {mode === "replay"
            ? "箭头为起点 → 终点 · 数字为骑行次数 · 无当前库存"
            : "银白库存点 = 1 辆当前可用车 · 风险环随视图变化"}
        </span>
        {mode === "live" && (
          <div className="legend-keys">
            <span>
              <i className="result-marker shortage" />
              缺车 / 偏低
            </span>
            <span>
              <i className="result-marker healthy" />
              正常
            </span>
            <span>
              <i className="result-marker overflow" />
              偏高 / 满桩
            </span>
            <span>
              <i className="result-marker neutral" />
              不可用
            </span>
          </div>
        )}
        {view === "dispatch" && mode === "live" && (
          <span>粉紫线 = 调度建议 · Top 5 与选中路线高亮 · 直线距离</span>
        )}
        <small>建筑扫描 / 道路光点为城市装饰，不代表车辆或 GPS。</small>
        {hiddenFlows.length > 0 && (
          <small>
            无坐标未绘制 {hiddenFlows.length} 条 OD · {hiddenFlowRides} 次骑行
          </small>
        )}
      </div>
    </main>
  );
}
