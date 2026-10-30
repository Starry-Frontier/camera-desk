"""Smoke-test the shipped executable without a Python installation on PATH."""
import argparse
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np


def within(path, root):
    try:
        return os.path.commonpath([str(root), str(Path(path).resolve())]).lower() == str(root).lower()
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--formats', action='store_true')
    parser.add_argument('--channels', type=int, choices=range(1, 9), default=2)
    parser.add_argument('--platform', choices=['offscreen', 'windows'], default='offscreen')
    args = parser.parse_args()
    if args.formats and args.channels != 2:
        parser.error('The format scenario uses exactly two channels')
    exe, work = args.exe.resolve(), args.work_dir.resolve()
    if work.exists() and list(work.iterdir()):
        raise SystemExit('Use a new empty verification directory.')
    work.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(('PYTHON', 'QT_', '_PYI_', 'OPENCV_')) or key in ('VIRTUAL_ENV', '_MEIPASS2'):
            environment.pop(key, None)
    windows = environment.get('SystemRoot', 'C:\\Windows')
    environment['PATH'] = os.path.join(windows, 'System32') + os.pathsep + windows
    environment['QT_QPA_PLATFORM'] = args.platform
    mode = '--smoke-test-formats' if args.formats else '--smoke-test'
    result = subprocess.run([str(exe), mode, '--demo-count', str(args.channels), '--data-dir', str(work)],
                            env=environment, cwd=str(work), timeout=90)
    assert result.returncode == 0, 'The packaged app exited with an error'
    runtime = json.loads((work / 'packaged-runtime.json').read_text(encoding='utf-8'))
    assert runtime['frozen'] and runtime['channels'] == args.channels
    assert not runtime['errors'] and not runtime['unexpected_dialogs'], runtime
    resource = Path(runtime['resource_directory']).resolve()
    for module in runtime['module_paths'].values():
        assert within(module, resource), 'A module was imported outside the bundle'
    # msvcp_win.dll belongs to Windows itself; require the redistributable
    # MSVC 140 family, rather than matching every OS DLL beginning with msvcp.
    required_prefixes = ('python', 'pyside', 'shiboken', 'qt6', 'opencv', 'cv2', 'msvcp140', 'vcruntime140', 'concrt140', 'libopenblas')
    for module in runtime['loaded_windows_modules']:
        if Path(module).name.lower().startswith(required_prefixes):
            assert within(module, resource), 'A runtime library was loaded outside the bundle: ' + module
    for n in range(args.channels):
        events = [e for e in runtime['recording_events'] if e['channel'] == 'demo:%d' % n]
        assert [e['active'] for e in events] == [True, False], 'A recording stopped or restarted unexpectedly'
        assert events[0]['path'] == events[1]['path'] and events[0]['path'], 'The recording output changed'
    photo_suffixes = {'.jpg', '.bmp', '.tif', '.webp'} if args.formats else {'.png'}
    video_suffixes = {'.mp4', '.mkv'} if args.formats else {'.avi'}
    files = list((work / 'captures').iterdir())
    photos = [path for path in files if path.suffix in photo_suffixes]
    videos = [path for path in files if path.suffix in video_suffixes]
    assert len(photos) == (5 if args.formats else args.channels) and len(videos) == args.channels, 'Multi-camera output was not completed'
    assert {path.suffix for path in photos} == photo_suffixes
    assert {path.suffix for path in videos} == video_suffixes
    expected = (480, 640) if args.formats else (720, 1280)
    for photo in photos:
        image = cv2.imdecode(np.fromfile(str(photo), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image is not None and image.shape[:2] == expected
    recordings = []
    for path in videos:
        cap = cv2.VideoCapture(str(path))
        assert cap.isOpened(), 'The saved video cannot be opened'
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps, frames = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        decoded = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                assert frame.shape[:2] == expected
                decoded += 1
        finally:
            cap.release()
        assert decoded > 0 and abs(decoded - frames) <= 1
        assert (width, height) == (expected[1], expected[0]) and fps > 0 and 1.5 < frames / fps < 8
        recordings.append({'format': path.suffix, 'width': width, 'height': height, 'fps': fps, 'frames': frames, 'decoded_frames': decoded, 'seconds': frames / fps})
    report = {'executable': exe.name, 'photos': len(photos), 'recordings': recordings,
              'version': runtime['version'], 'channels': args.channels, 'qt_platform': args.platform,
              'core_modules_and_runtime_libraries_loaded_from_bundle': True, 'frozen': True,
              'clean_environment': True, 'unicode_data_directory': str(work), 'multi_format_recording_with_photos': args.formats}
    (work / 'verification-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
