"""
Entry point for the SCP GUI application.
Run with:  python3 -m scpgui.main
"""
import sys
from PyQt5.QtWidgets import QApplication

from scpgui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SCP GUI")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
