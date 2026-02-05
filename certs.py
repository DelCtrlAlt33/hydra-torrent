import os
import ssl
import datetime

from config import CERTS_DIR, FULLCHAIN_PATH, PRIVKEY_PATH, CHAIN_PATH, logger


def ensure_certificates():
    """Auto-generate a self-signed RSA 2048 certificate if none exists."""
    if os.path.exists(FULLCHAIN_PATH) and os.path.exists(PRIVKEY_PATH):
        return  # certs already present

    os.makedirs(CERTS_DIR, exist_ok=True)
    logger.info("No TLS certificates found — generating self-signed cert …")

    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Hydra Torrent"),
    ])

    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # Write private key
    with open(PRIVKEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Write certificate (fullchain = cert itself for self-signed)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    with open(FULLCHAIN_PATH, "wb") as f:
        f.write(cert_pem)

    # Write chain.pem (same as fullchain for self-signed)
    with open(CHAIN_PATH, "wb") as f:
        f.write(cert_pem)

    logger.info(f"Self-signed certificate written to {CERTS_DIR}")


def create_server_ssl_context():
    """Create a TLS context for serving connections (peer server / index server)."""
    ensure_certificates()
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certfile=FULLCHAIN_PATH, keyfile=PRIVKEY_PATH)
    return ctx


def create_client_ssl_context(server_hostname=None):
    """Create a TLS context for outgoing connections (cert verification disabled)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
