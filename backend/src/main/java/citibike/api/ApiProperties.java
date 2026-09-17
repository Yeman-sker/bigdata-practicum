package citibike.api;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "citibike.api")
public class ApiProperties {

    private String historicalDataOrigin = "HISTORICAL";
    private String replayClockMode = "wall";
    private long liveTtlSeconds = 180;

    public String getHistoricalDataOrigin() {
        return historicalDataOrigin;
    }

    public void setHistoricalDataOrigin(String historicalDataOrigin) {
        this.historicalDataOrigin = historicalDataOrigin;
    }

    public String getReplayClockMode() {
        return replayClockMode;
    }

    public void setReplayClockMode(String replayClockMode) {
        this.replayClockMode = replayClockMode;
    }

    public long getLiveTtlSeconds() {
        return liveTtlSeconds;
    }

    public void setLiveTtlSeconds(long liveTtlSeconds) {
        this.liveTtlSeconds = liveTtlSeconds;
    }
}
