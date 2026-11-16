"""
Standalone PyQt5 3D Truss Viewer Window
Launched as a subprocess from the chatbot. Reads an HTML file and displays it
in a QWebEngineView with full Plotly interactivity.

Usage: python truss_viewer_window.py <html_file_path> [title]
"""

import sys
import os


def main():
    from PyQt5.QtWidgets import QApplication, QMainWindow
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    from PyQt5.QtCore import QUrl

    html_path = sys.argv[1] if len(sys.argv) > 1 else None
    title = sys.argv[2] if len(sys.argv) > 2 else "3D Truss Viewer"
    if not html_path or not os.path.exists(html_path):
        print(f"Usage: {sys.argv[0]} <html_file_path> [title]")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName(title)

    win = QMainWindow()
    win.setWindowTitle(title)
    win.resize(900, 700)

    web = QWebEngineView()
    web.setUrl(QUrl.fromLocalFile(os.path.abspath(html_path)))
    win.setCentralWidget(web)
    win.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
