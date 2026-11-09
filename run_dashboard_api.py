"""Run Khoa's API against separately started RPC services (not the complete UI)."""
import argparse
from dashboard_api import DashboardAPI, create_http_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    server = create_http_server(DashboardAPI(), args.port)
    print(f'Dashboard API: http://127.0.0.1:{server.server_port}/api/overview', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
