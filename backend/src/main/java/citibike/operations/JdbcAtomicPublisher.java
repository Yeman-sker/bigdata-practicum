package citibike.operations;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.PreparedStatement;
import java.sql.Timestamp;
import java.sql.Types;
import java.util.List;
import java.util.Objects;

import static citibike.operations.Operations.Risk;
import static citibike.operations.Operations.Release;
import static citibike.operations.Operations.Suggestion;

/** Spring JDBC adapter for the single-transaction ADS/live_release publication. */
public final class JdbcAtomicPublisher implements Operations.AtomicPublisher {
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;

    public JdbcAtomicPublisher(JdbcTemplate jdbc, TransactionTemplate transaction) {
        this.jdbc = Objects.requireNonNull(jdbc);
        this.transaction = Objects.requireNonNull(transaction);
    }

    @Override
    public void publish(List<Risk> risks, List<Suggestion> suggestions, Release release) {
        transaction.executeWithoutResult(status -> {
            jdbc.update("DELETE FROM ads_rebalance_suggestion");
            jdbc.update("DELETE FROM ads_station_current_risk");
            jdbc.update("DELETE FROM live_release");
            insertRisks(risks, release.snapshotId());
            insertSuggestions(suggestions);
            jdbc.update("""
                    INSERT INTO live_release
                    (singleton,snapshot_id,metadata_version,baseline_dataset_id,snapshot_at_utc,
                     ingested_at_utc,as_of_utc,published_at_utc,data_origin,clock_mode)
                    VALUES (1,?,?,?,?,?,?,?,?,?)
                    """, release.snapshotId(), release.metadataVersion(), release.baselineDatasetId(),
                    Timestamp.from(release.snapshotAt()), Timestamp.from(release.ingestedAt()), Timestamp.from(release.asOf()), Timestamp.from(release.publishedAt()),
                    release.dataOrigin(), release.clockMode());
        });
    }

    private void insertRisks(List<Risk> risks, String snapshotId) {
        String sql = """
                INSERT INTO ads_station_current_risk
                (station_id,snapshot_id,station_name,lat,lon,capacity,num_bikes_available,num_docks_available,
                 is_installed,is_renting,is_returning,current_status,forecast_status,current_reason,forecast_reason,
                 fill_ratio,expected_inbound_1h,expected_outbound_1h,expected_net_flow_1h,projected_bikes_1h,
                 sample_days,snapshot_at_utc,last_reported_at_utc,expires_at_utc,forecast_for_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """;
        jdbc.batchUpdate(sql, risks, risks.size(), (ps, r) -> {
            var o = r.observation(); var m = r.metadata();
            ps.setString(1, r.stationId()); ps.setString(2, snapshotId);
            set(ps, 3, m == null ? null : m.name(), Types.VARCHAR);
            set(ps, 4, m == null ? null : m.lat(), Types.DOUBLE); set(ps, 5, m == null ? null : m.lon(), Types.DOUBLE);
            set(ps, 6, m == null ? null : m.capacity(), Types.INTEGER); set(ps, 7, o.bikes(), Types.INTEGER); set(ps, 8, o.docks(), Types.INTEGER);
            set(ps, 9, o.installed(), Types.BOOLEAN); set(ps, 10, o.renting(), Types.BOOLEAN); set(ps, 11, o.returning(), Types.BOOLEAN);
            ps.setString(12, r.currentStatus().name()); ps.setString(13, r.forecastStatus().name());
            set(ps, 14, r.currentReason(), Types.VARCHAR); set(ps, 15, r.forecastReason(), Types.VARCHAR);
            set(ps, 16, r.fillRatio(), Types.DOUBLE); set(ps, 17, r.expectedInbound(), Types.DOUBLE); set(ps, 18, r.expectedOutbound(), Types.DOUBLE);
            set(ps, 19, r.expectedNetFlow(), Types.DOUBLE); set(ps, 20, r.projectedBikes(), Types.DOUBLE); set(ps, 21, r.sampleDays(), Types.BIGINT);
            setInstant(ps, 22, r.observation().snapshotAt()); setInstant(ps, 23, o.lastReportedAt());
            setInstant(ps, 24, r.expiresAt()); setInstant(ps, 25, r.forecastFor());
        });
    }

    private void insertSuggestions(List<Suggestion> suggestions) {
        String sql = """
                INSERT INTO ads_rebalance_suggestion
                (suggestion_id,snapshot_id,from_station_id,to_station_id,move_bikes,from_surplus,to_deficit,
                 distance_meters,priority,generated_at_utc,expires_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """;
        jdbc.batchUpdate(sql, suggestions, suggestions.size(), (ps, s) -> {
            ps.setString(1, s.suggestionId()); ps.setString(2, s.snapshotId()); ps.setString(3, s.fromStationId()); ps.setString(4, s.toStationId());
            ps.setInt(5, s.moveBikes()); ps.setInt(6, s.fromSurplus()); ps.setInt(7, s.toDeficit()); ps.setInt(8, s.distanceMeters());
            ps.setInt(9, s.priority()); setInstant(ps, 10, s.generatedAt()); setInstant(ps, 11, s.expiresAt());
        });
    }

    private static void set(PreparedStatement ps, int index, Object value, int type) throws java.sql.SQLException {
        if (value == null) ps.setNull(index, type); else ps.setObject(index, value);
    }

    private static void setInstant(PreparedStatement ps, int index, java.time.Instant value) throws java.sql.SQLException {
        if (value == null) ps.setNull(index, Types.TIMESTAMP); else ps.setTimestamp(index, Timestamp.from(value));
    }
}
