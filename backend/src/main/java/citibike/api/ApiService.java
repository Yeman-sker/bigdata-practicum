package citibike.api;

import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.stereotype.Service;

import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

import static citibike.api.ApiModels.ActualHour;
import static citibike.api.ApiModels.ActualRow;
import static citibike.api.ApiModels.AvailableDate;
import static citibike.api.ApiModels.AvailabilityPoint;
import static citibike.api.ApiModels.AvailabilityResponse;
import static citibike.api.ApiModels.Flow;
import static citibike.api.ApiModels.FlowRow;
import static citibike.api.ApiModels.HistoryResponse;
import static citibike.api.ApiModels.HistoricalRelease;
import static citibike.api.ApiModels.LiveRelease;
import static citibike.api.ApiModels.LiveStation;
import static citibike.api.ApiModels.MapResponse;
import static citibike.api.ApiModels.ProfileHour;
import static citibike.api.ApiModels.ProfileRow;
import static citibike.api.ApiModels.Reason;
import static citibike.api.ApiModels.RiskStatus;
import static citibike.api.ApiModels.Station;
import static citibike.api.ApiModels.StationInfo;
import static citibike.api.ApiModels.Suggestion;
import static citibike.api.ApiModels.SuggestionRow;

@Service
public class ApiService {

    private static final int MAX_STATIONS = 5_000;
    private static final int MAX_FLOWS = 50_000;
    private static final int MAX_SUGGESTIONS = 10_000;
    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
    private static final DateTimeFormatter INSTANT_FORMATTER = DateTimeFormatter.ISO_INSTANT;

    private final ApiDataRepository repository;
    private final ApiProperties properties;
    private final Clock clock;

