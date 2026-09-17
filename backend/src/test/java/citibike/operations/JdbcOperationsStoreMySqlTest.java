package citibike.operations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.core.io.FileSystemResource;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static citibike.operations.Operations.*;
import static org.junit.jupiter.api.Assertions.*;

/** Rebuilds only the explicitly supplied citibike_operations_it database. */
@EnabledIfEnvironmentVariable(named = "CITIBIKE_OPERATIONS_IT", matches = "true")
class JdbcOperationsStoreMySqlTest {
    private static final String DATASET = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e";
    private static final String SNAPSHOT = "144dc6c63f679f72b02b9ada5129d5393976fdb04be00534eda8ebe73c3fb3c3";
    private static final String METADATA = "84fb15f78a673f21687fce423e223a2970f7276903398e6e0c19c502ab1ea41b";
    private static final Instant SNAPSHOT_AT = Instant.parse("2025-02-05T13:00:00Z");
    private static final Instant AS_OF = SNAPSHOT_AT.plusSeconds(2);
    private static final Release FIXTURE_RELEASE = new Release(SNAPSHOT, METADATA, DATASET,
            SNAPSHOT_AT, AS_OF, AS_OF, AS_OF, "FIXTURE", "recorded");
    private DriverManagerDataSource datasource;
    private JdbcTemplate jdbc;
    private JdbcOperationsStore store;

    @BeforeEach
    void seedDedicatedDatabase() {
        datasource = new DriverManagerDataSource(requiredEnvironment("CITIBIKE_OPERATIONS_IT_URL"),
                requiredEnvironment("CITIBIKE_OPERATIONS_IT_USERNAME"),
                System.getenv().getOrDefault("CITIBIKE_OPERATIONS_IT_PASSWORD", ""));
        jdbc = new JdbcTemplate(datasource);
        assertEquals("citibike_operations_it", jdbc.queryForObject("SELECT DATABASE()", String.class),
                "These destructive tests require the dedicated citibike_operations_it database");
        new ResourceDatabasePopulator(new FileSystemResource("../sql/serving.sql")).execute(datasource);
        for (String table : List.of("ads_rebalance_suggestion", "ads_station_current_risk", "live_release",
                "historical_release", "dim_station_v1", "dws_station_hourly_flow_v1",
                "dws_station_hour_profile_v1", "dws_station_od_hourly_v1")) {
            jdbc.update("DELETE FROM " + table);
        }
        new ResourceDatabasePopulator(new FileSystemResource("../fixtures/day2/seed.sql")).execute(datasource);
        store = new JdbcOperationsStore(jdbc, new DataSourceTransactionManager(datasource));
    }

    @Test
    void baselineFiltersDatasetAndNewYorkDayHourAndIsImmutable() {
        var baseline = store.baseline(SNAPSHOT_AT);
        assertEquals(DATASET, baseline.datasetId());
        assertEquals(Map.of(
                "4199.12", Map.of(new DayHour(3, 8), new Profile(2, 0, 2, 2)),
                "5484.09", Map.of(new DayHour(3, 8), new Profile(0, 2, -2, 2))), baseline.profiles());
        assertEquals(baseline, store.baseline(Instant.parse("2025-07-09T12:00:00Z")));
        assertTrue(store.baseline(Instant.parse("2025-02-05T04:00:00Z")).profiles().isEmpty());
        assertThrows(UnsupportedOperationException.class, () -> baseline.profiles().clear());
        assertThrows(UnsupportedOperationException.class, () -> baseline.profiles().get("4199.12").clear());

        jdbc.update("UPDATE dws_station_hour_profile_v1 SET dataset_id = ? WHERE station_id = ?",
                "a".repeat(64), "4199.12");
        assertEquals(Map.of("5484.09", Map.of(new DayHour(3, 8), new Profile(0, 2, -2, 2))),
                store.baseline(SNAPSHOT_AT).profiles());
    }

