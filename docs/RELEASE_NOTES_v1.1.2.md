# Camera Desk v1.1.2

提供可直接复制到其他 Windows 电脑的单文件 EXE，同时保留便携文件夹版。

- `CameraDesk-Standalone-Windows-x64.exe`：单文件启动，内置 Python、Qt、OpenCV 和编码组件，无需安装 Python；首次启动会先解包运行组件。
- `CameraDesk-Windows-x64.zip`：解压后运行 `CameraDesk/CameraDesk.exe`，需保留整个文件夹及 `_internal`。
- 保留独立／全部录制、录像中直接拍照、共同保存目录、命名、五种图片格式和三种视频格式等功能。
- `--demo --demo-count 8` 可在未接摄像头时打开 8 路模拟源。
- 应用源码、测试、构建脚本及第三方对应源码继续以独立发行附件提供。

目标系统为 Windows 10/11 x64。25 项回归测试通过；两种打包程序均通过 8 路录像及录像中拍照、多格式输出检查。验证时移除了 Python／Qt 环境配置，PATH 仅保留 Windows 系统目录，并核对实际加载的核心组件来自随包运行目录；单文件版另通过 Windows 原生界面启动验证。

模拟测试不代表 8 台实体 USB 摄像头的带宽或其他电脑性能。之前测得的重复补帧问题尚未修复，不能保证每路无重复的 30 fps；本次主要整合可执行文件与验证便携运行。
