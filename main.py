"""Portable Windows desktop entry point."""
import argparse
import logging
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description='Camera Desk 多机位摄像台')
    parser.add_argument('--demo', action='store_true', help='Open two simulated cameras')
    parser.add_argument('--demo-count', type=int, choices=range(1, 9), default=2,
                        help='Number of simulated cameras with --demo (1-8)')
    parser.add_argument('--data-dir', type=Path, help='Settings and default capture folder')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test-formats', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    data = args.data_dir or base / 'data'
    try:
        data.mkdir(parents=True, exist_ok=True)
        log_handler = logging.FileHandler(str(data / 'camera-desk.log'), encoding='utf-8')
    except OSError:
        data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CameraDesk'
        data.mkdir(parents=True, exist_ok=True)
        log_handler = logging.FileHandler(str(data / 'camera-desk.log'), encoding='utf-8')
    logging.basicConfig(handlers=[log_handler], level=logging.INFO)
    from PySide6.QtWidgets import QApplication, QMessageBox
    from multicam.ui import MainWindow
    app = QApplication(sys.argv[:1])
    # Windows' offscreen test platform does not enumerate system fonts.
    from PySide6.QtGui import QFontDatabase
    if not QFontDatabase.families() and os.name == 'nt':
        fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for name in ('segoeui.ttf', 'msyh.ttc'):
            QFontDatabase.addApplicationFont(str(fonts / name))
    app.setApplicationName('Camera Desk')
    app.setOrganizationName('CameraDesk')
    app.setStyle('Fusion')
    def exception_hook(kind, value, traceback):
        logging.error('Unhandled application error', exc_info=(kind, value, traceback))
        QMessageBox.critical(None, 'Camera Desk', '操作未能完成：%s\n详细信息已写入 camera-desk.log。' % value)
    sys.excepthook = exception_hook
    window = MainWindow(data, demo=args.demo or args.smoke_test or args.smoke_test_formats,
                        demo_count=args.demo_count)
    window.show()
    if args.smoke_test or args.smoke_test_formats:
        from multicam.smoke import schedule_smoke_test
        schedule_smoke_test(window, data, formats=args.smoke_test_formats, channels=args.demo_count)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
