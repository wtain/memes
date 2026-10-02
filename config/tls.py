import logging
import ssl

logger = logging.getLogger(__name__)


def use_system_trust_store() -> None:
    """Make Python's ssl module (and so requests/urllib3/feedparser) verify against the OS
    trust store -- Windows CryptoAPI, macOS Security framework, OpenSSL's system CA paths on
    Linux -- instead of certifi's bundled CAs.

    Needed on workstations where antivirus/corporate proxies re-sign HTTPS traffic with a
    root that is installed in the OS store but absent from certifi (e.g. Norton Web Shield,
    which made trends_batch fail with CERTIFICATE_VERIFY_FAILED on 2026-10-02). Idempotent,
    and a no-op (falling back to certifi) if truststore isn't installed.
    """
    try:
        import truststore
    except ImportError:
        logger.warning("truststore not installed; using certifi CA bundle")
        return
    truststore.inject_into_ssl()
