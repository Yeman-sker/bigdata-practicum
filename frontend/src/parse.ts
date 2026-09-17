import type {
  AvailabilityResponse,
  HistoryResponse,
  MapResponse,
} from "./domain";

function check(ok: boolean) {
  if (!ok) throw new Error("接口响应格式无效");
}
function object(value: unknown, fields: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("响应对象格式无效");
  const data = value as Record<string, unknown>;
  const keys = fields.split(" ");
  check(
    Object.keys(data).length === keys.length &&
      keys.every((key) => Object.hasOwn(data, key)),
  );
  return data;
}
function array(value: unknown, maximum = Infinity): unknown[] {
  if (!Array.isArray(value) || value.length > maximum)
    throw new Error("响应数组格式无效");
  return value;
}
const text = (value: unknown, min = 0, max = Infinity): value is string =>
  typeof value === "string" &&
  [...value].length >= min &&
  [...value].length <= max;
const number = (
  value: unknown,
  min = -Infinity,
  max = Infinity,
): value is number =>
  typeof value === "number" &&
  Number.isFinite(value) &&
  value >= min &&
  value <= max;
const integer = (
  value: unknown,
  min = -Infinity,
  max = Infinity,
): value is number => number(value, min, max) && Number.isSafeInteger(value);
const id = (value: unknown) => text(value, 1, 128);
const hash = (value: unknown) =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const date = (value: unknown): value is string =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(Date.parse(value)) &&
  new Date(value).toISOString().slice(0, 10) === value;
const timestamp = (value: unknown) =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$/.test(
    value,
  ) &&
  date(value.slice(0, 10)) &&
  Number.isFinite(Date.parse(value));
const nullable = (value: unknown, valid: (value: unknown) => boolean) =>
  value === null || valid(value);
const weekday = (value: string) =>
  new Date(`${value}T12:00:00Z`).getUTCDay() || 7;
function sourceMonths(value: unknown, unique: boolean) {
  const months = array(value);
  check(
    months.every(
      (month) =>
        typeof month === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(month),
    ),
  );
  if (unique) check(new Set(months).size === months.length);
}
const statuses = new Set([
  "SHORTAGE_RISK",
  "LOW_INVENTORY",
  "HEALTHY",
  "HIGH_INVENTORY",
  "OVERFLOW_RISK",
  "SERVICE_UNAVAILABLE",
  "STALE_DATA",
  "INVALID_DATA",
  "INSUFFICIENT_DATA",
  "NOT_APPLICABLE",
]);
const reasons = new Set<unknown>([
  null,
  "INVALID_TIME",
  "STALE_OBSERVATION",
  "SERVICE_FLAGS",
  "NEGATIVE_INVENTORY",
  "MISSING_INVENTORY",
  "ZERO_SERVICEABLE_CAPACITY",
  "NO_BASELINE",
]);
const stationFields =
  "station_id station_name lat lon capacity num_bikes_available num_docks_available is_installed is_renting is_returning current_status forecast_status current_reason forecast_reason fill_ratio expected_inbound_1h expected_outbound_1h expected_net_flow_1h projected_bikes_1h sample_days snapshot_at_utc last_reported_at_utc expires_at_utc forecast_for_utc";
const replayNullFields =
  "num_bikes_available num_docks_available is_installed is_renting is_returning current_reason forecast_reason fill_ratio expected_inbound_1h expected_outbound_1h expected_net_flow_1h projected_bikes_1h sample_days snapshot_at_utc last_reported_at_utc expires_at_utc forecast_for_utc".split(
    " ",
  );

