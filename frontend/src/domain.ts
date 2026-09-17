export type Mode = "live" | "replay";
export type View = "current" | "forecast" | "dispatch";
export type Station = {
  station_id: string;
  station_name: string | null;
  lat: number | null;
  lon: number | null;
  capacity: number | null;
  num_bikes_available: number | null;
  num_docks_available: number | null;
  is_installed: boolean | null;
  is_renting: boolean | null;
  is_returning: boolean | null;
  fill_ratio: number | null;
  expected_inbound_1h: number | null;
  expected_outbound_1h: number | null;
  expected_net_flow_1h: number | null;
  current_status: string;
  forecast_status: string;
  current_reason: string | null;
  forecast_reason: string | null;
  projected_bikes_1h: number | null;
  sample_days: number | null;
  snapshot_at_utc: string | null;
  last_reported_at_utc: string | null;
  expires_at_utc: string | null;
  forecast_for_utc: string | null;
};

export type Flow = {
  from_station_id: string;
  to_station_id: string;
  ride_count: number;
};

export type Suggestion = {
  suggestion_id: string;
  from_station_id: string;
  to_station_id: string;
  move_bikes: number;
  from_surplus: number;
  to_deficit: number;
  generated_at_utc: string;
  distance_meters: number;
  priority: number;
  expires_at_utc: string;
};

export type MapResponse = {
  contract_version: "1.1";
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

export type AvailabilityResponse = {
  contract_version: "1.1";
  dataset_id: string;
  source_months: string[];
  dates: { service_date: string; hours: number[] }[];
};

export type ProfileHour = {
  hour: number;
  avg_inbound: number;
  avg_outbound: number;
  avg_net_flow: number;
  median_net_flow: number;
  sample_days: number;
};

export type ActualHour = {
  hour: number;
  inbound_rides: number;
  outbound_rides: number;
  net_flow: number;
  total_activity: number;
  electric_outbound: number;
  classic_outbound: number;
  member_outbound: number;
  casual_outbound: number;
};

export type HistoryResponse = {
  contract_version: "1.1";
  dataset_id: string;
  day_of_week: number;
  source_months: string[];
  station_id: string;
  station_name: string | null;
  service_date: string | null;
  profile_start_date: string | null;
  profile_end_date: string | null;
  profile: ProfileHour[];
  actual: ActualHour[];
};
