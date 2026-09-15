package citibike.api;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.hamcrest.Matchers.nullValue;
import static citibike.api.ApiModels.HistoryResponse;
import static citibike.api.ApiModels.MapResponse;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class ApiControllerTest {

    private ApiService service;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        service = mock(ApiService.class);
        mvc = MockMvcBuilders.standaloneSetup(new ApiController(service))
                .setControllerAdvice(new ApiErrorHandler())
                .build();
    }

    @Test
    void mapRejectsUnknownQueryParameters() throws Exception {
        mvc.perform(get("/api/v1/map").param("mode", "live").param("unexpected", "1"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("INVALID_PARAMETER"));

        verifyNoInteractions(service);
    }

    @Test
    void mapRejectsMalformedDate() throws Exception {
        mvc.perform(get("/api/v1/map").param("mode", "replay")
                        .param("service_date", "2025-02-30").param("hour", "8"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("INVALID_PARAMETER"));

        verifyNoInteractions(service);
    }

    @Test
    void mapSerializesExplicitNullFields() throws Exception {
        MapResponse response = new MapResponse("1.1", "replay", "FIXTURE", "recorded",
                "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e", null, null, null,
                "2025-01-15", 8, null, "2025-02-05T13:00:02Z", "2025-02-05T13:00:02Z", null,
                List.of(), List.of(), List.of());
        when(service.getMap("replay", java.time.LocalDate.of(2025, 1, 15), 8)).thenReturn(response);

        mvc.perform(get("/api/v1/map").param("mode", "replay")
                        .param("service_date", "2025-01-15").param("hour", "8"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("replay"))
                .andExpect(jsonPath("$.baseline_dataset_id").value(nullValue()))
                .andExpect(jsonPath("$.stations").isArray());
    }

    @Test
    void historyKeepsStationIdAsAStringPathValue() throws Exception {
        HistoryResponse response = new HistoryResponse("1.1",
                "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e",
                "5484.09", "样例甲站", 3, null, List.of("2025-01"),
                "2025-01-08", "2025-02-01", List.of(), List.of());
        when(service.getHistory("5484.09", 3, null)).thenReturn(response);

        mvc.perform(get("/api/v1/stations/5484.09/history").param("day_of_week", "3"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.station_id").value("5484.09"))
                .andExpect(jsonPath("$.service_date").value(nullValue()));
    }
}
