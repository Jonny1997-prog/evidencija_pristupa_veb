from app import app, init_db


def run_basic_tests() -> None:
    init_db()
    with app.test_client() as client:
        for path in [
            "/",
            "/posete/najava",
            "/posete/nenajavljena",
            "/posete/portirnica",
            "/kamioni/unos",
            "/kamioni/portirnica",
        ]:
            resp = client.get(path)
            assert resp.status_code == 200, f"GET {path} failed with {resp.status_code}"
    print("Svi osnovni testovi ruta su prošli.")


if __name__ == "__main__":
    run_basic_tests()
