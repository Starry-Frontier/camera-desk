# GitHub 发行流程

仓库包含应用源码、MIT 许可证、第三方许可证、测试与 Windows 自动构建流程。将这个项目作为仓库根目录上传，不要上传 `.venv`、拍摄文件、设置、日志和 `dist`。

## 生成本地发行文件

先按 README 创建环境、安装依赖并运行测试，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -DistDirectory build\portable-dist
powershell -ExecutionPolicy Bypass -File .\build.ps1 -SingleFile -NoZip -DistDirectory build\standalone-dist
powershell -ExecutionPolicy Bypass -File .\scripts\package-source.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\fetch-third-party-sources.ps1 -BundleDirectory build\portable-dist\CameraDesk
```

产物：

| 文件 | 用途 |
| --- | --- |
| `CameraDesk-Windows-x64.zip` | 包含 EXE、动态库、许可证和应用源码的便携版 |
| `CameraDesk-Standalone-Windows-x64.exe` | 自带运行组件的单文件版，可单独复制运行 |
| `CameraDesk-Source.zip` | 可直接上传 GitHub 的项目源码，包含工作流和文档 |
| `CameraDesk-ThirdParty-Sources.zip` | 对应 Qt / PySide / Shiboken / FFmpeg 源码与构建资料 |
| `SHA256SUMS.txt` | 由发行工作流生成的上述四个文件的 SHA-256 |

第三方源码下载脚本会校验固定的 SHA-256。不要把上游下载链接当作已经附带源码；公开发行时请上传完整的第三方源码归档，并随二进制包保留相同的版本与许可证。修改依赖或增加 Qt 模块后，必须更新源码清单并重新核对。

## 在 GitHub 创建版本

1. 核对 `multicam/__init__.py` 的版本、`CHANGELOG.md` 及 `docs/RELEASE_NOTES_v版本.md`。
2. 在 GitHub 建立仓库并推送源码。
3. 创建并推送对应标签，例如当前版本的 `v1.2.2`。
4. `Build Windows release` 会测试、打包，上传构建产物，并创建草稿发行版。
5. 检查草稿中的便携包、应用源码、第三方源码、校验值和说明；在第二台 Windows 电脑测试后，再在 GitHub 点击发布。

工作流只使用官方 checkout、setup-python、upload-artifact，以及 GitHub CLI。常规 CI 只读；发行任务在创建草稿时需要仓库内容写权限。手动运行工作流只上传 Actions 构建产物，不创建版本标签。

