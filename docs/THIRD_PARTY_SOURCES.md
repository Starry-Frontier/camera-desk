# Corresponding sources for Camera Desk Windows releases

This attachment accompanies Camera Desk's Windows binary release. It contains unmodified upstream source archives and their build inputs for the LGPL libraries distributed with the program. Application source is available separately in `CameraDesk-Source.zip` and in the portable package's `source` directory.

The machine-readable manifest records exact URLs, versions and SHA-256 checksums. Source downloads were checked on 2026-10-30. Qt archive hashes are published in Qt's official `.mirrorlist` metadata. The PySide archive was downloaded from Qt's official GitHub mirror at the immutable commit below. OpenCV FFmpeg archive contents were additionally checked against their official Git blob hashes.

## Qt 6.6.3

- `qtbase`: Qt Core, Gui, Network, Widgets, platform and basic image plugins.
- `qtsvg`: Qt SVG library and SVG image plugin.
- `qtimageformats`: the additional image plugins included by the PyInstaller Qt hook.

These match the Qt 6.6.3 DLLs and image-format plugins in the release. Build instructions and licenses are included in the archives. See [Building Qt for Windows](https://doc.qt.io/qt-6/windows-building.html). The application dynamically loads these libraries, and does not prevent replacing them with compatible modified versions.

## PySide6 and Shiboken6 6.6.3.1

The fix release has branch `6.6.3.1`, rather than a `v6.6.3.1` tag. Its frozen upstream commit is `5cad7f595f4cd7faa6e45a2ddf82f3bb14ab9140`, confirmed from [Qt's source repository](https://code.qt.io/cgit/pyside/pyside-setup.git/?id=5cad7f595f4cd7faa6e45a2ddf82f3bb14ab9140). The archive is obtained from the official `qtproject/pyside-pyside-setup` GitHub mirror at that commit, and includes both PySide and Shiboken sources, generator sources and build scripts. See [Qt for Python build instructions](https://doc.qt.io/qtforpython-6/building_from_source/index.html).

For a modified-library build, use Windows x64 with the same Python ABI as the selected application release. Replace the corresponding shared libraries under `_internal/PySide6` and `_internal/shiboken6`, or rebuild Camera Desk with the modified Python packages. Reverse engineering required to debug modifications to these libraries is permitted by the LGPL.

## OpenCV's FFmpeg Windows plugin

The distributed `opencv_videoio_ffmpeg4100_64.dll` comes from OpenCV's binary source revision `394dca6ceb3085c979415e6385996b6570e94153`, selected by [OpenCV 4.10.0's ffmpeg.cmake](https://github.com/opencv/opencv/blob/4.10.0/3rdparty/ffmpeg/ffmpeg.cmake). The expected upstream DLL MD5 is `bfef029900f788480a363d6dc05c4f0e`.

The corresponding `ffmpeg/4.x_20240522_src` branch is fixed at source commit `d35ed9aaa15fa2e638e68d22bfc980ea157d88bc`. Its six official source archives are preserved together under `opencv-ffmpeg/`:

- FFmpeg `n4.4.4`.
- libvpx `v1.14.0` and AOM `v3.9.0`.
- OpenH264 `v1.8.0` public API headers used by the wrapper build.
- The OpenCV FFmpeg interface code.
- The exact wrapper build scripts and source-download/archive scripts.

See the included `opencv_ffmpeg.tar.xz` for the build recipe. The wrapper is a separate DLL and can be rebuilt and replaced with a compatible modified version. No integrity lock prevents this. These archives include their own licenses; retain them when redistributing sources or rebuilt binaries.

## Maintaining this attachment

Keep this source archive accessible alongside every matching binary release. If a dependency version or packaged Qt module changes, update `scripts/third-party-sources.json` and this inventory before generating a new release. A default package rebuild uses the local Python runtime, so record the runtime version and retain its supplied license in the binary's runtime manifest and notices.
