# Camera Desk v1.0.0

面向 Windows 10 / 11 64 位的多机位摄像工具，应用源码采用 MIT 许可证。

## 下载与启动

普通使用者下载 `CameraDesk-Windows-x64.zip`，解压整个 `CameraDesk` 文件夹后双击 `CameraDesk.exe`，无需安装 Python。请保留 `_internal` 文件夹。

开发者可下载 `CameraDesk-Source.zip` 或 GitHub 提供的源码归档。`CameraDesk-ThirdParty-Sources.zip` 提供发行程序使用的 Qt、PySide/Shiboken 及 FFmpeg 对应源码、构建资料和来源清单。文件校验值见 `SHA256SUMS.txt`。

## 本版功能

- 两个及更多 USB/UVC 摄像头或兼容采集卡的独立预览。
- 每机位分辨率和目标帧率设置，显示实际采集状态。
- 亮度、对比度、饱和度、锐度、镜像及垂直翻转。
- 逐机位／全部拍照、录制；输出 PNG 与无声 MJPEG AVI。
- 设备支持时的曝光、自动对焦、白平衡等参数控制。
- 无需硬件的多机位模拟演示。

## 验证与限制

模拟多机位测试覆盖参数独立性、照片输出、录制时长、格式切换和退出保存；发行流程会测试打包后的程序。真实设备、USB 带宽和其他电脑的兼容性仍需实机确认。全部录制统一发出指令，不提供硬件帧同步。本版不录制音频。

应用采用 MIT 许可证，见发行包的 `LICENSE`。第三方组件遵循各自许可证，详见 `THIRD_PARTY.md` 与 `LICENSES`。

