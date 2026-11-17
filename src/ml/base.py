"""
ml/base.py — ML 推理服务客户端基类
==================================
角色：封装对 sidecar ML 推理服务（视觉质检 :8001 / RUL 预测 :8002）的 HTTP 调用，
      统一超时、重试、熔断、指标上报、OTel 追踪。

容错策略（与 LLM 调用同规格）:
  - 超时：按服务分别配置（视觉 ~300ms/张，给 10s 排队余量；RUL <1s，给 5s）
  - 重试：fast_retry（2 次，指数退避 + 抖动）。5xx（服务预热中/内部错误）触发退避重试；
    4xx（参数校验失败）不重试语义上更有意义，但为避免重复实现非幂等过滤，仍会被
    捎带重试 2 次后按原样抛出（校验类错误由节点层兜底展示）
  - 熔断：连续失败 5 次熔断 30s，半开探测恢复（复用 src.core.retry.CircuitBreaker）
  - 降级：调用方（Graph 节点）捕获 MLServiceUnavailableError 后走 LLM 兜底，
    ML 挂了 ≠ 对话挂了

注意：熔断状态为进程内单例；多 Pod 部署时可在 lifespan 中 configure_redis() 共享状态。
"""
import time
from contextlib import contextmanager

import httpx

from src.core.config import get_settings
from src.core.exceptions import MLServiceUnavailableError, ModelCircuitOpenError
from src.core.retry import CircuitBreaker, RetryConfig, async_retry
from src.core.logging import get_logger
from src.observability.metrics import record_ml

logger = get_logger(__name__)

# ML 调用重试配置：ML 推理本身不慢，失败多是服务未就绪/瞬时网络问题，重试 2 次足够
ml_retry = RetryConfig(max_retries=2, base_delay=0.5, max_delay=5.0)


class _RetryableHTTPError(Exception):
    """5xx 响应（服务预热中/内部错误）：值得退避重试，重试耗尽后转为 MLServiceUnavailableError。"""

    def __init__(self, status: int, body: str):
        self.status = status
        super().__init__(f"HTTP {status}: {body[:200]}")


@contextmanager
def _ml_span(service: str, path: str):
    """OTel span 包裹（惰性导入：未装 opentelemetry / tracing 未初始化时静默跳过）。"""
    cm = None
    try:
        from src.observability.tracing import get_tracer
        cm = get_tracer(f"ml.{service}").start_as_current_span(f"ml.{service}")
    except Exception:
        cm = None
    if cm is None:
        yield None
        return
    with cm as span:
        try:
            span.set_attribute("ml.path", path)
        except Exception:
            pass
        yield span


class MLServiceClient:
    """单个 ML 推理服务的 HTTP 客户端（httpx 异步）。"""

    service: str = "base"  # 子类覆盖: vision / rul

    def __init__(self, base_url: str, timeout: float, service: str):
        self.service = service
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        settings = get_settings()
        self._breaker = CircuitBreaker(
            name=f"ml-{service}",
            failure_threshold=settings.circuit_failure_threshold,
            cooldown_seconds=settings.circuit_cooldown_seconds,
            max_cooldown_seconds=settings.circuit_max_cooldown_seconds,
        )

    async def _post(self, path: str, **kwargs) -> dict:
        """POST 请求 + 重试 + 熔断 + 指标 + 追踪。成功返回 JSON dict，失败抛 MLServiceUnavailableError。"""
        t0 = time.time()
        ok = False
        with _ml_span(self.service, path):
            async def do_request() -> dict:
                resp = await self._client.post(path, **kwargs)
                if resp.status_code >= 500:
                    # 服务预热中 / 内部错误：值得退避重试
                    raise _RetryableHTTPError(resp.status_code, resp.text)
                if resp.status_code >= 400:
                    raise MLServiceUnavailableError(
                        f"ML 服务 {self.service} 返回 {resp.status_code}",
                        detail={"path": path, "body": resp.text[:500]},
                    )
                return resp.json()

            try:
                result = await async_retry(
                    do_request,
                    retry_config=ml_retry, circuit_breaker=self._breaker,
                )
                ok = True
                return result
            except ModelCircuitOpenError:
                logger.warning(f"ML 熔断器 [ml-{self.service}] 开路，快速失败")
                raise MLServiceUnavailableError(
                    f"ML 服务 {self.service} 熔断中", detail={"path": path},
                )
            except MLServiceUnavailableError:
                raise
            except Exception as e:
                logger.warning(f"ML 服务 {self.service} 调用失败: {e}")
                raise MLServiceUnavailableError(
                    f"ML 服务 {self.service} 不可达: {e}", detail={"path": path},
                )
            finally:
                record_ml(self.service, ok, time.time() - t0)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        """探测服务健康状态（不触发重试逻辑，直接请求 /health）。"""
        try:
            resp = await self._client.get("/health")
            return {"ok": resp.status_code == 200, "body": resp.json() if resp.status_code == 200 else resp.text[:200]}
        except Exception as e:
            return {"ok": False, "body": str(e)}
