package citibike.api;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

/** Wire models and the small read-side records used by the JDBC adapter. */
public final class ApiModels {

    private ApiModels() {
    }

    public enum RiskStatus {
        SHORTAGE_RISK,
        LOW_INVENTORY,
        HEALTHY,
        HIGH_INVENTORY,
        OVERFLOW_RISK,
        SERVICE_UNAVAILABLE,
        STALE_DATA,
        INVALID_DATA,
        INSUFFICIENT_DATA,
        NOT_APPLICABLE
    }

    public enum Reason {
        INVALID_TIME,
        STALE_OBSERVATION,
        SERVICE_FLAGS,
        NEGATIVE_INVENTORY,
        MISSING_INVENTORY,
        ZERO_SERVICEABLE_CAPACITY,
        NO_BASELINE
    }

    public record Station(
            String station_id,
            String station_name,
            Double lat,
            Double lon,
            Integer capacity,
            Integer num_bikes_available,
            Integer num_docks_available,
            Boolean is_installed,
            Boolean is_renting,
            Boolean is_returning,
            RiskStatus current_status,
            RiskStatus forecast_status,
            Reason current_reason,
            Reason forecast_reason,
            Double fill_ratio,
            Double expected_inbound_1h,
            Double expected_outbound_1h,
            Double expected_net_flow_1h,
            Double projected_bikes_1h,
            Long sample_days,
            String snapshot_at_utc,
            String last_reported_at_utc,
            String expires_at_utc,
            String forecast_for_utc) {
    }

    public record Flow(String from_station_id, String to_station_id, Long ride_count) {
    }

    public record Suggestion(
            String suggestion_id,
            String from_station_id,
            String to_station_id,
            Integer move_bikes,
            Integer from_surplus,
            Integer to_deficit,
            Integer distance_meters,
            Integer priority,
            String generated_at_utc,
            String expires_at_utc) {
    }

    public record MapResponse(
            String contract_version,
            String mode,
            String data_origin,
            String clock_mode,
            String dataset_id,
            String baseline_dataset_id,
            String snapshot_id,
            String metadata_version,
            String service_date,
            Integer hour,
            String observed_at_utc,
            String as_of_utc,
            String served_at_utc,
            String expires_at_utc,
            List<Station> stations,
            List<Flow> flows,
            List<Suggestion> suggestions) {
    }

    public record AvailableDate(String service_date, List<Integer> hours) {
    }

    public record AvailabilityResponse(
            String contract_version,
            String dataset_id,
            List<String> source_months,
            List<AvailableDate> dates) {
    }

    public record ProfileHour(
            Integer hour,
            Double avg_inbound,
            Double avg_outbound,
            Double avg_net_flow,
            Double median_net_flow,
            Long sample_days) {
    }

    public record ActualHour(
            Integer hour,
            Long inbound_rides,
            Long outbound_rides,
            Long net_flow,
            Long total_activity,
            Long electric_outbound,
            Long classic_outbound,
            Long member_outbound,
            Long casual_outbound) {
    }

    public record HistoryResponse(
            String contract_version,
            String dataset_id,
            String station_id,
            String station_name,
            Integer day_of_week,
            String service_date,
            List<String> source_months,
            String profile_start_date,
            String profile_end_date,
            List<ProfileHour> profile,
            List<ActualHour> actual) {
    }

    public record ErrorDetail(String code, String message) {
    }

    public record ErrorResponse(ErrorDetail error) {
    }

    public record HistoricalRelease(
            String datasetId,
            List<String> sourceMonths,
            LocalDate minServiceDate,
            LocalDate maxServiceDate,
            Instant publishedAtUtc) {
    }

    public record LiveRelease(
            String snapshotId,
            String metadataVersion,
            String baselineDatasetId,
            Instant snapshotAtUtc,
            Instant ingestedAtUtc,
            Instant asOfUtc,
            Instant publishedAtUtc,
            String dataOrigin,
            String clockMode) {
    }

    public record LiveStation(
            String stationId,
            String stationName,
            Double lat,
            Double lon,
            Integer capacity,
            Integer numBikesAvailable,
            Integer numDocksAvailable,
            Boolean installed,
            Boolean renting,
            Boolean returning,
            String currentStatus,
            String forecastStatus,
            String currentReason,
            String forecastReason,
            Double fillRatio,
            Double expectedInbound,
            Double expectedOutbound,
            Double expectedNetFlow,
            Double projectedBikes,
            Long sampleDays,
            Instant snapshotAtUtc,
            Instant lastReportedAtUtc,
            Instant expiresAtUtc,
            Instant forecastForUtc) {
    }

    public record SuggestionRow(
            String suggestionId,
            String fromStationId,
            String toStationId,
            Integer moveBikes,
            Integer fromSurplus,
            Integer toDeficit,
            Integer distanceMeters,
            Integer priority,
            Instant generatedAtUtc,
            Instant expiresAtUtc) {
    }

    public record StationInfo(
            String stationId,
            String stationName,
            Double lat,
            Double lon,
            Integer capacity) {
    }

    public record FlowRow(String fromStationId, String toStationId, Long rideCount) {
    }

    public record ProfileRow(
            Integer hour,
            Double avgInbound,
            Double avgOutbound,
            Double avgNetFlow,
            Double medianNetFlow,
            Long sampleDays) {
    }

    public record ActualRow(
            Integer hour,
            Long inboundRides,
            Long outboundRides,
            Long netFlow,
            Long totalActivity,
            Long electricOutbound,
            Long classicOutbound,
            Long memberOutbound,
            Long casualOutbound) {
    }

    public record AvailabilityPoint(LocalDate serviceDate, Integer hour) {
    }
}