    public ApiService(ApiDataRepository repository, ApiProperties properties, Clock clock) {
        this.repository = repository;
        this.properties = properties;
        this.clock = clock;
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public MapResponse getMap(String mode, LocalDate serviceDate, Integer hour) {
        if (mode == null || mode.isBlank()) {
            throw ApiException.invalid("mode is required");
        }
        return switch (mode) {
            case "live" -> getLiveMap(serviceDate, hour);
            case "replay" -> getReplayMap(serviceDate, hour);
            default -> throw ApiException.invalid("invalid mode");
        };
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public AvailabilityResponse getAvailability() {
        HistoricalRelease release = historicalRelease();
        List<AvailabilityPoint> points = repository.findAvailability(release.datasetId());
        Map<LocalDate, List<Integer>> byDate = new LinkedHashMap<>();
        for (AvailabilityPoint point : points) {
            byDate.computeIfAbsent(point.serviceDate(), ignored -> new ArrayList<>()).add(point.hour());
        }
        List<AvailableDate> dates = byDate.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> new AvailableDate(entry.getKey().toString(),
                        entry.getValue().stream().distinct().sorted().toList()))
                .toList();
        return new AvailabilityResponse("1.1", release.datasetId(), release.sourceMonths(), dates);
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public HistoryResponse getHistory(String stationId, Integer dayOfWeek, LocalDate serviceDate) {
        validateStationId(stationId);
        if (dayOfWeek == null || dayOfWeek < 1 || dayOfWeek > 7) {
            throw ApiException.invalid("invalid day_of_week");
        }
        if (serviceDate != null) {
            if (DayOfWeek.from(serviceDate).getValue() != dayOfWeek) {
                throw ApiException.invalid("service_date does not match day_of_week");
            }
        }
        HistoricalRelease release = historicalRelease();
        if (serviceDate != null) {
            if (!isAvailable(release.datasetId(), serviceDate, null)) {
                throw ApiException.notFound("service date not found");
            }
        }

        Optional<StationInfo> historicalStation = repository.findHistoricalStation(release.datasetId(), stationId);
        StationInfo station = historicalStation.orElseGet(
                () -> repository.findCurrentStation(stationId).orElse(null));
        if (station == null) {
            throw ApiException.notFound("station not found");
        }

        List<ProfileHour> profile = toProfileHours(
                repository.findProfile(release.datasetId(), stationId, dayOfWeek));

        List<ActualHour> actual = List.of();
        if (serviceDate != null) {
            List<ActualRow> actualRows = repository.findActual(release.datasetId(), stationId, serviceDate);
            if (!actualRows.isEmpty()) {
                Map<Integer, ActualRow> byHour = actualRows.stream()
                        .collect(Collectors.toMap(ActualRow::hour, Function.identity(), (left, right) -> left));
                List<ActualHour> filled = new ArrayList<>();
                for (int currentHour = 0; currentHour < 24; currentHour++) {
                    ActualRow row = byHour.get(currentHour);
                    filled.add(toActualHour(currentHour, row));
                }
                actual = List.copyOf(filled);
            }
        }

        return new HistoryResponse(
                "1.1",
                release.datasetId(),
                stationId,
                station.stationName(),
                dayOfWeek,
                serviceDate == null ? null : serviceDate.toString(),
                release.sourceMonths(),
                dateString(release.minServiceDate()),
                dateString(release.maxServiceDate()),
                profile,
                actual);
    }

    private MapResponse getLiveMap(LocalDate serviceDate, Integer hour) {
        if (serviceDate != null || hour != null) {
            throw ApiException.invalid("live does not accept historical parameters");
        }
        LiveRelease release = repository.findLiveRelease()
                .orElseThrow(() -> ApiException.unavailable("live data unavailable"));
        Instant servedAt = clock.instant();
        Instant asOf = effectiveLiveTime(release, servedAt);
        List<LiveStation> rows = repository.findLiveStations(release.snapshotId(), MAX_STATIONS + 1);
        ensureWithinLimit(rows.size(), MAX_STATIONS, "stations");
        boolean snapshotExpired = isExpired(
                release.snapshotAtUtc().plusSeconds(properties.getLiveTtlSeconds()), asOf);
        boolean noBaseline = release.baselineDatasetId() == null;
        List<Station> stations = rows.stream()
                .sorted(Comparator.comparing(LiveStation::stationId))
                .map(row -> toLiveStation(row, asOf,
                        snapshotExpired || isExpired(row.expiresAtUtc(), asOf), noBaseline))
                .toList();

        List<Suggestion> suggestions = List.of();
        if (!snapshotExpired && !noBaseline) {
            List<SuggestionRow> suggestionRows = repository.findLiveSuggestions(
                    release.snapshotId(), asOf, MAX_SUGGESTIONS + 1);
            ensureWithinLimit(suggestionRows.size(), MAX_SUGGESTIONS, "suggestions");
            suggestions = suggestionRows.stream()
                    .sorted(Comparator.comparing(SuggestionRow::priority))
                    .map(this::toSuggestion)
                    .toList();
        }

        LocalDate localServiceDate = release.snapshotAtUtc().atZone(NEW_YORK).toLocalDate();
        return new MapResponse(
                "1.1",
                "live",
                release.dataOrigin(),
                release.clockMode(),
                null,
                release.baselineDatasetId(),
                release.snapshotId(),
                release.metadataVersion(),
                localServiceDate.toString(),
                null,
                formatInstant(release.snapshotAtUtc()),
                formatInstant(asOf),
                formatInstant(servedAt),
                formatInstant(release.snapshotAtUtc().plusSeconds(properties.getLiveTtlSeconds())),
                stations,
                List.of(),
                suggestions);
    }

    private MapResponse getReplayMap(LocalDate serviceDate, Integer hour) {
        if (serviceDate == null || hour == null || hour < 0 || hour > 23) {
            throw ApiException.invalid("replay requires service_date and hour");
        }
        HistoricalRelease release = historicalRelease();
        if (!isAvailable(release.datasetId(), serviceDate, hour)) {
            throw ApiException.notFound("historical data not found");
        }

        List<String> stationIds = repository.findActiveStationIds(
                release.datasetId(), serviceDate, hour, MAX_STATIONS + 1);
        ensureWithinLimit(stationIds.size(), MAX_STATIONS, "stations");
        List<FlowRow> flowRows = repository.findFlows(release.datasetId(), serviceDate, hour, MAX_FLOWS + 1);
        ensureWithinLimit(flowRows.size(), MAX_FLOWS, "flows");

        Map<String, StationInfo> stationInfo = repository.findHistoricalStations(release.datasetId(), stationIds).stream()
                .collect(Collectors.toMap(StationInfo::stationId, Function.identity(), (left, right) -> left));
        List<Station> stations = stationIds.stream()
                .sorted()
                .map(id -> toHistoricalStation(id, stationInfo.get(id)))
                .toList();
        List<Flow> flows = flowRows.stream()
                .sorted(Comparator.comparing(FlowRow::fromStationId).thenComparing(FlowRow::toStationId))
                .map(row -> new Flow(row.fromStationId(), row.toStationId(), row.rideCount()))
                .toList();

        QueryTime queryTime = replayTime();
        return new MapResponse(
                "1.1",
                "replay",
                properties.getHistoricalDataOrigin(),
                queryTime.clockMode(),
                release.datasetId(),
                null,
                null,
                null,
                serviceDate.toString(),
                hour,
                null,
                formatInstant(queryTime.asOfUtc()),
                formatInstant(queryTime.servedAtUtc()),
                null,
                stations,
                flows,
                List.of());
    }

    private QueryTime replayTime() {
        Optional<LiveRelease> liveRelease = repository.findLiveRelease();
        Instant servedAt = clock.instant();
        if (liveRelease.isPresent()) {
            LiveRelease release = liveRelease.get();
            Instant asOf = "recorded".equals(release.clockMode()) ? release.asOfUtc() : servedAt;
            return new QueryTime(asOf, servedAt, release.clockMode());
        }
        return new QueryTime(servedAt, servedAt, properties.getReplayClockMode());
    }

    private Station toLiveStation(LiveStation row, Instant asOf, boolean expired, boolean noBaseline) {
        RiskStatus currentStatus = status(row.currentStatus());
        Reason currentReason = reason(row.currentReason());
        RiskStatus forecastStatus = status(row.forecastStatus());
        Reason forecastReason = reason(row.forecastReason());
        Double fillRatio = row.fillRatio();
        Double expectedInbound = row.expectedInbound();
        Double expectedOutbound = row.expectedOutbound();
        Double expectedNetFlow = row.expectedNetFlow();
        Double projectedBikes = row.projectedBikes();
        Long sampleDays = row.sampleDays();
        Instant forecastFor = row.forecastForUtc();

        if (expired) {
            currentStatus = RiskStatus.STALE_DATA;
            forecastStatus = RiskStatus.STALE_DATA;
            currentReason = Reason.STALE_OBSERVATION;
            forecastReason = Reason.STALE_OBSERVATION;
            fillRatio = null;
            expectedInbound = null;
            expectedOutbound = null;
            expectedNetFlow = null;
            projectedBikes = null;
            sampleDays = null;
            forecastFor = null;
        } else if (noBaseline) {
            if (isForecastEligible(currentStatus)) {
                forecastStatus = RiskStatus.INSUFFICIENT_DATA;
                forecastReason = Reason.NO_BASELINE;
            }
            expectedInbound = null;
            expectedOutbound = null;
            expectedNetFlow = null;
            projectedBikes = null;
            sampleDays = null;
            forecastFor = null;
        }

        return new Station(
                row.stationId(),
                row.stationName(),
                row.lat(),
                row.lon(),
                row.capacity(),
                row.numBikesAvailable(),
                row.numDocksAvailable(),
                row.installed(),
                row.renting(),
                row.returning(),
                currentStatus,
                forecastStatus,
                currentReason,
                forecastReason,
                fillRatio,
                expectedInbound,
                expectedOutbound,
                expectedNetFlow,
                projectedBikes,
                sampleDays,
                formatInstant(row.snapshotAtUtc()),
                formatInstant(row.lastReportedAtUtc()),
                formatInstant(row.expiresAtUtc()),
                formatInstant(forecastFor));
    }

    private static Station toHistoricalStation(String stationId, StationInfo info) {
        return new Station(
                stationId,
                info == null ? null : info.stationName(),
                info == null ? null : info.lat(),
                info == null ? null : info.lon(),
                info == null ? null : info.capacity(),
                null,
                null,
                null,
                null,
                null,
                RiskStatus.NOT_APPLICABLE,
                RiskStatus.NOT_APPLICABLE,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null);
    }

    private Suggestion toSuggestion(SuggestionRow row) {
        return new Suggestion(
                row.suggestionId(),
                row.fromStationId(),
                row.toStationId(),
                row.moveBikes(),
                row.fromSurplus(),
                row.toDeficit(),
                row.distanceMeters(),
                row.priority(),
                formatInstant(row.generatedAtUtc()),
                formatInstant(row.expiresAtUtc()));
    }

    private static ActualHour toActualHour(int hour, ActualRow row) {
        if (row == null) {
            return new ActualHour(hour, 0L, 0L, 0L, 0L, 0L, 0L, 0L, 0L);
        }
        return new ActualHour(hour, row.inboundRides(), row.outboundRides(), row.netFlow(), row.totalActivity(),
                row.electricOutbound(), row.classicOutbound(), row.memberOutbound(), row.casualOutbound());
    }

    private static List<ProfileHour> toProfileHours(List<ProfileRow> rows) {
        if (rows.isEmpty()) {
            return List.of();
        }
        Map<Integer, ProfileRow> byHour = new LinkedHashMap<>();
        for (ProfileRow row : rows) {
            if (row == null || row.hour() == null || row.hour() < 0 || row.hour() > 23
                    || byHour.put(row.hour(), row) != null) {
                throw ApiException.internal("invalid profile result");
            }
        }
        if (byHour.size() != 24) {
            throw ApiException.internal("invalid profile result");
        }
        List<ProfileHour> profile = new ArrayList<>();
        for (int hour = 0; hour < 24; hour++) {
            ProfileRow row = byHour.get(hour);
            if (row == null) {
                throw ApiException.internal("invalid profile result");
            }
            profile.add(new ProfileHour(row.hour(), row.avgInbound(), row.avgOutbound(), row.avgNetFlow(),
                    row.medianNetFlow(), row.sampleDays()));
        }
        return List.copyOf(profile);
    }

    private boolean isAvailable(String datasetId, LocalDate serviceDate, Integer hour) {
        return repository.findAvailability(datasetId).stream()
                .anyMatch(point -> point.serviceDate().equals(serviceDate)
                        && (hour == null || point.hour().equals(hour)));
    }

    private HistoricalRelease historicalRelease() {
        return repository.findHistoricalRelease()
                .orElseThrow(() -> ApiException.unavailable("historical data unavailable"));
    }

    private Instant effectiveLiveTime(LiveRelease release, Instant wallClockNow) {
        if ("recorded".equals(release.clockMode())) {
            return release.asOfUtc();
        }
        return wallClockNow;
    }

    private static boolean isExpired(Instant expiresAtUtc, Instant asOfUtc) {
        return expiresAtUtc != null && !expiresAtUtc.isAfter(asOfUtc);
    }

    private static boolean isForecastEligible(RiskStatus currentStatus) {
        return switch (currentStatus) {
            case SHORTAGE_RISK, LOW_INVENTORY, HEALTHY, HIGH_INVENTORY, OVERFLOW_RISK -> true;
            default -> false;
        };
    }

    private static RiskStatus status(String value) {
        if (value == null) {
            throw new ApiException(ApiErrorCode.INTERNAL_ERROR, "invalid status");
        }
        try {
            return RiskStatus.valueOf(value);
        } catch (IllegalArgumentException exception) {
            throw new ApiException(ApiErrorCode.INTERNAL_ERROR, "invalid status");
        }
    }

    private static Reason reason(String value) {
        if (value == null) {
            return null;
        }
        try {
            return Reason.valueOf(value);
        } catch (IllegalArgumentException exception) {
            throw new ApiException(ApiErrorCode.INTERNAL_ERROR, "invalid reason");
        }
    }

    private static String formatInstant(Instant value) {
        return value == null ? null : INSTANT_FORMATTER.format(value);
    }

    private static String dateString(LocalDate value) {
        return value == null ? null : value.toString();
    }

    private static void validateStationId(String stationId) {
        if (stationId == null || stationId.isBlank() || stationId.length() > 128) {
            throw ApiException.invalid("invalid station_id");
        }
    }

    private static void ensureWithinLimit(int actual, int limit, String label) {
        if (actual > limit) {
            throw ApiException.tooLarge(label + " result too large");
        }
    }

    private record QueryTime(Instant asOfUtc, Instant servedAtUtc, String clockMode) {
    }
}
