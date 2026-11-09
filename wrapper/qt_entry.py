"""PyInstaller entry point for the bundled Qt frontend."""
import argparse
import sys

from wrapper.core import InstallError, portable_root
from wrapper.qt_ui import run


def main():
    parser = argparse.ArgumentParser(description="Savage Wind portable GUI")
    parser.add_argument("--root")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--tls-selftest", action="store_true",
                        help="Verificar o pacote de certificados TLS sem acessar a rede")
    parser.add_argument("--config", action="store_true", help="Abrir o instalador em vez de iniciar direto")
    args = parser.parse_args()
    if args.tls_selftest:
        from wrapper.core import tls_context
        try:
            count = len(tls_context().get_ca_certs())
        except (OSError, InstallError) as error:
            print(f"Falha nos certificados HTTPS: {error}", file=sys.stderr)
            return 1
        if count < 50:
            print(f"Certificados HTTPS insuficientes ({count} raízes confiáveis).", file=sys.stderr)
            return 1
        print(f"Pacote HTTPS válido: {count} certificados confiáveis.")
        return 0
    try:
        root = portable_root(explicit=args.root)
    except InstallError as error:
        print(error, file=sys.stderr)
        return 2
    return run(root, selftest=args.selftest, autoplay=not args.config)


if __name__ == "__main__":
    sys.exit(main())
