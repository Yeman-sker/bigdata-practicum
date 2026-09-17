package citibike.api;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Runs only when a dedicated MySQL fixture database is explicitly provided.
 * The default Maven test suite remains independent of external services.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("fixture")
@EnabledIfEnvironmentVariable(named = "CITIBIKE_MYSQL_IT", matches = "true")
class ApiMySqlIntegrationTest {

    private static final String DATASET = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private JdbcTemplate jdbc;

    @DynamicPropertySource
    static void datasourceProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", () -> requiredEnvironment("CITIBIKE_MYSQL_IT_URL"));
        registry.add("spring.datasource.username", () -> requiredEnvironment("CITIBIKE_MYSQL_IT_USERNAME"));
        registry.add("spring.datasource.password", () -> environment("CITIBIKE_MYSQL_IT_PASSWORD"));
    }

    @Test
    void seedUsesRealJdbcRepositoryForAllReadEndpoints() throws Exception {
        mvc.perform(get("/api/v1/history/availability"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.dataset_id").value(DATASET))
                .andExpect(jsonPath("$.dates.length()").value(4));

        mvc.perform(get("/api/v1/map").param("mode", "live"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.stations.length()").value(2))
                .andExpect(jsonPath("$.stations[0].station_id").value("4199.12"))
                .andExpect(jsonPath("$.suggestions.length()").value(1));

        mvc.perform(get("/api/v1/map").param("mode", "replay")
                        .param("service_date", "2025-01-15").param("hour", "8"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.dataset_id").value(DATASET))
                .andExpect(jsonPath("$.stations.length()").value(2))
                .andExpect(jsonPath("$.flows.length()").value(1));

        mvc.perform(get("/api/v1/stations/5484.09/history")
                        .param("day_of_week", "3").param("service_date", "2025-01-15"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.station_name").value("样例甲站"))
                .andExpect(jsonPath("$.profile.length()").value(24))
                .andExpect(jsonPath("$.actual.length()").value(24));
    }

    @Test
    @Transactional
    void missingLiveReleaseReturns503FromRealJdbcPath() throws Exception {
        jdbc.update("DELETE FROM live_release WHERE singleton = 1");

        mvc.perform(get("/api/v1/map").param("mode", "live"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("DATA_UNAVAILABLE"));
    }

    private static String requiredEnvironment(String name) {
        String value = environment(name);
        if (value.isBlank()) {
            throw new IllegalStateException(name + " must be set for MySQL integration tests");
        }
        return value;
    }

    private static String environment(String name) {
        return System.getenv().getOrDefault(name, "");
    }
}
