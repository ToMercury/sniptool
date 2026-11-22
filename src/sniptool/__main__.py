import sys

from .bootstrap import setup_qt_plugins

setup_qt_plugins()

from .app import main  # noqa: E402  必须在设置 QT_PLUGIN_PATH 之后导入

if __name__ == "__main__":
    sys.exit(main())
