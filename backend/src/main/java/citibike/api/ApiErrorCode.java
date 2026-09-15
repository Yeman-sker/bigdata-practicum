package citibike.api;

import org.springframework.http.HttpStatus;

public enum ApiErrorCode {
    INVALID_PARAMETER(HttpStatus.BAD_REQUEST),
    NOT_FOUND(HttpStatus.NOT_FOUND),
    RESULT_TOO_LARGE(HttpStatus.UNPROCESSABLE_ENTITY),
    DATA_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE),
    INTERNAL_ERROR(HttpStatus.INTERNAL_SERVER_ERROR);

    private final HttpStatus status;

    ApiErrorCode(HttpStatus status) {
        this.status = status;
    }

    public HttpStatus status() {
        return status;
    }
}
