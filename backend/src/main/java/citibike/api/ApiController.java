package citibike.api;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.Map;
import java.util.Set;

import static citibike.api.ApiModels.AvailabilityResponse;
import static citibike.api.ApiModels.HistoryResponse;
import static citibike.api.ApiModels.MapResponse;

@RestController
@RequestMapping("/api/v1")
public class ApiController {

    private static final Set<String> MAP_PARAMETERS = Set.of("mode", "service_date", "hour");
    private static final Set<String> HISTORY_PARAMETERS = Set.of("day_of_week", "service_date");

    private final ApiService service;

    public ApiController(ApiService service) {
        this.service = service;
    }

    @GetMapping("/map")
    public MapResponse getMap(HttpServletRequest request) {
        rejectUnknownParameters(request, MAP_PARAMETERS);
        String mode = singleParameter(request, "mode");
        LocalDate serviceDate = dateParameter(request, "service_date");
        Integer hour = integerParameter(request, "hour");
        return service.getMap(mode, serviceDate, hour);
    }

    @GetMapping("/history/availability")
    public AvailabilityResponse getAvailability(HttpServletRequest request) {
        rejectUnknownParameters(request, Set.of());
        return service.getAvailability();
    }

    @GetMapping("/stations/{stationId}/history")
    public HistoryResponse getHistory(@PathVariable("stationId") String stationId, HttpServletRequest request) {
        rejectUnknownParameters(request, HISTORY_PARAMETERS);
        Integer dayOfWeek = integerParameter(request, "day_of_week");
        LocalDate serviceDate = dateParameter(request, "service_date");
        return service.getHistory(stationId, dayOfWeek, serviceDate);
    }

    private static void rejectUnknownParameters(HttpServletRequest request, Set<String> allowed) {
        for (String name : request.getParameterMap().keySet()) {
            if (!allowed.contains(name)) {
                throw ApiException.invalid("unknown parameter");
            }
        }
    }

    private static String singleParameter(HttpServletRequest request, String name) {
        Map<String, String[]> parameters = request.getParameterMap();
        String[] values = parameters.get(name);
        if (values == null) {
            return null;
        }
        if (values.length != 1 || values[0] == null || values[0].isBlank()) {
            throw ApiException.invalid("invalid parameter");
        }
        return values[0];
    }

    private static Integer integerParameter(HttpServletRequest request, String name) {
        String value = singleParameter(request, name);
        if (value == null) {
            return null;
        }
        try {
            return Integer.valueOf(value);
        } catch (NumberFormatException exception) {
            throw ApiException.invalid("invalid parameter");
        }
    }

    private static LocalDate dateParameter(HttpServletRequest request, String name) {
        String value = singleParameter(request, name);
        if (value == null) {
            return null;
        }
        try {
            return LocalDate.parse(value);
        } catch (DateTimeParseException exception) {
            throw ApiException.invalid("invalid parameter");
        }
    }
}