    @Test
    void baselineKeepsOneSnapshotWhenHistoricalPublicationChangesBetweenReads() {
        var expected = store.baseline(SNAPSHOT_AT);
        JdbcTemplate concurrentPublication = new JdbcTemplate(datasource) {
            @Override
            public <T> List<T> queryForList(String sql, Class<T> elementType) {
                List<T> release = super.queryForList(sql, elementType);
                assertTrue(TransactionSynchronizationManager.isActualTransactionActive());
                assertTrue(TransactionSynchronizationManager.isCurrentTransactionReadOnly());
                assertEquals(Connection.TRANSACTION_REPEATABLE_READ,
                        TransactionSynchronizationManager.getCurrentTransactionIsolationLevel());
                try (Connection connection = datasource.getConnection();
                     var statement = connection.createStatement()) {
                    connection.setAutoCommit(false);
                    statement.executeUpdate("UPDATE historical_release SET dataset_id = '" + "a".repeat(64) + "'");
                    statement.executeUpdate("UPDATE dws_station_hour_profile_v1 SET dataset_id = '"
                            + "a".repeat(64) + "', avg_net_flow = 99");
                    connection.commit();
                } catch (SQLException e) {
                    throw new AssertionError(e);
                }
                return release;
            }
        };
        var concurrentStore = new JdbcOperationsStore(concurrentPublication,
                new DataSourceTransactionManager(datasource));
        assertEquals(expected, concurrentStore.baseline(SNAPSHOT_AT));
        assertEquals("a".repeat(64), store.baseline(SNAPSHOT_AT).datasetId());
        assertEquals(99, store.baseline(SNAPSHOT_AT).profiles().get("4199.12")
                .get(new DayHour(3, 8)).avgNetFlow());
    }

    @Test
    void publishesSharedFixtureExactlyAndEmptySnapshotReplacesBothAdsTables() throws Exception {
        var expected = publication();
        assertEquals(FIXTURE_RELEASE, store.latestRelease().orElseThrow());
        jdbc.update("DELETE FROM ads_rebalance_suggestion");
        jdbc.update("DELETE FROM ads_station_current_risk");
        jdbc.update("DELETE FROM live_release");
        assertTrue(store.latestRelease().isEmpty());

        List<Risk> risks = fixtureRisks(store.baseline(SNAPSHOT_AT));
        store.publish(risks, Operations.rebalance(SNAPSHOT, risks, AS_OF), FIXTURE_RELEASE);
        assertEquals(expected, publication());
        assertEquals(FIXTURE_RELEASE, store.latestRelease().orElseThrow());

        Instant next = Instant.parse("2025-02-05T13:01:00.123456Z");
        Release empty = new Release("a".repeat(64), METADATA, DATASET, next, next.plusSeconds(1),
                next.plusSeconds(2), next.plusSeconds(3), "GBFS_LIVE", "wall");
        store.publish(List.of(), List.of(), empty);
        assertEquals(List.of(), publication().get("risks"));
        assertEquals(List.of(), publication().get("suggestions"));
        assertEquals(empty, store.latestRelease().orElseThrow());
        assertEquals("2025-02-05 13:01:00.123456", jdbc.queryForObject(
                "SELECT DATE_FORMAT(snapshot_at_utc, '%Y-%m-%d %H:%i:%s.%f') FROM live_release", String.class));
    }

    @Test
    void missingBaselineStillPublishesInventoryAndPreservesSqlNulls() throws Exception {
        jdbc.update("DELETE FROM historical_release");
        jdbc.update("DELETE FROM live_release");
        var baseline = store.baseline(SNAPSHOT_AT);
        assertNull(baseline.datasetId());
        assertTrue(baseline.profiles().isEmpty());
        List<Risk> risks = new ArrayList<>(fixtureRisks(baseline));
        risks.add(new RiskCalculator().calculate(new Observation("nullable", SNAPSHOT_AT, null,
                null, null, null, false, true), null, Map.of(), AS_OF, null));
        Release release = new Release(SNAPSHOT, METADATA, null, SNAPSHOT_AT, AS_OF, AS_OF, AS_OF,
                "FIXTURE", "recorded");
        store.publish(risks, Operations.rebalance(SNAPSHOT, risks, AS_OF), release);

        assertEquals(release, store.latestRelease().orElseThrow());
        assertEquals(3, jdbc.queryForObject("SELECT COUNT(*) FROM ads_station_current_risk", Integer.class));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM ads_rebalance_suggestion", Integer.class));
        Map<String, Object> station = jdbc.queryForMap(
                "SELECT * FROM ads_station_current_risk WHERE station_id = '4199.12'");
        assertEquals(34, station.get("num_bikes_available"));
        assertEquals("OVERFLOW_RISK", station.get("current_status"));
        assertEquals("INSUFFICIENT_DATA", station.get("forecast_status"));
        assertEquals("NO_BASELINE", station.get("forecast_reason"));
        for (String column : List.of("expected_inbound_1h", "expected_outbound_1h", "expected_net_flow_1h",
                "projected_bikes_1h", "sample_days", "forecast_for_utc")) assertNull(station.get(column), column);

        Map<String, Object> nullable = jdbc.queryForMap(
                "SELECT * FROM ads_station_current_risk WHERE station_id = 'nullable'");
        for (String column : List.of("station_name", "lat", "lon", "capacity", "num_bikes_available",
                "num_docks_available", "is_installed", "last_reported_at_utc", "fill_ratio"))
            assertNull(nullable.get(column), column);
        assertFalse(jdbc.queryForObject(
                "SELECT is_renting FROM ads_station_current_risk WHERE station_id = 'nullable'", Boolean.class));
        assertTrue(jdbc.queryForObject(
                "SELECT is_returning FROM ads_station_current_risk WHERE station_id = 'nullable'", Boolean.class));
    }

