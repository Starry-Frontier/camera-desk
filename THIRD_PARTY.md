# Third-party software notices

Camera Desk uses the following unmodified third-party components. Copyright and license notices included with their distributions are retained in `LICENSES`. The application source and build instructions are included in `source` in the portable release.

Camera Desk's own source code is released under the MIT License in `LICENSE`. That license does not replace or alter the licenses of the third-party components below.

| Component | Version | License / source |
| --- | --- | --- |
| CPython | Local initial build 3.8.6; GitHub build 3.11.x (see runtime-info.json in the binary release) | Python Software Foundation License; https://www.python.org/downloads/ |
| Qt for Python / PySide6 Essentials / Shiboken6 | 6.6.3.1 | LGPLv3 option; https://code.qt.io/cgit/pyside/pyside-setup.git/?id=5cad7f595f4cd7faa6e45a2ddf82f3bb14ab9140 |
| Qt libraries | 6.6.3 | LGPLv3 option for the Qt Core, GUI and Widgets modules used here; https://download.qt.io/archive/qt/6.6/6.6.3/submodules/ |
| OpenCV Python headless | 4.10.0.84 | Apache-2.0 / upstream third-party notices; https://github.com/opencv/opencv-python/tree/84 |
| OpenCV | 4.10.0 | Apache-2.0; https://github.com/opencv/opencv/tree/4.10.0 |
| NumPy | 1.24.4 | BSD-3-Clause and bundled dependency notices; https://github.com/numpy/numpy/tree/v1.24.4 |
| PyInstaller bootloader | 6.11.1 | GPL-2.0-or-later with bootloader exception; https://github.com/pyinstaller/pyinstaller/tree/v6.11.1 |
| FFmpeg video plugin (OpenCV Windows bundle) | FFmpeg n4.4.4 / OpenCV 4.10.0 | LGPL-2.1-or-later; corresponding upstream sources at https://github.com/opencv/opencv_3rdparty/tree/d35ed9aaa15fa2e638e68d22bfc980ea157d88bc/sources |

## Qt / LGPL notice and replacement

This application uses Qt and Qt for Python under the LGPLv3 open-source option. The Qt libraries are separate shared DLLs in `_internal\PySide6`. No changes have been made to Qt, PySide6 or Shiboken6. You may replace these libraries with compatible modified versions and run the application with those versions. Reverse engineering for debugging modifications to these libraries is permitted to the extent required by the LGPL. There is no signature or integrity lock preventing replacement.

Keep the original portable folder as a backup. Build compatible 64-bit Qt/PySide6/Shiboken6 libraries for the same Python ABI, then replace the relevant files under `_internal\PySide6` and `_internal\shiboken6`. Alternatively, rebuild the included application source using the modified Python packages. The application imports only the Qt Core, GUI and Widgets APIs. See the upstream build documentation: https://doc.qt.io/qtforpython-6/building_from_source/index.html .

The standalone EXE contains these same shared libraries, extracted to a temporary directory at launch. To use compatible modified libraries with the standalone layout, install the modified Python packages in the supplied source/build environment and run `build.ps1 -SingleFile -NoZip -DistDirectory build\modified-standalone`. All application code needed to rebuild the combined work is supplied under MIT, without signature checks or restrictions on this replacement. The folder edition is also provided for direct DLL replacement. License texts are embedded in the standalone EXE and supplied as accessible files with the portable and source archives.

The full LGPLv3 and GPLv3 license texts are included in `LICENSES`. For external redistribution, retain these notices, preserve the library replacement mechanism, and accompany the release with the corresponding source of the distributed LGPL libraries, or another source-distribution arrangement allowed by their licenses. Upstream source locations are listed above; a mere upstream link does not itself replace the distributor's source obligations. Official guidance: https://www.qt.io/development/open-source-lgpl-obligations .

OpenCV's `LICENSE-3RD-PARTY.txt` covers codecs and other bundled components. The videos written by Camera Desk use MJPEG or MPEG-4 without audio.

## Corresponding-source release attachment

`scripts/fetch-third-party-sources.ps1` creates `CameraDesk-ThirdParty-Sources.zip` from the fixed versions and SHA-256 values in `scripts/third-party-sources.json`. It contains Qt base, SVG and image-format module sources; the exact PySide/Shiboken fix-release source commit; and OpenCV's matching FFmpeg source archives, dependencies, wrapper and build scripts. Publish that attachment alongside the binary release. Its included `SOURCES.md` explains the version mapping and build/replacement references. Verify the module inventory again whenever the binary dependencies change.
