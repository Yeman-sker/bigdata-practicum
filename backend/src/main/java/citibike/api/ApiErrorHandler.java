package citibike.api;

import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.CannotCreateTransactionException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ApiErrorHandler {

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiModels.ErrorResponse> handleApiException(ApiException exception) {
        return response(exception.code(), exception.getMessage());
    }

    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<ApiModels.ErrorResponse> handleDataAccessException(DataAccessException exception) {
        return response(ApiErrorCode.DATA_UNAVAILABLE, "data unavailable");
    }

    @ExceptionHandler(CannotCreateTransactionException.class)
    public ResponseEntity<ApiModels.ErrorResponse> handleTransactionException(
            CannotCreateTransactionException exception) {
        return response(ApiErrorCode.DATA_UNAVAILABLE, "data unavailable");
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiModels.ErrorResponse> handleUnexpectedException(Exception exception) {
        return response(ApiErrorCode.INTERNAL_ERROR, "internal error");
    }

    private ResponseEntity<ApiModels.ErrorResponse> response(ApiErrorCode code, String message) {
        String safeMessage = message == null || message.isBlank() ? code.name().toLowerCase() : message;
        return ResponseEntity.status(code.status())
                .body(new ApiModels.ErrorResponse(new ApiModels.ErrorDetail(code.name(), safeMessage)));
    }
}
