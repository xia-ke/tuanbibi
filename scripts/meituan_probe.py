"""Local one-URL public Meituan probe: python -m scripts.meituan_probe HTTPS_URL."""
import argparse
import json
import sys

from app.meituan_public import PublicPageError, fetch_public_product


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.meituan_probe",
        description="检查单个公开美团商品页；不登录、不绕过平台访问限制。",
    )
    parser.add_argument("url", help="已知的美团商品详情 HTTPS 地址（不是搜索页）")
    args = parser.parse_args(argv)
    try:
        result = fetch_public_product(args.url)
    except PublicPageError as exc:
        print(json.dumps({
            "status": "unavailable",
            "reason": exc.reason,
            "message": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
