"""Chinese standard button captions without external Qt translation files."""
from PySide6.QtCore import QTranslator


class ChineseButtonTranslator(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        if context != 'QPlatformTheme':
            return ''
        captions = {
            'OK': '确定', 'Cancel': '取消', 'Yes': '是', 'No': '否',
            'Close': '关闭', 'Save': '保存', 'Open': '打开', 'Apply': '应用',
            'Reset': '重置', 'Help': '帮助', 'Discard': '放弃',
            'Abort': '中止', 'Retry': '重试', 'Ignore': '忽略',
            'Save All': '全部保存', 'Yes to All': '全部选是',
            'No to All': '全部选否', 'Restore Defaults': '恢复默认值',
            "Don't Save": '不保存',
        }
        return captions.get(source.replace('&', ''), '')


def install_chinese_buttons(app):
    if getattr(app, '_camera_desk_translator', None) is None:
        translator = ChineseButtonTranslator(app)
        app.installTranslator(translator)
        # Keep the Python virtual-method implementation alive with the app.
        app._camera_desk_translator = translator
