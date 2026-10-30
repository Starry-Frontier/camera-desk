# 1～8 路同时录像模拟测试

运行项目自带的动态演示画面，通过正式 `MainWindow`、`CameraWorker`、预览、异步拍照和视频编码流程测试。测试脚本不打开实体摄像头，不修改已安装软件的数据；录像、照片、预览和 JSON 结果写入独立测试目录。

## 运行方式

在项目目录使用已安装依赖的 Python 运行，输出目录必须为空：

```powershell
# 1～8 路逐档测试，每档 15 秒，1280×720 / 30 fps。
.\.venv\Scripts\python.exe scripts\verify-multichannel.py --output-dir verification\channels-1-to-8 --counts 1 2 3 4 5 6 7 8 --seconds 15

# 8 路持续 120 秒，混合 AVI、MP4、MKV，录像期间保存全部五种图片格式。
.\.venv\Scripts\python.exe scripts\verify-multichannel.py --output-dir verification\channels-8-endurance --counts 8 --seconds 120 --format mixed --photo-rounds 5

# 8 路 1920×1080 / 30 fps，持续 60 秒。
.\.venv\Scripts\python.exe scripts\verify-multichannel.py --output-dir verification\channels-8-1080p --counts 8 --seconds 60 --width 1920 --height 1080 --format mixed --photo-rounds 3
```

程序使用 Qt 的离屏模式，显示和绘制完整测试窗口但不占用用户桌面。默认的设备扫描在测试窗口中关闭，正式图像生成、录像和拍照实现保持不变。增加的采集线程子类仅记录采样和实际写入次数，调用正式编码实现；未替换编码器、磁盘输出或录像时钟。

## 检查内容

- 同时启动指定路数，各通道仅开始和停止一次，不发生自动中断或重启。
- 每个视频从头至尾逐帧解码，检查帧数、尺寸、帧率和真实录制时长；首、中、末画面的通道标识匹配。
- 录像期间点击“全部拍照”，验证数量、唯一文件名、共同保存目录、格式和图片尺寸；不允许出现保存或覆盖确认窗口。
- 分别统计模拟源采样率、录制文件标注帧率、重复补帧数量以及录像中使用的新采样数。
- 记录测试进程 CPU、内存及界面心跳间隔。CPU 比例按当前电脑全部逻辑处理器归一化；不代表整机 CPU 总占用。

`functional_pass` 表示上述文件和流程检查通过。`realtime_pass` 另外要求每路模拟源采样率不低于目标的 95%，且重复补帧比例不超过 5%。因此文件完整、时长正确与无重复帧的流畅录像是不同检查项目。

脚本退出码 0 表示功能检查通过；实时性结果需单独查看 JSON 或报告，未达实时性目标不会掩盖成功保存的文件。

“录像有效更新 fps”＝（视频写入帧数－使用同一采样重复写入的帧数）／视频时长。它描述新采样的使用率；模拟源产生 30 fps、文件标注 30 fps，都不能单独证明录像使用了每一个新采样。

AVI 还会统计完全相同的相邻解码画面，作为补帧计数的交叉核对；模拟场景自身在运动慢时也可能产生相同画面。MP4 的相同输入可能解码出轻微像素差异，故不以逐像素相等判断该格式的补帧。

本测试验证软件模拟负载，不覆盖 USB 带宽、设备驱动、相机原生模式、硬件同步，也不替代其他 Windows 电脑或 8 台实体摄像头的测试。模拟场景纹理较简单，编码负载可能低于真实拍摄画面。
