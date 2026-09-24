"""
Brute-force protection for POST /api/auth/login/.

Two limits, both applied only to the login view:
- per client IP  (stops one machine hammering the endpoint)
- per username   (stops many machines guessing one account's password)

Rates come from REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] ("login_ip",
"login_user"). Other endpoints are not throttled, so page polling
(/api/runs/logs/, the Sidebar badge) is unaffected.
"""

from rest_framework.throttling import SimpleRateThrottle


class LoginIPRateThrottle(SimpleRateThrottle):
    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LoginUsernameRateThrottle(SimpleRateThrottle):
    scope = "login_user"

    def get_cache_key(self, request, view):
        username = str(request.data.get("username") or "").strip().lower()

        if not username:
            # Nothing to key on; the IP limit still applies.
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": username,
        }