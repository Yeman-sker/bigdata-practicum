package citibike.api;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.dao.DataRetrievalFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

import static citibike.api.ApiModels.ActualRow;
import static citibike.api.ApiModels.AvailabilityPoint;
import static citibike.api.ApiModels.FlowRow;
import static citibike.api.ApiModels.HistoricalRelease;
import static citibike.api.ApiModels.LiveRelease;
import static citibike.api.ApiModels.LiveStation;
import static citibike.api.ApiModels.ProfileRow;
import static citibike.api.ApiModels.StationInfo;
import static citibike.api.ApiModels.SuggestionRow;

@Repository
public class JdbcApiDataRepository implements ApiDataRepository {

    private static final String LIVE_STATION_COLUMNS = """
            station_id, station_name, lat, lon, capacity,
            num_bikes_available, num_docks_available,
            is_installed, is_renting, is_returning,
            current_status, forecast_status, current_reason, forecast_reason,
            fill_ratio, expected_inbound_1h, expected_outbound_1h,
            expected_net_flow_1h, projected_bikes_1h, sample_days,
            snapshot_at_utc, last_reported_at_utc, expires_at_utc, forecast_for_utc
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public JdbcApiDataRepository(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    @Override
    public Optional<LiveRelease> findLiveRelease() {
        List<LiveRelease> rows = jdbc.query("""
                SELECT snapshot_id, metadata_version, baseline_dataset_id,
                       snapshot_at_utc, ingested_at_utc, as_of_utc, published_at_utc,
                       data_origin, clock_mode
                  FROM live_release
                 WHERE singleton = 1
                """, (rs, rowNum) -> new LiveRelease(
                rs.getString("snapshot_id"),
                rs.getString("metadata_version"),
                rs.getString("baseline_dataset_id"),
                instant(rs, "snapshot_at_utc"),
                instant(rs, "ingested_at_utc"),
                instant(rs, "as_of_utc"),
                instant(rs, "published_at_utc"),
                rs.getString("data_origin"),
                rs.getString("clock_mode")));
        return rows.stream().findFirst();
    }

    @Override
    public List<LiveStation> findLiveStations(String snapshotId, int limit) {
        return jdbc.query("""
                SELECT %s
                  FROM ads_station_current_risk
                 WHERE snapshot_id = ?
                 ORDER BY station_id ASC
                 LIMIT ?
                """.formatted(LIVE_STATION_COLUMNS),
                (rs, rowNum) -> liveStation(rs), snapshotId, limit);
    }

    @Override
    public List<SuggestionRow> findLiveSuggestions(String snapshotId, Instant asOfUtc, int limit) {
        return jdbc.query("""
                SELECT suggestion_id, from_station_id, to_station_id, move_bikes,
                       from_surplus, to_deficit, distance_meters, priority,
                       generated_at_utc, expires_at_utc
                  FROM ads_rebalance_suggestion
                 WHERE snapshot_id = ? AND expires_at_utc > ?
                 ORDER BY priority ASC
                 LIMIT ?
                """, (rs, rowNum) -> new SuggestionRow(
                rs.getString("suggestion_id"),
                rs.getString("from_station_id"),
                rs.getString("to_station_id"),
                rs.getInt("move_bikes"),
                rs.getInt("from_surplus"),
                rs.getInt("to_deficit"),
                rs.getInt("distance_meters"),
                rs.getInt("priority"),
                instant(rs, "generated_at_utc"),
                instant(rs, "expires_at_utc")), snapshotId, Timestamp.from(asOfUtc), limit);
    }

    @Override
    public Optional<HistoricalRelease> findHistoricalRelease() {
        List<HistoricalRelease> rows = jdbc.query("""
                SELECT dataset_id, source_months, min_service_date, max_service_date,
                       published_at_utc
                  FROM historical_release
                 WHERE singleton = 1
                """, (rs, rowNum) -> new HistoricalRelease(
                rs.getString("dataset_id"),
                sourceMonths(rs.getString("source_months")),
                rs.getObject("min_service_date", LocalDate.class),
                rs.getObject("max_service_date", LocalDate.class),
                instant(rs, "published_at_utc")));
        return rows.stream().findFirst();
    }

    @Override
    public List<AvailabilityPoint> findAvailability(String datasetId) {
        return jdbc.query("""
                SELECT DISTINCT service_date, hour
                  FROM dws_station_hourly_flow_v1
                 WHERE dataset_id = ?
                 ORDER BY service_date ASC, hour ASC
                """, (rs, rowNum) -> new AvailabilityPoint(
                rs.getObject("service_date", LocalDate.class), rs.getInt("hour")), datasetId);
    }

    @Override
    public List<String> findActiveStationIds(String datasetId, LocalDate serviceDate, int hour, int limit) {
        return jdbc.queryForList("""
                SELECT station_id
                  FROM (
                        SELECT station_id
                          FROM dws_station_hourly_flow_v1
                         WHERE dataset_id = ? AND service_date = ?
                        UNION
                        SELECT from_station_id AS station_id
                          FROM dws_station_od_hourly_v1
                         WHERE dataset_id = ? AND service_date = ? AND hour = ?
                        UNION
                        SELECT to_station_id AS station_id
                          FROM dws_station_od_hourly_v1
                         WHERE dataset_id = ? AND service_date = ? AND hour = ?
                       ) active_station
                 ORDER BY station_id ASC
                 LIMIT ?
                """, String.class,
                datasetId, serviceDate, datasetId, serviceDate, hour,
                datasetId, serviceDate, hour, limit);
    }

    @Override
    public List<FlowRow> findFlows(String datasetId, LocalDate serviceDate, int hour, int limit) {
        return jdbc.query("""
                SELECT from_station_id, to_station_id, ride_count
                  FROM dws_station_od_hourly_v1
                 WHERE dataset_id = ? AND service_date = ? AND hour = ?
                 ORDER BY from_station_id ASC, to_station_id ASC
                 LIMIT ?
                """, (rs, rowNum) -> new FlowRow(
                rs.getString("from_station_id"),
                rs.getString("to_station_id"),
                rs.getLong("ride_count")), datasetId, serviceDate, hour, limit);
    }

    @Override
    public List<StationInfo> findHistoricalStations(String datasetId, Collection<String> stationIds) {
        if (stationIds.isEmpty()) {
            return Collections.emptyList();
        }
        List<String> ids = new ArrayList<>(stationIds);
        String placeholders = ids.stream().map(id -> "?").collect(Collectors.joining(", "));
        List<Object> args = new ArrayList<>();
        args.add(datasetId);
        args.addAll(ids);
        return jdbc.query("""
                SELECT station_id, station_name, lat, lon, capacity
                  FROM dim_station_v1
                 WHERE dataset_id = ? AND station_id IN (%s)
                """.formatted(placeholders), (rs, rowNum) -> stationInfo(rs), args.toArray());
    }

    @Override
    public Optional<StationInfo> findHistoricalStation(String datasetId, String stationId) {
        List<StationInfo> rows = jdbc.query("""
                SELECT station_id, station_name, lat, lon, capacity
                  FROM dim_station_v1
                 WHERE dataset_id = ? AND station_id = ?
                """, (rs, rowNum) -> stationInfo(rs), datasetId, stationId);
        return rows.stream().findFirst();
    }

    @Override
    public Optional<StationInfo> findCurrentStation(String stationId) {
        List<StationInfo> rows = jdbc.query("""
                SELECT station_id, station_name, lat, lon, capacity
                  FROM ads_station_current_risk
                 WHERE station_id = ?
                 LIMIT 1
                """, (rs, rowNum) -> stationInfo(rs), stationId);
        return rows.stream().findFirst();
    }

    @Override
    public List<ProfileRow> findProfile(String datasetId, String stationId, int dayOfWeek) {
        return jdbc.query("""
                SELECT hour, avg_inbound, avg_outbound, avg_net_flow,
                       median_net_flow, sample_days
                  FROM dws_station_hour_profile_v1
                 WHERE dataset_id = ? AND station_id = ? AND day_of_week = ?
                 ORDER BY hour ASC
                """, (rs, rowNum) -> new ProfileRow(
                rs.getInt("hour"),
                rs.getDouble("avg_inbound"),
                rs.getDouble("avg_outbound"),
                rs.getDouble("avg_net_flow"),
                rs.getDouble("median_net_flow"),
                rs.getLong("sample_days")), datasetId, stationId, dayOfWeek);
    }

    @Override
    public List<ActualRow> findActual(String datasetId, String stationId, LocalDate serviceDate) {
        return jdbc.query("""
                SELECT hour, inbound_rides, outbound_rides, net_flow, total_activity,
                       electric_outbound, classic_outbound, member_outbound, casual_outbound
                  FROM dws_station_hourly_flow_v1
                 WHERE dataset_id = ? AND station_id = ? AND service_date = ?
                 ORDER BY hour ASC
                """, (rs, rowNum) -> new ActualRow(
                rs.getInt("hour"),
                rs.getLong("inbound_rides"),
                rs.getLong("outbound_rides"),
                rs.getLong("net_flow"),
                rs.getLong("total_activity"),
                rs.getLong("electric_outbound"),
                rs.getLong("classic_outbound"),
                rs.getLong("member_outbound"),
                rs.getLong("casual_outbound")), datasetId, stationId, serviceDate);
    }

    private LiveStation liveStation(ResultSet rs) throws SQLException {
        return new LiveStation(
                rs.getString("station_id"),
                rs.getString("station_name"),
                nullableDouble(rs, "lat"),
                nullableDouble(rs, "lon"),
                nullableInt(rs, "capacity"),
                nullableInt(rs, "num_bikes_available"),
                nullableInt(rs, "num_docks_available"),
                nullableBoolean(rs, "is_installed"),
                nullableBoolean(rs, "is_renting"),
                nullableBoolean(rs, "is_returning"),
                rs.getString("current_status"),
                rs.getString("forecast_status"),
                rs.getString("current_reason"),
                rs.getString("forecast_reason"),
                nullableDouble(rs, "fill_ratio"),
                nullableDouble(rs, "expected_inbound_1h"),
                nullableDouble(rs, "expected_outbound_1h"),
                nullableDouble(rs, "expected_net_flow_1h"),
                nullableDouble(rs, "projected_bikes_1h"),
                nullableLong(rs, "sample_days"),
                instant(rs, "snapshot_at_utc"),
                instant(rs, "last_reported_at_utc"),
                instant(rs, "expires_at_utc"),
                instant(rs, "forecast_for_utc"));
    }

    private StationInfo stationInfo(ResultSet rs) throws SQLException {
        return new StationInfo(
                rs.getString("station_id"),
                rs.getString("station_name"),
                nullableDouble(rs, "lat"),
                nullableDouble(rs, "lon"),
                nullableInt(rs, "capacity"));
    }

    private List<String> sourceMonths(String json) {
        try {
            return objectMapper.readValue(json, new TypeReference<>() {
            });
        } catch (JsonProcessingException e) {
            throw new DataRetrievalFailureException("Invalid historical release metadata", e);
        }
    }

    private static Instant instant(ResultSet rs, String column) throws SQLException {
        Timestamp value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }

    private static Integer nullableInt(ResultSet rs, String column) throws SQLException {
        int value = rs.getInt(column);
        return rs.wasNull() ? null : value;
    }

    private static Long nullableLong(ResultSet rs, String column) throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Double nullableDouble(ResultSet rs, String column) throws SQLException {
        double value = rs.getDouble(column);
        return rs.wasNull() ? null : value;
    }

    private static Boolean nullableBoolean(ResultSet rs, String column) throws SQLException {
        boolean value = rs.getBoolean(column);
        return rs.wasNull() ? null : value;
    }
}