export function parseMap(value: unknown): MapResponse {
  const data = object(
    value,
    "contract_version mode data_origin clock_mode dataset_id baseline_dataset_id snapshot_id metadata_version service_date hour observed_at_utc as_of_utc served_at_utc expires_at_utc stations flows suggestions",
  );
  check(
    data.contract_version === "1.1" &&
      (data.mode === "live" || data.mode === "replay"),
  );
  check(data.clock_mode === "wall" || data.clock_mode === "recorded");
  check(
    date(data.service_date) &&
      timestamp(data.as_of_utc) &&
      timestamp(data.served_at_utc),
  );
  const live = data.mode === "live";
  if (live) {
    check(
      ["GBFS_LIVE", "GBFS_REPLAY", "FIXTURE"].includes(
        String(data.data_origin),
      ),
    );
    check(
      data.dataset_id === null &&
        data.hour === null &&
        hash(data.snapshot_id) &&
        hash(data.metadata_version),
    );
    check(
      nullable(data.baseline_dataset_id, hash) &&
        timestamp(data.observed_at_utc) &&
        timestamp(data.expires_at_utc),
    );
  } else {
    check(data.data_origin === "HISTORICAL" || data.data_origin === "FIXTURE");
    check(hash(data.dataset_id) && integer(data.hour, 0, 23));
    for (const field of [
      "baseline_dataset_id",
      "snapshot_id",
      "metadata_version",
      "observed_at_utc",
      "expires_at_utc",
    ])
      check(data[field] === null);
  }
  const ids = new Set<string>();
  let previousId = "";
  for (const item of array(data.stations, 5000)) {
    const station = object(item, stationFields);
    check(id(station.station_id) && !ids.has(String(station.station_id)));
    const stationId = String(station.station_id);
    if (live) check(stationId > previousId);
    previousId = stationId;
    ids.add(stationId);
    check(nullable(station.station_name, (value) => text(value, 0, 512)));
    check(
      nullable(station.lat, (value) => number(value, -90, 90)) &&
        nullable(station.lon, (value) => number(value, -180, 180)),
    );
    check(nullable(station.capacity, (value) => integer(value, 1)));
    for (const field of ["num_bikes_available", "num_docks_available"])
      check(nullable(station[field], integer));
    for (const field of ["is_installed", "is_renting", "is_returning"])
      check(station[field] === null || typeof station[field] === "boolean");
    check(
      text(station.current_status) &&
        statuses.has(station.current_status) &&
        text(station.forecast_status) &&
        statuses.has(station.forecast_status),
    );
    check(reasons.has(station.current_reason));
    check(reasons.has(station.forecast_reason));
    check(nullable(station.fill_ratio, (value) => number(value, 0, 1)));
    for (const field of ["expected_inbound_1h", "expected_outbound_1h"])
      check(nullable(station[field], (value) => number(value, 0)));
    for (const field of ["expected_net_flow_1h", "projected_bikes_1h"])
      check(nullable(station[field], number));
    check(nullable(station.sample_days, (value) => integer(value, 1)));
    for (const field of [
      "snapshot_at_utc",
      "last_reported_at_utc",
      "expires_at_utc",
      "forecast_for_utc",
    ])
      check(nullable(station[field], timestamp));
    if (live) {
      check(
        station.current_status !== "NOT_APPLICABLE" &&
          station.forecast_status !== "NOT_APPLICABLE",
      );
      check(
        timestamp(station.snapshot_at_utc) && timestamp(station.expires_at_utc),
      );
    } else {
      check(
        station.current_status === "NOT_APPLICABLE" &&
          station.forecast_status === "NOT_APPLICABLE",
      );
      for (const field of replayNullFields) check(station[field] === null);
    }
  }
  let previousFlow: { from: string; to: string } | null = null;
  for (const item of array(data.flows, live ? 0 : 50000)) {
    const flow = object(item, "from_station_id to_station_id ride_count");
    check(
      id(flow.from_station_id) &&
        id(flow.to_station_id) &&
        integer(flow.ride_count, 1),
    );
    const from = String(flow.from_station_id),
      to = String(flow.to_station_id);
    check(ids.has(from) && ids.has(to));
    if (previousFlow)
      check(
        from > previousFlow.from ||
          (from === previousFlow.from && to > previousFlow.to),
      );
    previousFlow = { from, to };
  }
  const suggestionIds = new Set();
  let priority = 0;
  for (const item of array(data.suggestions, live ? 10000 : 0)) {
    const suggestion = object(
      item,
      "suggestion_id from_station_id to_station_id move_bikes from_surplus to_deficit distance_meters priority generated_at_utc expires_at_utc",
    );
    check(
      text(suggestion.suggestion_id, 66, 80) &&
        !suggestionIds.has(suggestion.suggestion_id),
    );
    suggestionIds.add(suggestion.suggestion_id);
    check(
      id(suggestion.from_station_id) &&
        id(suggestion.to_station_id) &&
        ids.has(String(suggestion.from_station_id)) &&
        ids.has(String(suggestion.to_station_id)),
    );
    for (const field of [
      "move_bikes",
      "from_surplus",
      "to_deficit",
      "priority",
    ])
      check(integer(suggestion[field], 1));
    check(
      integer(suggestion.distance_meters, 0) &&
        Number(suggestion.priority) > priority,
    );
    priority = Number(suggestion.priority);
    check(
      timestamp(suggestion.generated_at_utc) &&
        timestamp(suggestion.expires_at_utc),
    );
  }
  return value as MapResponse;
}

