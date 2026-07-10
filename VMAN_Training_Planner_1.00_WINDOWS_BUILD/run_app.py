from pathlib import Path
import multiprocessing
import traceback


def main():
    try:
        from vman_engine.gui import run_app
        run_app()
    except Exception:
        log_path = Path(__file__).resolve().parent / "vman_error_log.txt"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
