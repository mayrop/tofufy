from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


SUPPORTED_SETTINGS_FILES = {
    "caching": "cloudflare-caching.tf",
    "network": "cloudflare-network.tf",
    "security": "cloudflare-security.tf",
    "ssl_tls": "cloudflare-ssl-tls.tf",
    "speed": "cloudflare-speed.tf",
    "scrape_shield": "cloudflare-scrape-shield.tf",
}

SECTION_FILE_HEADERS = {
    "caching": ["Caching > Configuration"],
    "network": ["Network"],
    "security": ["Security > Settings"],
    "ssl_tls": ["SSL/TSL > Overview"],
    "speed": ["Speed > Settings"],
    "scrape_shield": ["Scrape Shield"],
}

SECTION_SETTING_COMMENTS = {
    "caching": {
        "cache_level": [
            "Caching Level",
            "Determine how much of your website's static content you",
            "want Cloudflare to cache. Increased caching can speed up page load time.",
        ],
        "browser_cache_ttl": [
            "Browser Cache TTL",
            "Determine the length of time Cloudflare instructs a visitor's browser",
            "to cache files. During this period, the browser loads the files from",
            "its local cache, speeding up page loads.",
        ],
        "always_online": [
            "Always Online",
            "Keep your website online for visitors when your origin server",
            "is unavailable. Cloudflare serves limited copies of web pages",
            "available from the Internet Archive's Wayback Machine.",
        ],
        "development_mode": [
            "Development Mode",
            "Temporarily bypass our cache allowing you to see changes",
            "to your origin server in realtime.",
            "Note: Enabling this feature can significantly increase",
            "origin server load. Development Mode does not purge the cache",
            "so files will need to be purged after development mode expires.",
        ],
        "sort_query_string_for_cache": [
            "Enable Query String Sort",
            "Cloudflare will treat files with the same query strings as",
            "the same file in cache, regardless of the order of the query strings.",
        ],
    },
    "network": {
        "ipv6": ["IPv6 Compatibility", "Enable IPv6 support and gateway."],
        "websockets": [
            "WebSockets",
            "Allow WebSockets connections to your origin server.",
            "Concurrent connection guidelines for your plan: custom.",
        ],
        "pseudo_ipv4": [
            "Pseudo IPv4",
            "Adds an IPv4 header to requests when a client is using IPv6,",
            "but the server only supports IPv4.",
        ],
        "ip_geolocation": [
            "IP Geolocation",
            "Include the country code of the visitor location with all requests to your website.",
            "Note: You must retrieve the IP Geolocation information from the CF-IPCountry HTTP header.",
        ],
        "max_upload": ["Maximum Upload Size", "The amount of data visitors can upload to your website in a single request."],
        "response_buffering": ["Response Buffering", "Enable or disable buffering of responses from the origin server."],
        "true_client_ip_header": ["True Client IP Header", "Enable or disable the True Client IP header."],
        "opportunistic_onion": [
            "Opportunistic Onion",
            "Onion Routing allows routing traffic from legitimate users on the",
            "Tor network through Cloudflare's onion services rather than exit nodes,",
            "thereby improving privacy of the users and enabling more",
            "fine-grained protection.",
        ],
    },
    "security": {
        "browser_check": [
            "Browser integrity check",
            "Evaluate HTTP headers from your visitor's browser for threats.",
            "If a threat is found a block page will be delivered.",
        ],
        "challenge_ttl": [
            "Challenge passage",
            "Specify the length of time that a visitor, who has successfully completed",
            "a Challenge (Javascript Challenge, Interactive Challenge or Managed Challenge),",
            "can access your website.",
            "When the configured timeout expires, the visitor will be issued a new challenge.",
            "Challenge Passage does not apply to Rate Limiting.",
        ],
        "replace_insecure_js": [
            "Replace insecure JavaScript libraries",
            "Automatically replace insecure JavaScript libraries with safer and faster",
            "alternatives provided under cdnjs and powered by Cloudflare.",
            "Currently supports the following libraries: Polyfill.",
        ],
        "security_level": [
            "Security level",
            "Cloudflare's security automatically protects your domain from malicious traffic.",
            "The security level is now fully automated and is set to 'always protected' by default.",
        ],
    },
    "ssl_tls": {
        "ssl": ["SSL/TLS encryption", "Current encryption mode: Full"],
        "always_use_https": [
            "Always Use HTTPS",
            "Redirect all requests with scheme \"http\" to \"https\".",
            "This applies to all http requests to the zone.",
        ],
        "min_tls_version": [
            "Minimum TLS Version",
            "Only allow HTTPS connections from visitors that support",
            "the selected TLS protocol version or newer.",
        ],
        "opportunistic_encryption": [
            "Opportunistic Encryption",
            "Opportunistic Encryption allows browsers to benefit from the",
            "improved performance of HTTP/2 by letting them know that your site",
            "is available over an encrypted connection. Browsers will continue",
            "to show \"http\" in the address bar, not \"https\".",
        ],
        "tls_1_3": [
            "TLS 1.3",
            "Enable the latest version of the TLS protocol for improved",
            "security and performance.",
        ],
        "automatic_https_rewrites": [
            "Automatic HTTPS Rewrites",
            "Automatic HTTPS Rewrites helps fix mixed content by changing",
            "\"http\" to \"https\" for all resources or links on your web site",
            "that can be served with HTTPS.",
        ],
        "ech": [
            "Encrypted Client Hello",
            "Encrypted Client Hello (ECH) enhances the privacy of visitors",
            "to your website by encrypting the entire ClientHello message",
            "during the TLS handshake, including the Server Name Indication (SNI).",
        ],
        "tls_client_auth": [
            "Authenticated Origin Pulls",
            "TLS client certificate presented for authentication on origin pull.",
            "Configure expiration notification for your certificates here.",
        ],
    },
    "speed": {
        "image_resizing": [
            "Image Transformations",
            "You can resize, adjust quality, and convert images to WebP format,",
            "on demand. We cache every derived image at the edge, so you store",
            "only the original image.",
        ],
        "polish": [
            "Polish",
            "Improve image load time by optimizing images hosted on your domain.",
            "Optionally, the WebP image codec can be used with supported clients",
            "for additional performance benefits.",
        ],
        "speed_brain": [
            "Speed Brain",
            "Speed Brain speeds up page load times by leveraging the",
            "Speculation Rules API. This instructs browsers to make",
            "speculative prefetch requests as a way to speed up next page",
            "navigation loading time.",
        ],
        "early_hints": [
            "Early Hints",
            "Cloudflare's edge will cache and send 103 Early Hints responses",
            "with Link headers from your HTML pages. Early Hints allows",
            "browsers to preload linked assets before they see a 200 OK",
            "or other final response from the origin.",
        ],
        "rocket_loader": ["Rocket Loader™", "Improve the paint time for pages which include JavaScript."],
        "http2": ["HTTP/2", "Accelerates your website with HTTP/2"],
        "origin_h2_max_streams": [
            "HTTP/2 to Origin",
            "Enable HTTP/2 requests between Cloudflare's edge and your origin.",
            "With Cloudflare's multiplexing capability, further enhance performance",
            "by optimizing requests to your origin server",
        ],
        "http3": [
            "HTTP/3 (with QUIC)",
            "Accelerates HTTP requests by using QUIC, which provides encryption",
            "and performance improvements compared to TCP and TLS.",
        ],
        "h2_prioritization": [
            "Enhanced HTTP/2 Prioritization",
            "Optimizes the order of resource delivery, independent of the browser.",
            "Greatest improvements will be experienced by visitors using",
            "Safari and Edge browsers.",
        ],
        "0rtt": [
            "0-RTT Connection Resumption",
            "Improves performance for clients who have previously connected",
            "to your website.",
        ],
    },
    "scrape_shield": {
        "email_obfuscation": [
            "Email Address Obfuscation",
            "Display obfuscated email addresses on your website to",
            "prevent harvesting by bots and spammers, without visible",
            "changes to the address for human visitors.",
        ],
        "hotlink_protection": ["Hotlink Protection", "Protect your images from off-site linking."],
    },
}

