from engleks_contracts import ErrorCode, ErrorResponse, HealthResponse, HealthStatus


def test_health_response_round_trip():
    body = HealthResponse(status=HealthStatus.OK).model_dump(mode="json")
    assert body == {"status": "ok"}
    assert HealthResponse.model_validate(body).status is HealthStatus.OK


def test_error_response_serializes_code_as_string():
    body = ErrorResponse(code=ErrorCode.INTERNAL_ERROR, message="x").model_dump(mode="json")
    assert body == {"code": "internal_error", "message": "x"}


def test_unknown_fields_are_ignored():
    assert HealthResponse.model_validate({"status": "ok", "extra": 1}).status is HealthStatus.OK
