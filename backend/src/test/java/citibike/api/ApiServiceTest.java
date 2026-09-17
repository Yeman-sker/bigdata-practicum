package citibike.api;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

import static citibike.api.ApiModels.ActualRow;
import static citibike.api.ApiModels.AvailabilityPoint;
import static citibike.api.ApiModels.FlowRow;
import static citibike.api.ApiModels.HistoricalRelease;
import static citibike.api.ApiModels.LiveRelease;
import static citibike.api.ApiModels.LiveStation;
import static citibike.api.ApiModels.ProfileRow;
import static citibike.api.ApiModels.StationInfo;
import static citibike.api.ApiModels.SuggestionRow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ApiServiceTest {

    private static final String DATASET = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e";
    private static final String SNAPSHOT = "144dc6c63f679f72b02b9ada5129d5393976fdb04be00534eda8ebe73c3fb3c3";
    private static final String METADATA = "84fb15f78a673f21687fce423e223a2970f7276903398e6e0c19c502ab1ea41b";
    private static final Instant SNAPSHOT_AT = Instant.parse("2025-02-05T13:00:00Z");
    private static final Instant AS_OF = Instant.parse("2025-02-05T13:00:02Z");
    private static final Instant EXPIRES = Instant.parse("2025-02-05T13:02:40Z");

    private FakeRepository repository;
    private ApiProperties properties;
    private ApiService service;

    @BeforeEach
    void setUp() {
        repository = new FakeRepository();
        properties = new ApiProperties();
        properties.setHistoricalDataOrigin("FIXTURE");
        service = new ApiService(repository, properties, Clock.fixed(AS_OF, ZoneOffset.UTC));
    }

    @Test
    void liveMapKeepsThePublishedBatchTogether() {
        repository.liveRelease = Optional.of(liveRelease(DATASET));
        repository.liveStations = List.of(liveStation("4199.12", "OVERFLOW_RISK"),
                liveStation("5484.09", "SHORTAGE_RISK"));
        repository.suggestions = List.of(new SuggestionRow(
                SNAPSHOT + ":1", "4199.12", "5484.09", 8, 8, 8, 84, 1,
                AS_OF, EXPIRES));

        var response = service.getMap("live", null, null);

        assertEquals("1.1", response.contract_version());
        assertEquals("2025-02-05", response.service_date());
        assertEquals(List.of("4199.12", "5484.09"),
                response.stations().stream().map(ApiModels.Station::station_id).toList());
        assertEquals(1, response.suggestions().size());
        assertEquals(8, response.suggestions().get(0).move_bikes());
        assertEquals("FIXTURE", response.data_origin());
    }

    @Test
    void noBaselinePreservesCurrentValuesButClearsForecastValues() {
        repository.liveRelease = Optional.of(liveRelease(null));
        repository.liveStations = List.of(liveStation("5484.09", "SHORTAGE_RISK"));
        repository.suggestions = List.of(new SuggestionRow(
                SNAPSHOT + ":1", "4199.12", "5484.09", 8, 8, 8, 84, 1,
                AS_OF, EXPIRES));

        var station = service.getMap("live", null, null).stations().get(0);

        assertEquals(6, station.num_bikes_available());
        assertEquals(ApiModels.RiskStatus.SHORTAGE_RISK, station.current_status());
        assertEquals(ApiModels.RiskStatus.INSUFFICIENT_DATA, station.forecast_status());
        assertEquals(ApiModels.Reason.NO_BASELINE, station.forecast_reason());
        assertTrue(station.expected_net_flow_1h() == null);
        assertTrue(station.sample_days() == null);
        assertTrue(service.getMap("live", null, null).suggestions().isEmpty());
    }

    @Test
    void noBaselineDoesNotMaskAnExistingCurrentDataError() {
        repository.liveRelease = Optional.of(liveRelease(null));
        repository.liveStations = List.of(liveStation("5484.09", "SERVICE_UNAVAILABLE"));

        var station = service.getMap("live", null, null).stations().get(0);

        assertEquals(ApiModels.RiskStatus.SERVICE_UNAVAILABLE, station.current_status());
        assertEquals(ApiModels.RiskStatus.SERVICE_UNAVAILABLE, station.forecast_status());
        assertTrue(station.expected_net_flow_1h() == null);
    }

    @Test
    void wallClockExpiryUsesOneRequestTimestampForBothClocks() {
        Instant wallNow = Instant.parse("2025-02-05T13:03:00Z");
        repository.liveRelease = Optional.of(new LiveRelease(
                SNAPSHOT, METADATA, DATASET, SNAPSHOT_AT, AS_OF, AS_OF, AS_OF, "FIXTURE", "wall"));
        repository.liveStations = List.of(liveStation("5484.09", "SHORTAGE_RISK"));
        service = new ApiService(repository, properties, Clock.fixed(wallNow, ZoneOffset.UTC));

        var response = service.getMap("live", null, null);

        assertEquals(wallNow.toString(), response.as_of_utc());
        assertEquals(wallNow.toString(), response.served_at_utc());
        assertEquals(ApiModels.RiskStatus.STALE_DATA, response.stations().get(0).current_status());
    }

    @Test
    void expiredRowsBecomeStaleAndExpiredSuggestionsAreNotServed() {
        Instant staleAsOf = Instant.parse("2025-02-05T13:04:00Z");
        repository.liveRelease = Optional.of(new LiveRelease(
                SNAPSHOT, METADATA, DATASET, SNAPSHOT_AT, AS_OF, staleAsOf, AS_OF, "FIXTURE", "recorded"));
        repository.liveStations = List.of(liveStation("5484.09", "SHORTAGE_RISK"));
        repository.suggestions = List.of(new SuggestionRow(
                SNAPSHOT + ":1", "4199.12", "5484.09", 8, 8, 8, 84, 1,
                AS_OF, EXPIRES));
        service = new ApiService(repository, properties, Clock.fixed(staleAsOf, ZoneOffset.UTC));

        var station = service.getMap("live", null, null).stations().get(0);

        assertEquals(ApiModels.RiskStatus.STALE_DATA, station.current_status());
        assertEquals(ApiModels.Reason.STALE_OBSERVATION, station.current_reason());
        assertEquals(6, station.num_bikes_available());
        assertTrue(station.fill_ratio() == null);
        assertTrue(service.getMap("live", null, null).suggestions().isEmpty());
    }

    @Test
    void replayReturnsOnlyHistoricalFieldsAndSortedFlows() {
        repository.liveRelease = Optional.of(liveRelease(DATASET));
        repository.historicalRelease = Optional.of(historicalRelease());
        repository.availability = List.of(new AvailabilityPoint(LocalDate.of(2025, 1, 15), 8));
        repository.activeStationIds = List.of("4199.12", "5484.09");
        repository.flows = List.of(new FlowRow("5484.09", "4199.12", 2L));
        repository.historicalStations = List.of(
                new StationInfo("4199.12", "样例乙站", 40.7, -74.001, 40),
                new StationInfo("5484.09", "样例甲站", 40.7, -74.0, 40));

        var response = service.getMap("replay", LocalDate.of(2025, 1, 15), 8);

        assertEquals(DATASET, response.dataset_id());
        assertEquals("recorded", response.clock_mode());
        assertEquals(2, response.stations().size());
        assertEquals(ApiModels.RiskStatus.NOT_APPLICABLE, response.stations().get(0).current_status());
        assertTrue(response.stations().get(0).num_bikes_available() == null);
        assertEquals(2L, response.flows().get(0).ride_count());
        assertTrue(response.suggestions().isEmpty());
    }

    @Test
    void historyFillsAnActiveDayToTwentyFourHours() {
        repository.historicalRelease = Optional.of(historicalRelease());
        repository.availability = List.of(new AvailabilityPoint(LocalDate.of(2025, 1, 15), 8));
        repository.historicalStations = List.of(new StationInfo("5484.09", "样例甲站", 40.7, -74.0, 40));
        repository.profile = profileRows();
        repository.actual = List.of(new ActualRow(8, 0L, 2L, -2L, 2L, 1L, 1L, 1L, 1L));

        var response = service.getHistory("5484.09", 3, LocalDate.of(2025, 1, 15));

        assertEquals(24, response.profile().size());
        assertEquals(-2.0, response.profile().get(8).avg_net_flow());
        assertEquals(24, response.actual().size());
        assertEquals(0L, response.actual().get(0).total_activity());
        assertEquals(2L, response.actual().get(8).outbound_rides());
    }

    @Test
    void availabilityIsGroupedAndSortedIndependentlyOfRepositoryOrder() {
        repository.historicalRelease = Optional.of(historicalRelease());
        repository.availability = List.of(
                new AvailabilityPoint(LocalDate.of(2025, 1, 15), 23),
                new AvailabilityPoint(LocalDate.of(2025, 1, 8), 8),
                new AvailabilityPoint(LocalDate.of(2025, 1, 15), 8),
                new AvailabilityPoint(LocalDate.of(2025, 1, 8), 8));

        var response = service.getAvailability();

        assertEquals(List.of(new ApiModels.AvailableDate("2025-01-08", List.of(8)),
                        new ApiModels.AvailableDate("2025-01-15", List.of(8, 23))),
                response.dates());
    }

    @Test
    void invalidReplayArgumentsAreRejectedBeforeReadingData() {
        assertThrows(ApiException.class, () -> service.getMap("replay", LocalDate.of(2025, 1, 15), 24));
        assertThrows(ApiException.class, () -> service.getMap("live", LocalDate.of(2025, 1, 15), null));
    }

    private static LiveRelease liveRelease(String baselineDatasetId) {
        return new LiveRelease(SNAPSHOT, METADATA, baselineDatasetId, SNAPSHOT_AT, AS_OF, AS_OF, AS_OF,
                "FIXTURE", "recorded");
    }

    private static HistoricalRelease historicalRelease() {
        return new HistoricalRelease(DATASET, List.of("2025-01"),
                LocalDate.of(2025, 1, 8), LocalDate.of(2025, 2, 1), AS_OF);
    }

    private static LiveStation liveStation(String stationId, String currentStatus) {
        return new LiveStation(stationId, "样例站", 40.7, -74.0, 40, 6, 34,
                true, true, true, currentStatus, currentStatus, null, null,
                0.15, 0.0, 2.0, -2.0, 4.0, 2L, SNAPSHOT_AT,
                Instant.parse("2025-02-05T12:59:40Z"), EXPIRES,
                Instant.parse("2025-02-05T14:00:00Z"));
    }

    private static List<ProfileRow> profileRows() {
        List<ProfileRow> rows = new ArrayList<>();
        for (int hour = 0; hour < 24; hour++) {
            rows.add(hour == 8
                    ? new ProfileRow(hour, 0.0, 2.0, -2.0, -2.0, 2L)
                    : new ProfileRow(hour, 0.0, 0.0, 0.0, 0.0, 2L));
        }
        return rows;
    }

    private static final class FakeRepository implements ApiDataRepository {
        private Optional<LiveRelease> liveRelease = Optional.empty();
        private List<LiveStation> liveStations = List.of();
        private List<SuggestionRow> suggestions = List.of();
        private Optional<HistoricalRelease> historicalRelease = Optional.empty();
        private List<AvailabilityPoint> availability = List.of();
        private List<String> activeStationIds = List.of();
        private List<FlowRow> flows = List.of();
        private List<StationInfo> historicalStations = List.of();
        private List<ProfileRow> profile = List.of();
        private List<ActualRow> actual = List.of();

        @Override
        public Optional<LiveRelease> findLiveRelease() {
            return liveRelease;
        }

        @Override
        public List<LiveStation> findLiveStations(String snapshotId, int limit) {
            return liveStations;
        }

        @Override
        public List<SuggestionRow> findLiveSuggestions(String snapshotId, Instant asOfUtc, int limit) {
            return suggestions.stream().filter(row -> row.expiresAtUtc().isAfter(asOfUtc)).toList();
        }

        @Override
        public Optional<HistoricalRelease> findHistoricalRelease() {
            return historicalRelease;
        }

        @Override
        public List<AvailabilityPoint> findAvailability(String datasetId) {
            return availability;
        }

        @Override
        public List<String> findActiveStationIds(String datasetId, LocalDate serviceDate, int hour, int limit) {
            return activeStationIds;
        }

        @Override
        public List<FlowRow> findFlows(String datasetId, LocalDate serviceDate, int hour, int limit) {
            return flows;
        }

        @Override
        public List<StationInfo> findHistoricalStations(String datasetId, Collection<String> stationIds) {
            return historicalStations;
        }

        @Override
        public Optional<StationInfo> findHistoricalStation(String datasetId, String stationId) {
            return historicalStations.stream().filter(station -> station.stationId().equals(stationId)).findFirst();
        }

        @Override
        public Optional<StationInfo> findCurrentStation(String stationId) {
            return Optional.empty();
        }

        @Override
        public List<ProfileRow> findProfile(String datasetId, String stationId, int dayOfWeek) {
            return profile;
        }

        @Override
        public List<ActualRow> findActual(String datasetId, String stationId, LocalDate serviceDate) {
            return actual;
        }
    }
}
