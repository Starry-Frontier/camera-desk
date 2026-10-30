# 参与开发

本项目的应用代码采用 MIT 许可证。提交贡献时请确认你有权以该许可证提供代码；第三方组件继续遵循各自许可证。

使用 Windows 10 / 11 64 位和 Python 3.11 进行开发。当前本地初版也在 Python 3.8.6 验证，CI 与后续发行构建采用 Python 3.11。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py --demo
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q --basetemp test-output/pytest
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

测试使用模拟视频源，不需要摄像头。涉及设备兼容性的修改，请在 PR 中补充实际设备型号、Windows 版本、USB 连接方式和请求／实际分辨率。不要把拍摄内容、个人配置、日志或构建目录提交到仓库。

提交 PR 时写明具体问题、修改后的行为和验证结果。采集与磁盘写入应留在摄像线程；主界面不应阻塞等待单个机位。新增参数必须明确是软件图像处理还是设备硬件控制。

发布步骤见 [docs/RELEASING.md](docs/RELEASING.md)。

