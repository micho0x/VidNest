"""Native Windows file pickers. Prints the chosen path (empty when cancelled).

    python picker.py            -> choose a folder
    python picker.py file       -> choose a cookies.txt file
"""
import sys
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    print("")
    sys.exit(0)


def downloads() -> str:
    path = Path.home() / "Downloads"
    return str(path) if path.is_dir() else str(Path.home())


def main() -> None:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "folder"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        if mode == "file":
            path = filedialog.askopenfilename(
                title="VidNest - pick the cookies.txt you exported from YouTube",
                initialdir=downloads(),
                filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")],
            )
        else:
            path = filedialog.askdirectory(
                title="VidNest - where should downloads go?",
                mustexist=False,
            )
    except Exception:
        path = ""
    finally:
        root.destroy()

    sys.stdout.write(path or "")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
