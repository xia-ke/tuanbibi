"""Meituan public product-page probe: public metadata only, never checkout price.

Explicit host allowlist, robots checks, no redirects/cookies/browser or CAPTCHA bypass.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import json
import httpx

USER_AGENT = "TuanBiBiResearchBot/0.1 (+https://github.com/xia-ke/tuanbibi)"
ALLOWED_HOSTS = frozenset({"www.meituan.com", "i.meituan.com", "m.meituan.com",
                           "h5.meituan.com", "meishi.meituan.com"})
SAFE_QUERY_KEYS = frozenset({"id", "dealid", "productid", "skuid", "poiid", "itemid"})
MAX_ROBOTS_BYTES = 128 * 1024
MAX_HTML_BYTES = 512 * 1024


class PublicPageError(Exception):
    def __init__(self, reason: str, message: str, status: int = 422):
        super().__init__(message)
        self.reason, self.status = reason, status


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048 or not url.strip():
        raise PublicPageError("invalid_url", "商品链接不合法")
    try:
        p = urlsplit(url.strip())
        if (p.scheme != "https" or (p.hostname or "").lower() not in ALLOWED_HOSTS
                or p.username or p.password or p.port not in (None, 443)
                or not p.path.startswith("/")
                or any(ord(c) < 32 for c in url)):
            raise ValueError("unsupported")
    except ValueError:
        raise PublicPageError("unsupported_domain", "只允许已审核的美团 HTTPS 页面域名")
    return url.strip()


def safe_source_url(url: str) -> str:
    p = urlsplit(validate_url(url))
    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
            if k.lower() in SAFE_QUERY_KEYS and len(v) <= 96]
    return urlunsplit(("https", p.netloc.lower(), p.path, urlencode(kept), ""))


def read_limited(response: httpx.Response, limit: int) -> bytes:
    content = bytearray()
    for part in response.iter_bytes():
        content.extend(part)
        if len(content) > limit:
            raise PublicPageError("too_large", "响应内容超过大小限制", 413)
    return bytes(content)


def check_response(response: httpx.Response, description: str) -> None:
    if response.status_code in (401, 403, 429):
        raise PublicPageError("access_restricted", description + "拒绝访问", 403)
    if 300 <= response.status_code < 400:
        raise PublicPageError("redirect_blocked", description + "发生跳转，未继续访问")
    if response.status_code >= 400:
        raise PublicPageError("http_error", description + "返回 HTTP " + str(response.status_code), 502)


def check_robots(client: httpx.Client, url: str) -> None:
    p = urlsplit(url)
    address = "https://" + p.hostname + "/robots.txt"
    try:
        with client.stream("GET", address, headers={"User-Agent": USER_AGENT}) as res:
            if res.status_code in (404, 410):
                return
            check_response(res, "robots.txt")
            body = read_limited(res, MAX_ROBOTS_BYTES)
    except httpx.RequestError as exc:
        raise PublicPageError("robots_unavailable", "无法读取 robots 规则", 503) from exc
    if b"<html" in body[:512].lower():
        raise PublicPageError("robots_unavailable", "robots.txt 不是文本规则", 503)
    robots = RobotFileParser()
    robots.parse(body.decode("utf-8", errors="replace").splitlines())
    if not robots.can_fetch("TuanBiBiResearchBot", url):
        raise PublicPageError("robots_disallow", "robots 禁止采集此路径", 403)


class ProductMetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.jsonld = []
        self.in_jsonld = False
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = str(a.get("property") or a.get("name") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = str(a.get("content") or "").strip()
        if tag == "script" and str(a.get("type") or "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.fragments = []

    def handle_data(self, value):
        if self.in_jsonld:
            self.fragments.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self.in_jsonld = False
            self.jsonld.append("".join(self.fragments))


def products(value):
    if isinstance(value, list):
        for part in value:
            yield from products(part)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if any(str(t).lower() == "product" for t in types):
            yield value
        if "@graph" in value:
            yield from products(value["@graph"])


def yuan_to_fen(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        fen = Decimal(str(value)) * 100
        return int(fen) if fen.is_finite() and fen >= 0 and fen <= 10_000_000 and fen == fen.to_integral_value() else None
    except (InvalidOperation, ValueError):
        return None


def parse_public_html(html: str, url: str) -> dict:
    p = ProductMetaParser()
    p.feed(html)
    entry = None
    for snippet in p.jsonld:
        if len(snippet) > 100_000:
            continue
        try:
            candidates = list(products(json.loads(snippet)))
            if candidates:
                entry = candidates[0]
                break
        except (ValueError, RecursionError):
            continue
    title = brand = description = evidence = None
    fen = None
    if entry:
        title = entry.get("name")
        description = entry.get("description")
        seller = entry.get("brand") or entry.get("seller")
        brand = seller.get("name") if isinstance(seller, dict) else seller
        offers = entry.get("offers")
        offers = offers if isinstance(offers, list) else [offers]
        for offer in offers:
            if isinstance(offer, dict) and str(offer.get("priceCurrency") or "CNY").upper() == "CNY":
                fen = yuan_to_fen(offer.get("price"))
                if fen is not None:
                    evidence = "json_ld:Product.offers.price"
                    break
    elif p.meta.get("og:type", "").lower() == "product" or any(k.startswith("product:") for k in p.meta):
        title = p.meta.get("og:title") or p.meta.get("product:name")
        description = p.meta.get("og:description")
        brand = p.meta.get("product:brand")
        if str(p.meta.get("product:price:currency") or "CNY").upper() == "CNY":
            fen = yuan_to_fen(p.meta.get("product:price:amount"))
            evidence = "meta:product:price:amount" if fen is not None else None
    if not isinstance(title, str) or not title.strip():
        raise PublicPageError("no_product_metadata", "公开页面不包含可核实的商品结构化信息")
    return {
        "platform": "meituan",
        "source_type": "public_page",
        "source_url": safe_source_url(url),
        "product_name": title.strip()[:160],
        "brand_name": brand.strip()[:160] if isinstance(brand, str) else None,
        "description": description.strip()[:1500] if isinstance(description, str) else None,
        "display_price_cents": fen,
        "price_evidence": evidence,
        "price_status": "public_display_price" if fen is not None else "not_disclosed",
        "final_price_verified": False,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_public_product(url: str, client: httpx.Client | None = None) -> dict:
    url = validate_url(url)
    owns_client = client is None
    if owns_client:
        client = httpx.Client(follow_redirects=False, trust_env=False,
                              timeout=httpx.Timeout(7.0, connect=4.0))
    try:
        check_robots(client, url)
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as r:
            check_response(r, "商品页面")
            ctype = r.headers.get("content-type", "").lower()
            if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
                raise PublicPageError("not_html", "页面未返回 HTML 内容")
            raw = read_limited(r, MAX_HTML_BYTES)
            charset = r.encoding or "utf-8"
        return parse_public_html(raw.decode(charset, errors="replace"), url)
    except (httpx.RequestError, UnicodeError) as exc:
        raise PublicPageError("network_error", "公开商品页面不可访问", 502) from exc
    finally:
        if owns_client:
            client.close()
