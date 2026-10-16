"""パラメータCSVの読み書きとセット列の管理。

行データの形式は元のNEV CSVに合わせる。GUI表示は行わず、画面から利用するモデル。
"""
import csv

ATTRS=(('ID','MAN'),('COMMENT','OPT'),('DEV','MAN'),('CH','MAN'),('PIN','OPT'),('LABEL','MAN'),('NCMODE','MAN'),('BDMODE','OPT'))

class CsvParameterFile:
    """属性列とパラメータセット列を解釈するCSVモデル。"""
    def __init__(self,rows,path=None):
        # ヘッダーの#属性名を探し、それより後ろの通常列をセットとして扱う。
        if not rows: raise ValueError('CSVが空です')
        self.rows=rows; self.path=path
        self.columns={name:rows[0].index('#'+name) for name,_ in ATTRS if '#'+name in rows[0]}
        missing=[name for name,required in ATTRS if required=='MAN' and name not in self.columns]
        if missing: raise ValueError('必須属性列がありません: '+', '.join(missing))
        boundary=max(self.columns.values())
        self.parameter_columns=[i for i,name in enumerate(rows[0]) if i>boundary and not name.startswith('#')]
    @classmethod
    def read(cls,path):
        """CSVをUTF-8で読み込み、列構成を検証してモデルを返す。"""
        with open(path,newline='',encoding='utf-8-sig') as f: return cls(list(csv.reader(f)),path)
    def save(self,path=None):
        """現在の行データを指定先（省略時は元ファイル）へ保存する。"""
        target=path or self.path
        if not target: raise ValueError('保存先が指定されていません')
        with open(target,'w',newline='',encoding='utf-8') as f: csv.writer(f).writerows(self.rows)
        self.path=target
        return target
    def add_set(self,name):
        """新しいセット列を追加し、各パラメータの初期値を0にする。"""
        if name in [self.rows[0][c] for c in self.parameter_columns]: raise ValueError('同名のセットがあります')
        col=len(self.rows[0]); self.rows[0].append(name)
        for row in self.rows[1:]: row.append('0')
        self.parameter_columns.append(col)
    def delete_set(self,index):
        """セット列を削除し、後続列のインデックスを詰める。"""
        col=self.parameter_columns.pop(index)
        for row in self.rows: row.pop(col)
        self.parameter_columns=[c-1 if c>col else c for c in self.parameter_columns]