    @Test
    void insertFailureAfterDeletesRollsBackBothAdsTablesAndEveryReleaseTimestamp() throws Exception {
        var before = publication();
        List<Risk> risks = fixtureRisks(store.baseline(SNAPSHOT_AT));
        Suggestion first = Operations.rebalance("a".repeat(64), risks, AS_OF.plusSeconds(60)).get(0);
        Suggestion duplicatePriority = new Suggestion("a".repeat(64) + ":2", first.snapshotId(),
                first.fromStationId(), first.toStationId(), first.moveBikes(), first.fromSurplus(),
                first.toDeficit(), first.distanceMeters(), first.priority(), first.generatedAt(), first.expiresAt());
        assertThrows(DataIntegrityViolationException.class, () -> store.publish(risks,
                List.of(first, duplicatePriority), release("a".repeat(64), SNAPSHOT_AT.plusSeconds(60))));
        assertEquals(before, publication());
        assertEquals(FIXTURE_RELEASE, store.latestRelease().orElseThrow());
    }

    @Test
    void restartedStoreIgnoresDurableIdWithoutRefreshingRowsOrClock() {
        var before = publication();
        var restarted = new JdbcOperationsStore(new JdbcTemplate(datasource),
                new DataSourceTransactionManager(datasource));
        assertEquals(FIXTURE_RELEASE, restarted.latestRelease().orElseThrow());
        restarted.publish(List.of(), List.of(), release(SNAPSHOT, SNAPSHOT_AT.plusSeconds(600)));
        assertEquals(before, publication());
        assertEquals(FIXTURE_RELEASE, restarted.latestRelease().orElseThrow());
    }

    @Test
    void differentIdWithNonNewerSourceTimeIsTerminalAndPreservesPublication() {
        var before = publication();
        for (Instant timestamp : List.of(SNAPSHOT_AT, SNAPSHOT_AT.minusSeconds(1))) {
            assertThrows(JdbcOperationsStore.StalePublication.class, () -> store.publish(
                    List.of(), List.of(), release("a".repeat(64), timestamp)));
            assertEquals(before, publication());
        }
    }

    private List<Risk> fixtureRisks(JdbcOperationsStore.Baseline baseline) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Metadata> metadata = new HashMap<>();
        for (JsonNode m : mapper.readTree(Path.of("../fixtures/day2/metadata.json").toFile())) {
            String id = m.get("station_id").asText();
            metadata.put(id, new Metadata(id, m.get("station_name").asText(), m.get("lat").asDouble(),
                    m.get("lon").asDouble(), m.get("capacity").asInt()));
        }
        List<Risk> risks = new ArrayList<>();
        for (String line : Files.readAllLines(Path.of("../fixtures/day2/events.ndjson"))) {
            JsonNode event = mapper.readTree(line);
            if (!"station".equals(event.get("headers").get("record_type").asText())) continue;
            JsonNode v = event.get("value");
            String id = v.get("station_id").asText();
            Observation observation = new Observation(id, Instant.parse(v.get("snapshot_at_utc").asText()),
                    Instant.parse(v.get("last_reported_at_utc").asText()), v.get("num_bikes_available").asInt(),
                    v.get("num_docks_available").asInt(), v.get("is_installed").asBoolean(),
                    v.get("is_renting").asBoolean(), v.get("is_returning").asBoolean());
            risks.add(new RiskCalculator().calculate(observation, metadata.get(id),
                    baseline.profiles().getOrDefault(id, Map.of()), AS_OF, baseline.datasetId()));
        }
        return risks;
    }

    private Map<String, List<Map<String, Object>>> publication() {
        return Map.of("risks", jdbc.queryForList("SELECT * FROM ads_station_current_risk ORDER BY station_id"),
                "suggestions", jdbc.queryForList("SELECT * FROM ads_rebalance_suggestion ORDER BY priority"),
                "release", jdbc.queryForList("SELECT * FROM live_release"));
    }

    private static Release release(String snapshotId, Instant snapshotAt) {
        return new Release(snapshotId, METADATA, DATASET, snapshotAt, snapshotAt.plusSeconds(2),
                snapshotAt.plusSeconds(3), snapshotAt.plusSeconds(4), "FIXTURE", "recorded");
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv().getOrDefault(name, "");
        if (value.isBlank()) throw new IllegalStateException(name + " must be set for operations MySQL tests");
        return value;
    }
}
