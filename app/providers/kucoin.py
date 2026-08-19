"""KuCoin specifics.

Three things differ enough from the generic ccxt path to be worth a subclass:

  1. KuCoin keys carry a passphrase in addition to key and secret. A missing
     passphrase fails with a signature error that reads like a wrong secret,
     which sends people down the wrong path for an hour.
  2. KuCoin blocks US egress. It answers 403 with a body that varies, so the
     detection lives here alongside a remedy that names the actual fix.
  3. KuCoin rejects requests whose timestamp is more than a few seconds off its
     own clock, so skew is a first-class check rather than a curiosity.
"""

from __future__ import annotations

import logging
from typing import Any

from app.providers.base import (
    ConnectivityReport,
    Credentials,
    ProviderAuthError,
    ProviderError,
    ProviderGeoBlockError,
)
from app.providers.ccxt_provider import CcxtProvider

log = logging.getLogger(__name__)

# KuCoin rejects a request signed more than this far from its own clock.
CLOCK_SKEW_LIMIT_SECONDS = 5.0

GEO_REMEDY = (
    "KuCoin blocks requests from US IP addresses. Render defaults to Oregon (US), "
    "which is why this fails there and works on a non-US host. Deploy the service "
    "in a non-US region -- singapore or frankfurt -- and note that a Render "
    "service's region is fixed at creation, so this means creating a new service "
    "rather than editing the existing one."
)


class KuCoinProvider(CcxtProvider):
    name = "kucoin"

    def __init__(self, credentials: Credentials | None = None, *, sandbox: bool = False) -> None:
        super().__init__("kucoin", credentials, sandbox=sandbox)

    def verify_credentials(self) -> dict[str, Any]:
        # Check this before the network call so the error names the real problem.
        if self.credentials.key and not self.credentials.password:
            raise ProviderAuthError(
                "KuCoin API keys need a passphrase as well as a key and secret. "
                "Set the passphrase env var (FREQTRADE__EXCHANGE__PASSWORD by default); "
                "without it KuCoin returns a signature error that looks like a bad secret."
            )
        return super().verify_credentials()

    #: KuCoin's "Get API Key Info" endpoint, which reports what the key may do.
    #: user_info is a different endpoint -- the account summary -- and carries no
    #: permission field at all, so reading permissions from it silently produced
    #: an empty list that looked like a read-only key.
    _KEY_INFO_METHODS = ("private_get_user_api_key", "private_get_user_info")

    def permissions(self) -> set[str]:
        """Read the permissions KuCoin actually granted the key.

        KuCoin reports the permission list directly, so unlike the generic
        provider this does not have to infer from what succeeds -- which matters,
        because inference cannot detect withdrawal rights without attempting a
        withdrawal.

        If no endpoint yields a permission list, this raises rather than
        returning what it managed to infer. The check that reads this exists to
        catch a key that can withdraw, and "I could not tell" reported as "read
        only" is exactly the false all-clear it is meant to prevent.
        """
        found: set[str] = set()
        tried: list[str] = []

        for method_name in self._KEY_INFO_METHODS:
            method = getattr(self.exchange, method_name, None)
            if method is None:
                continue
            tried.append(method_name)
            try:
                raw = method()
            except Exception as exc:
                log.info("kucoin %s unavailable: %s", method_name, exc)
                continue

            data = raw.get("data") if isinstance(raw, dict) else None
            entries = data if isinstance(data, list) else [data] if data else []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # The field has been called both 'permission' and 'permissions'.
                declared = entry.get("permission") or entry.get("permissions") or ""
                if isinstance(declared, list):
                    declared = ",".join(str(item) for item in declared)
                for permission in str(declared).split(","):
                    cleaned = permission.strip().lower()
                    if cleaned:
                        found.add(cleaned)
            if found:
                return found

        raise ProviderError(
            "KuCoin did not report what this key is allowed to do"
            + (f" (tried {', '.join(tried)})" if tried else "")
            + ". Check the key on KuCoin directly."
        )

    def check_connectivity(self) -> ConnectivityReport:
        report = super().check_connectivity()
        if report.geo_blocked and not report.detail:
            return ConnectivityReport(
                reachable=report.reachable,
                geo_blocked=True,
                latency_ms=report.latency_ms,
                server_time_skew_seconds=report.server_time_skew_seconds,
                egress_ip=report.egress_ip,
                egress_country=report.egress_country,
                detail=GEO_REMEDY,
            )
        return report

    def _translate(self, exc: Exception) -> Exception:
        translated = super()._translate(exc)
        if isinstance(translated, ProviderGeoBlockError):
            translated.detail = f"{translated.detail or ''}\n{GEO_REMEDY}".strip()
        return translated
