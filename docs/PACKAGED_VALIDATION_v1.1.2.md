# v1.1.2 打包运行记录

日期：2026-10-30。本记录验证实际生成的 EXE，不依赖源码方式运行应用。

| 程序 | Qt 界面 | 模拟通道 | 视频 | 录像中照片 | 结果 |
|---|---|---:|---|---|---|
| 便携文件夹 EXE | 离屏 | 8 | 8 段 1280×720 AVI，约 3.5 秒 | 8 张 PNG | 通过 |
| 便携文件夹 EXE | 离屏 | 2 | MP4、MKV，640×480 / 20 fps，各约 3 秒 | JPEG、BMP、TIFF、WebP 共 5 张 | 通过 |
| 单独复制的单文件 EXE | 离屏 | 8 | 8 段 1280×720 AVI，约 3.23 秒 | 8 张 PNG | 通过 |
| 单独复制的单文件 EXE | Windows 原生 | 2 | MP4、MKV，640×480 / 20 fps，各约 2.8 秒 | JPEG、BMP、TIFF、WebP 共 5 张 | 通过 |

共 20 个视频、26 张照片。视频全部从头到尾逐帧解码，尺寸、帧数、时长正确；各路仅开始和停止一次，原文件路径保持一致。照片尺寸与格式正确，快捷保存不弹窗口，工作线程未报告错误。另有 25 项源码回归测试通过。

启动环境删除 `PYTHON*`、`QT_*`、`OPENCV_*`、虚拟环境及打包器的继承配置，PATH 仅含 Windows 系统目录；工作目录采用独立的中文路径。单文件 EXE 先复制到一个仅含该 EXE 的中文目录，与源码、DLL 文件夹分开。

打包程序在退出前记录核心 Python 模块路径及实际加载的 Windows DLL。验证 Python、Qt／PySide／Shiboken、MSVC 140、OpenCV／FFmpeg、OpenBLAS 等组件来自包内目录或单文件 EXE 本次解包目录；Windows 自带系统 DLL 正常从系统目录加载。Windows 原生界面测试确认 qwindows 插件可用。

另检查单文件为 x64 GUI PE，权限清单为 asInvoker，无需管理员权限；内含 Python 运行库、Qt Widgets、Windows 界面插件、视频插件及第三方许可证。单文件约 83.5 MiB，首次启动会自动解包组件。

可用 `scripts/verify-package.py --exe 路径 --work-dir 新目录 --channels 8` 复现 8 路打包验证，或加 `--formats --platform windows` 复现原生界面多格式验证（使用默认 2 路）。构建方式见 README 与发行指南。

这些检查验证当前电脑上的便携依赖和模拟拍摄流程；尚未在第二台实体 Windows 电脑上运行，也不能替代 8 台实体 USB 摄像头的带宽与驱动测试。本次打包未修改重复补帧行为，视频标注帧率不代表无重复的有效更新率。
