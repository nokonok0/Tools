from pathlib import Path
import os
import subprocess
import sys


def launch():
    directory = Path(__file__).resolve().parent
    if os.name != "nt":
        # LinuxはWindowsと別の仮想環境を使い、同じフォルダでも取り違えない。
        environment = directory / ".venv-linux"
        interpreter = environment / "bin" / "python"
        if interpreter.is_file() and Path(sys.prefix).resolve() != environment.resolve():
            os.execv(str(interpreter), [str(interpreter), str(directory / "CodexMeter.pyw")])
            return
    else:
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
