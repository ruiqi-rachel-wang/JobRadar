import concurrent.futures
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests as _real_requests
from bs4 import BeautifulSoup

from ..common import BASE_DIR, HEADERS, REQUESTS_VERIFY_SSL, http_session


class _SessionRequests:
    """Drop-in for the `requests` module that routes get/post through a
    per-thread Session, so repeated calls to the same host reuse the TLS
    connection instead of handshaking every time. Anything else
    (exceptions, codes, …) falls through to the real module."""

    @staticmethod
    def get(url, **kwargs):
        return http_session().get(url, **kwargs)

    @staticmethod
    def post(url, **kwargs):
        return http_session().post(url, **kwargs)

    @staticmethod
    def head(url, **kwargs):
        return http_session().head(url, **kwargs)

    def __getattr__(self, name):
        return getattr(_real_requests, name)


# Shadows the real module for every fetcher doing `from .base import *`.
requests = _SessionRequests()
from ..utils import (
    _parse_posted, _posted_from_json, extract_location_from_text,
    extract_posted_from_html, extract_posted_from_text, get_param_title,
    has_include, make_id, make_paged_url, norm, normalize_location,
)

# ATS modules intentionally use `from .base import *` to keep individual source
# files compact. Export underscore helpers explicitly; Python star imports skip
# them unless __all__ is defined.
__all__ = [name for name in globals() if not name.startswith("__")]
