import os
import logging

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _collector_endpoint() -> str:
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006").strip().rstrip("/")
    protocol = os.getenv("PHOENIX_PROTOCOL", "").strip().lower()
    if protocol != "grpc" and endpoint and not endpoint.endswith("/v1/traces"):
        endpoint += "/v1/traces"
    return endpoint


def setup_phoenix_tracing() -> bool:
    if not _env_flag("PHOENIX_ENABLED"):
        logger.info("Phoenix tracing disabled")
        return False

    try:
        from phoenix.otel import register

        register(
            project_name=os.getenv("PHOENIX_PROJECT_NAME", "ouroboros-local"),
            endpoint=_collector_endpoint(),
            protocol=os.getenv("PHOENIX_PROTOCOL", "http/protobuf"),
            batch=_env_flag("PHOENIX_BATCH", default=True),
            auto_instrument=True,
        )

        logger.info(
            "Phoenix tracing enabled: project=%s endpoint=%s",
            os.getenv("PHOENIX_PROJECT_NAME", "ouroboros-local"),
            _collector_endpoint(),
        )
        return True

    except Exception:
        logger.exception("Phoenix tracing setup failed")
        return False
