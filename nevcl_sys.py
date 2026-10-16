"""機器通信モジュールから使うQt非依存のログ窓口。

GTK版のダイアログ依存を外し、メッセージを標準出力へ記録する。
"""
class _Logger:
    """旧通信コードが期待する logger インターフェースを提供する。"""
    def __init__(self): self.root_win = None
    def activate_dialog(self, win): self.root_win = win
    def log(self, message, *level): print(('ERROR' if level and level[0] >= 2 else 'WARNING' if level and level[0] == 1 else 'NOTE'), message)
    def show(self, message, *level): self.log(message, *level)
class Tooltipper:
    """GTK版互換のための空実装。Qt画面ではQt標準のツールチップを使う。"""
    def __init__(self, *_args, **_kwargs): pass
logger = _Logger()
