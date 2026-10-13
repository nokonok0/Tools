from pathlib import Path
import subprocess
import sys


def launch():
    directory = Path(__file__).resolve().parent
    environment = directory / ".venv"
    interpreter = environment / "Scripts" / "pythonw.exe"
    if interpreter.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        subprocess.Popen([str(interpreter), str(directory / "CodexMeter.pyw")],
                         cwd=directory, creationflags=subprocess.CREATE_NO_WINDOW)
        return
    from meter_ui import main
    main()

if __name__ == "__main__":
    launch()
