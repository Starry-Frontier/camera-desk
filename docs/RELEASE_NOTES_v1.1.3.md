# Camera Desk v1.1.3

Windows 10/11 x64 多机位摄像工具，应用代码使用 MIT 许可证。

- 修复全新检出源码时测试输出目录未初始化的问题，使 GitHub Windows 检查和发行构建能够从空环境开始。
- 提供 `CameraDesk-Standalone-Windows-x64.exe` 单文件版，内置 Python、Qt 与编码组件；复制后即可运行，无需安装 Python。
- 提供 `CameraDesk-Windows-x64.zip` 便携文件夹版，解压完整文件夹后运行 `CameraDesk.exe`，需保留 `_internal`。
- 保留独立／全部录像、录像中快捷拍照、共同保存目录、自定义命名、五种图片格式与三种视频格式，以及 `--demo --demo-count 8` 模拟演示。
- `CameraDesk-Source.zip` 提供完整源码、测试和构建脚本；`CameraDesk-ThirdParty-Sources.zip` 提供对应第三方源码，发行附件附 SHA-256 校验值。

自动发行流程先执行回归测试，再构建两种 Windows 程序并直接验证 8 路模拟录像、录像中拍照、多格式保存和随包依赖加载，最后生成发行附件。

8 路模拟验证不等同于 8 台实体 USB 摄像头的带宽和驱动验证。此前发现的重复补帧问题仍保留，不能保证每路无重复的 30 fps。
