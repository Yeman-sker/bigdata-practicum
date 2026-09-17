package citibike.operations;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static citibike.operations.Operations.*;

@Repository
public class JdbcOperationsStore implements AtomicPublisher {
    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
    private final JdbcTemplate jdbc;
    private final TransactionTemplate read;
    private final TransactionTemplate write;

    public JdbcOperationsStore(JdbcTemplate jdbc, PlatformTransactionManager transactions) {
        this.jdbc = jdbc;
        read = new TransactionTemplate(transactions);
        read.setReadOnly(true);
        read.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);
        write = new TransactionTemplate(transactions);
    }

    public record Baseline(String datasetId, Map<String, Map<DayHour, Profile>> profiles) {}

    public static final class StalePublication extends RuntimeException {
        public StalePublication(String snapshotId) {
            super("Snapshot is not newer than the durable release: " + snapshotId);
        }
    }

    public Optional<Release> latestRelease() {
        return jdbc.query("""
                SELECT snapshot_id, metadata_version, baseline_dataset_id,
                       snapshot_at_utc, ingested_at_utc, as_of_utc, published_at_utc,
                       data_origin, clock_mode
                  FROM live_release WHERE singleton = 1
                """, (rs, row) -> new Release(rs.getString("snapshot_id"),
                rs.getString("metadata_version"), rs.getString("baseline_dataset_id"),
                rs.getObject("snapshot_at_utc", LocalDateTime.class).toInstant(ZoneOffset.UTC),
                rs.getObject("ingested_at_utc", LocalDateTime.class).toInstant(ZoneOffset.UTC),
                rs.getObject("as_of_utc", LocalDateTime.class).toInstant(ZoneOffset.UTC),
                rs.getObject("published_at_utc", LocalDateTime.class).toInstant(ZoneOffset.UTC),
                rs.getString("data_origin"), rs.getString("clock_mode"))).stream().findFirst();
    }

    public Baseline baseline(Instant snapshotAt) {
        return read.execute(transaction -> {
            List<String> releases = jdbc.queryForList(
                    "SELECT dataset_id FROM historical_release WHERE singleton = 1", String.class);
            if (releases.isEmpty()) return new Baseline(null, Map.of());
            String datasetId = releases.get(0);
            var local = snapshotAt.atZone(NEW_YORK);
            DayHour dayHour = new DayHour(local.getDayOfWeek().getValue(), local.getHour());
            Map<String, Map<DayHour, Profile>> profiles = new HashMap<>();
            jdbc.query("""
                    SELECT station_id, avg_inbound, avg_outbound, avg_net_flow, sample_days
                      FROM dws_station_hour_profile_v1
                     WHERE dataset_id = ? AND day_of_week = ? AND hour = ?
                    """, rs -> {
                profiles.put(rs.getString("station_id"), Map.of(dayHour, new Profile(
                        rs.getDouble("avg_inbound"), rs.getDouble("avg_outbound"),
                        rs.getDouble("avg_net_flow"), rs.getLong("sample_days"))));
            }, datasetId, dayHour.dayOfWeek(), dayHour.hour());
            return new Baseline(datasetId, Map.copyOf(profiles));
        });
    }

    @Override
    public void publish(List<Risk> risks, List<Suggestion> suggestions, Release release) {
        write.executeWithoutResult(transaction -> {
            Optional<Release> current = latestRelease();
            if (current.isPresent()) {
                if (current.get().snapshotId().equals(release.snapshotId())) return;
                if (!release.snapshotAt().isAfter(current.get().snapshotAt()))
                    throw new StalePublication(release.snapshotId());
            }
            jdbc.update("DELETE FROM ads_rebalance_suggestion");
            jdbc.update("DELETE FROM ads_station_current_risk");
            jdbc.batchUpdate("""
                    INSERT INTO ads_station_current_risk (
                        station_id, snapshot_id, station_name, lat, lon, capacity,
                        num_bikes_available, num_docks_available, is_installed, is_renting, is_returning,
                        current_status, forecast_status, current_reason, forecast_reason, fill_ratio,
                        expected_inbound_1h, expected_outbound_1h, expected_net_flow_1h,
                        projected_bikes_1h, sample_days, snapshot_at_utc, last_reported_at_utc,
                        expires_at_utc, forecast_for_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, risks.stream().map(risk -> {
                Metadata m = risk.metadata();
                Observation o = risk.observation();
                return new Object[]{risk.stationId(), release.snapshotId(),
                        m == null ? null : m.name(), m == null ? null : m.lat(),
                        m == null ? null : m.lon(), m == null ? null : m.capacity(),
                        o.bikes(), o.docks(), o.installed(), o.renting(), o.returning(),
                        risk.currentStatus().name(), risk.forecastStatus().name(),
                        risk.currentReason(), risk.forecastReason(), risk.fillRatio(),
                        risk.expectedInbound(), risk.expectedOutbound(), risk.expectedNetFlow(),
                        risk.projectedBikes(), risk.sampleDays(), utc(o.snapshotAt()),
                        utc(o.lastReportedAt()), utc(risk.expiresAt()), utc(risk.forecastFor())};
            }).toList());
            jdbc.batchUpdate("""
                    INSERT INTO ads_rebalance_suggestion (
                        suggestion_id, snapshot_id, from_station_id, to_station_id, move_bikes,
                        from_surplus, to_deficit, distance_meters, priority, generated_at_utc, expires_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, suggestions.stream().map(s -> new Object[]{s.suggestionId(), s.snapshotId(),
                    s.fromStationId(), s.toStationId(), s.moveBikes(), s.fromSurplus(), s.toDeficit(),
                    s.distanceMeters(), s.priority(), utc(s.generatedAt()), utc(s.expiresAt())}).toList());
            jdbc.update("""
                    INSERT INTO live_release (
                        singleton, snapshot_id, metadata_version, baseline_dataset_id, snapshot_at_utc,
                        ingested_at_utc, as_of_utc, published_at_utc, data_origin, clock_mode
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                        snapshot_id = VALUES(snapshot_id), metadata_version = VALUES(metadata_version),
                        baseline_dataset_id = VALUES(baseline_dataset_id), snapshot_at_utc = VALUES(snapshot_at_utc),
                        ingested_at_utc = VALUES(ingested_at_utc), as_of_utc = VALUES(as_of_utc),
                        published_at_utc = VALUES(published_at_utc), data_origin = VALUES(data_origin),
                        clock_mode = VALUES(clock_mode)
                    """, release.snapshotId(), release.metadataVersion(), release.baselineDatasetId(),
                    utc(release.snapshotAt()), utc(release.ingestedAt()), utc(release.asOf()),
                    utc(release.publishedAt()), release.dataOrigin(), release.clockMode());
        });
    }

    // MySQL DATETIME stores UTC fields without an offset, independently of the JVM time zone.
    private static LocalDateTime utc(Instant instant) {
        return instant == null ? null : LocalDateTime.ofInstant(instant, ZoneOffset.UTC);
    }
}