SECTION_SETTING_IDS = {
    "caching": [
        "cache_level",
        "browser_cache_ttl",
        "always_online",
        "development_mode",
        "sort_query_string_for_cache",
    ],
    "network": [
        "ipv6",
        "websockets",
        "pseudo_ipv4",
        "ip_geolocation",
        "max_upload",
        "response_buffering",
        "true_client_ip_header",
        "opportunistic_onion",
    ],
    "security": [
        "browser_check",
        "challenge_ttl",
        "replace_insecure_js",
        "security_level",
    ],
    "ssl_tls": [
        "ssl",
        "always_use_https",
        "min_tls_version",
        "opportunistic_encryption",
        "tls_1_3",
        "automatic_https_rewrites",
        "ech",
        "tls_client_auth",
    ],
    "speed": [
        "image_resizing",
        "polish",
        "speed_brain",
        "early_hints",
        "rocket_loader",
        "http2",
        "origin_h2_max_streams",
        "http3",
        "h2_prioritization",
        "0rtt",
    ],
    "scrape_shield": [
        "email_obfuscation",
        "hotlink_protection",
    ],
}

PRESERVE_LOCAL_SETTING_IDS = {
    "ssl_tls": {
        "tls_client_auth",
    },
}

MANAGED_TRANSFORM_DESCRIPTIONS = {
    "request": {
        "add_bot_protection_headers": "Adds HTTP request headers with bot-related values: bot score, verified bot, threat score, JA3 and JA4 fingerprints.",
        "add_client_certificate_headers": "Adds HTTP request headers with Mutual TLS (mTLS) client authentication values.",
        "add_visitor_location_headers": "Adds HTTP request headers with location information for the visitor's IP address, including city, country, continent, longitude, and latitude.",
        "add_true_client_ip_headers": "Adds a \"True-Client-IP\" request header with the visitor's IP address. Unavailable when \"Remove visitor IP headers\" is enabled.",
        "remove_visitor_ip_headers": "Removes HTTP request headers that may contain the visitor's IP address. Unavailable when \"Add True-Client-IP header\" is enabled.",
        "add_waf_credential_check_status_header": "Adds a header 'Exposed-Credential-Check' that provides values on fields which have been found to be leaked.",
    },
    "response": {
        "remove_x-powered-by_header": "Removes the \"X-Powered-By\" HTTP response header that provides information about the application at the origin server that handled the request.",
        "add_security_headers": "Adds several security-related HTTP response headers providing cross-site scripting (XSS) protection.",
    },
}

