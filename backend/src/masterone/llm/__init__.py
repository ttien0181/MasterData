# LLM integration subsystem — Phase L0 + L1
# Chỉ export những gì module bên ngoài cần dùng.
from masterone.llm.config import LLMSettings, get_llm_settings
from masterone.llm.client import LLMClient, LLMError, LLMSchemaError, LLMTimeoutError, LLMDisabledError, get_llm_client
from masterone.llm.circuit_breaker import CircuitBreaker, LLMCircuitOpenError, get_circuit_breaker
from masterone.llm.metrics import LLMMetrics, get_metrics
from masterone.llm.contracts import (
    SemanticField,
    SemanticResult,
    SemanticReasonCode,
    SemanticInputItem,
    SemanticOutputItem,
    SemanticBatchRequest,
    SemanticBatchResponse,
    SemanticEvidence,
    ExplanationInput,
    ExplanationOutput,
    LLMRequestStatus,
)

__all__ = [
    "LLMSettings",
    "get_llm_settings",
    "LLMClient",
    "LLMError",
    "LLMSchemaError",
    "LLMTimeoutError",
    "LLMDisabledError",
    "get_llm_client",
    "CircuitBreaker",
    "LLMCircuitOpenError",
    "get_circuit_breaker",
    "LLMMetrics",
    "get_metrics",
    "SemanticField",
    "SemanticResult",
    "SemanticReasonCode",
    "SemanticInputItem",
    "SemanticOutputItem",
    "SemanticBatchRequest",
    "SemanticBatchResponse",
    "SemanticEvidence",
    "ExplanationInput",
    "ExplanationOutput",
    "LLMRequestStatus",
]
