"""certs.py — OPC UA 证书生成（自签，S7-1500 互信流程同款）
============================================================
生成三件套（PEM/DER 双格式，Siemens 与 asyncua 均可用）：
  server_cert.der / server_key.pem   仿真服务器（或边缘自证）
  client_cert.der / client_key.pem   边缘采集器客户端证书

真实 S7-1500 接入时的证书互换（详见 README 手册）：
  1. 本脚本生成 client_cert.der → 在 TIA 证书管理器导入为"受信客户端"
  2. TIA 导出 PLC 服务器证书 → 放到 certs/ 目录供 OpcuaSource 信任

    python -m services.edge_collector.certs --out services/edge_collector/certs
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _make_selfsigned(cert_out: Path, key_out: Path, common_name: str, app_uri: str,
                     dns: str = "localhost", ip: str = "127.0.0.1", days: int = 3650) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([
            x509.UniformResourceIdentifier(app_uri),
            x509.DNSName(dns),
            x509.IPAddress(ipaddress.ip_address(ip)),
        ]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage(
            [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_out.parent.mkdir(parents=True, exist_ok=True)
    cert_out.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    key_out.parent.mkdir(parents=True, exist_ok=True)
    key_out.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))


def generate_certs(out_dir: str | Path,
                   server_uri: str = "urn:line03:opcua-sim-server",
                   client_uri: str = "urn:line03:edge-collector") -> dict[str, Path]:
    out = Path(out_dir)
    paths = {
        "server_cert": out / "server_cert.der",
        "server_key": out / "server_key.pem",
        "client_cert": out / "client_cert.der",
        "client_key": out / "client_key.pem",
    }
    _make_selfsigned(paths["server_cert"], paths["server_key"], "line03-opcua-sim", server_uri)
    _make_selfsigned(paths["client_cert"], paths["client_key"], "line03-edge-collector", client_uri)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 OPC UA 自签证书（服务器+客户端）")
    parser.add_argument("--out", default=str(Path(__file__).parent / "certs"))
    args = parser.parse_args()
    paths = generate_certs(args.out)
    for k, p in paths.items():
        print(f"{k}: {p}")


if __name__ == "__main__":
    main()
