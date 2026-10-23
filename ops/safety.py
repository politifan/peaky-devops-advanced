"""Small stdlib-only checks that remain active with Python optimization."""
import urllib.request


def require(condition, message='Required check failed'):
    if not condition:
        raise RuntimeError(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