export function parseAvailability(value: unknown): AvailabilityResponse {
  const data = object(value, "contract_version dataset_id source_months dates");
  check(data.contract_version === "1.1" && hash(data.dataset_id));
  sourceMonths(data.source_months, true);
  let previousDate = "";
  for (const item of array(data.dates)) {
    const row = object(item, "service_date hours");
    check(date(row.service_date) && row.service_date > previousDate);
    previousDate = String(row.service_date);
    let previousHour = -1;
    for (const selectedHour of array(row.hours, 24)) {
      check(integer(selectedHour, 0, 23) && selectedHour > previousHour);
      previousHour = Number(selectedHour);
    }
  }
  return value as AvailabilityResponse;
}

export function parseHistory(
  value: unknown,
  expected?: {
    stationId: string;
    dayOfWeek: number;
    serviceDate: string | null;
  },
): HistoryResponse {
  const data = object(
    value,
    "contract_version dataset_id station_id station_name day_of_week service_date source_months profile_start_date profile_end_date profile actual",
  );
  check(
    data.contract_version === "1.1" &&
      hash(data.dataset_id) &&
      id(data.station_id),
  );
  check(
    nullable(data.station_name, (value) => text(value, 0, 512)) &&
      integer(data.day_of_week, 1, 7),
  );
  check(
    nullable(data.service_date, date) &&
      nullable(data.profile_start_date, date) &&
      nullable(data.profile_end_date, date),
  );
  sourceMonths(data.source_months, false);
  if (date(data.profile_start_date) && date(data.profile_end_date))
    check(data.profile_start_date <= data.profile_end_date);
  if (date(data.service_date))
    check(weekday(data.service_date) === data.day_of_week);
  if (expected)
    check(
      data.station_id === expected.stationId &&
        data.day_of_week === expected.dayOfWeek &&
        data.service_date === expected.serviceDate,
    );
  const profile = array(data.profile, 24),
    actual = array(data.actual, 24);
  check(profile.length === 0 || profile.length === 24);
  check(
    actual.length === 0 || (actual.length === 24 && data.service_date !== null),
  );
  for (const [index, item] of profile.entries()) {
    const row = object(
      item,
      "hour avg_inbound avg_outbound avg_net_flow median_net_flow sample_days",
    );
    check(
      row.hour === index &&
        number(row.avg_inbound, 0) &&
        number(row.avg_outbound, 0),
    );
    check(
      number(row.avg_net_flow) &&
        number(row.median_net_flow) &&
        integer(row.sample_days, 1),
    );
  }
  for (const [index, item] of actual.entries()) {
    const row = object(
      item,
      "hour inbound_rides outbound_rides net_flow total_activity electric_outbound classic_outbound member_outbound casual_outbound",
    );
    check(row.hour === index && integer(row.net_flow));
    for (const field of [
      "inbound_rides",
      "outbound_rides",
      "total_activity",
      "electric_outbound",
      "classic_outbound",
      "member_outbound",
      "casual_outbound",
    ])
      check(integer(row[field], 0));
  }
  return value as HistoryResponse;
}
