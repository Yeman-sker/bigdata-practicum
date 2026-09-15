package citibike.api;

import java.time.Instant;
import java.time.LocalDate;
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

/** Read-side boundary for the tables owned by the offline and operations streams. */
public interface ApiDataRepository {

    Optional<LiveRelease> findLiveRelease();

    List<LiveStation> findLiveStations(String snapshotId, int limit);

    List<SuggestionRow> findLiveSuggestions(String snapshotId, Instant asOfUtc, int limit);

    Optional<HistoricalRelease> findHistoricalRelease();

    List<AvailabilityPoint> findAvailability(String datasetId);

    List<String> findActiveStationIds(String datasetId, LocalDate serviceDate, int hour, int limit);

    List<FlowRow> findFlows(String datasetId, LocalDate serviceDate, int hour, int limit);

    List<StationInfo> findHistoricalStations(String datasetId, Collection<String> stationIds);

    Optional<StationInfo> findHistoricalStation(String datasetId, String stationId);

    Optional<StationInfo> findCurrentStation(String stationId);

    List<ProfileRow> findProfile(String datasetId, String stationId, int dayOfWeek);

    List<ActualRow> findActual(String datasetId, String stationId, LocalDate serviceDate);
}
