package citibike.api;

public class ApiException extends RuntimeException {

    private final ApiErrorCode code;

    public ApiException(ApiErrorCode code, String message) {
        super(message);
        this.code = code;
    }

    public ApiErrorCode code() {
        return code;
    }

    public static ApiException invalid(String message) {
        return new ApiException(ApiErrorCode.INVALID_PARAMETER, message);
    }

    public static ApiException notFound(String message) {
        return new ApiException(ApiErrorCode.NOT_FOUND, message);
    }

    public static ApiException tooLarge(String message) {
        return new ApiException(ApiErrorCode.RESULT_TOO_LARGE, message);
    }

    public static ApiException unavailable(String message) {
        return new ApiException(ApiErrorCode.DATA_UNAVAILABLE, message);
    }

    public static ApiException internal(String message) {
        return new ApiException(ApiErrorCode.INTERNAL_ERROR, message);
    }
}