RULESET_PHASE_FILENAMES = {
    "http_request_cache_settings": "config-ruleset-http-request-cache-settings.tf",
    "http_request_firewall_custom": "config-ruleset-http-request-firewall-custom.tf",
    "http_ratelimit": "config-ruleset-http-ratelimit.tf",
}

RULESET_PHASE_COMMENTS = {
    "http_request_cache_settings": "Caching > Configuration > Cache Rules",
    "http_request_firewall_custom": "Security > WAF > Custom Rules",
    "http_ratelimit": "Security > WAF > Rate Limiting Rules",
}

RULESET_PHASE_RESOURCE_NAMES = {
    "http_request_cache_settings": "caching",
    "http_request_firewall_custom": "security",
    "http_ratelimit": "rate_limit",
}



@dataclass(frozen=True)
class CloudflareDNSRecord:
    key: str
    name: str
    record_type: str
    content: str
    proxied: bool = True
    ttl: int = 1
    tags: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=lambda: {"flatten_cname": False})
    record_id: str = ""


@dataclass(frozen=True)
class CloudflareZoneSetting:
    section: str
    setting_id: str
    value: Any


@dataclass(frozen=True)
class CloudflareListDefinition:
    local_name: str
    resource_name: str
    internal_type: str
    kind: str
    description: str
    items: List[Dict[str, Any]]
    list_id: str = ""


@dataclass(frozen=True)
class CloudflareRulesetDefinition:
    phase: str
    ruleset_id: str
    name: str
    kind: str
    description: str
    ruleset: Dict[str, Any]


def sanitize_dns_key(name: str, record_type: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower()).strip("-")
    if not base:
        base = "root"
    suffix = record_type.strip().lower()
    return base if suffix in {"a", "cname"} else f"{base}-{suffix}"


def sanitize_identifier(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip().lower()).strip("_")
    if not text:
        text = "item"
    if text[0].isdigit():
        text = f"_{text}"
    return text


def compute_relative_hostname(name: str, zone_name: str) -> str:
    normalized_name = (name or "").rstrip(".").lower()
    normalized_zone = (zone_name or "").rstrip(".").lower()
    if not normalized_name or normalized_name == normalized_zone:
        return ""
    if normalized_zone and normalized_name.endswith(f".{normalized_zone}"):
        return normalized_name[: -(len(normalized_zone) + 1)]
    return normalized_name


def finalize_dns_key(relative_name: str, record_type: str, occurrence: int) -> str:
    base_name = relative_name or "root"
    base_name = base_name.replace("*", "star")
    key = sanitize_dns_key(base_name, record_type)
    if relative_name.startswith("_") or (key and key[0].isdigit()):
        key = f"record-{key}"
    if occurrence > 1:
        key = f"{key}-{occurrence:02d}"
    return key


def group_zone_settings(setting_values: Dict[str, Any], sections: Iterable[str] | None = None) -> Dict[str, List[CloudflareZoneSetting]]:
    selected_sections = list(sections) if sections else list(SUPPORTED_SETTINGS_FILES)
    grouped: Dict[str, List[CloudflareZoneSetting]] = {}
    for section in selected_sections:
        ids = SECTION_SETTING_IDS.get(section, [])
        values = [
            CloudflareZoneSetting(section=section, setting_id=setting_id, value=setting_values[setting_id])
            for setting_id in ids
            if setting_id in setting_values
        ]
        grouped[section] = values
    return grouped


def infer_list_internal_type(name: str) -> str:
    lowered = (name or "").lower()
    if lowered.startswith("blacklist"):
        return "blacklist"
    return "whitelist"


def normalize_list_items(kind: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items:
        entry: Dict[str, Any] = {}
        comment = item.get("comment")
        if kind == "ip" and item.get("ip") is not None:
            entry["ip"] = str(item.get("ip"))
        elif kind == "asn" and item.get("asn") is not None:
            entry["asn"] = int(item.get("asn"))
        elif kind == "hostname":
            hostname = item.get("hostname")
            if isinstance(hostname, dict):
                value = hostname.get("url_hostname")
            else:
                value = hostname
            if value is not None:
                entry["hostname"] = str(value)
        else:
            continue
        if comment:
            entry["comment"] = str(comment)
        normalized.append(entry)
    return normalized
