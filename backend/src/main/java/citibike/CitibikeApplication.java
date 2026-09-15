package citibike;

import citibike.api.ApiProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

import java.time.Clock;

@SpringBootApplication
@EnableConfigurationProperties(ApiProperties.class)
public class CitibikeApplication {

    @Bean
    public Clock utcClock() {
        return Clock.systemUTC();
    }

    public static void main(String[] args) {
        SpringApplication.run(CitibikeApplication.class, args);
    }
}
