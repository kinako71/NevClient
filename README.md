# NEV Client

| ファイル                                             | 役割                                                     |
| ---------------------------------------------------- | -------------------------------------------------------- |
| `nevcl.py`                                           | 起動処理、メイン画面、各パネルと計測処理の連携           |
| `nevcl_param.py`                                     | パラメータ／属性／DAQmx機器パネル                        |
| `nevcl_csvif.py`                                     | CSV読込・保存とパラメータセット管理                      |
| `nevcl_psa.py`                                       | スイープ設定、NI-Scopeチャンネル設定、波形プロットパネル |
| `nevcl_daqmx.py`, `nevcl_niscope.py`, `nevcl_tcp.py` | NEVサーバー通信と計測機器制御                            |
| `nevcl_sys.py`                                       | UIに依存しないロガー                                     |

## 起動

```sh
python -m pip install -r requirements.txt
python nevcl.py [サーバーアドレス]
```

接続には対応するNEVサーバーとDAQmx／NI-Scope環境が必要です。

## 編集するときの目安

- ウィンドウの配置、ボタン処理、接続や掃引の流れ: `nevcl.py`
- 属性／パラメータ一覧の列幅、フォント、スピンボックス: `nevcl_param.py`
- スイープ設定、NI-Scopeチャンネル画面、グラフの見た目: `nevcl_psa.py`
- CSVの必須属性列、セット追加・削除、CSV形式: `nevcl_csvif.py`
- サーバー通信のコマンドと応答解析: `nevcl_tcp.py`, `nevcl_daqmx.py`, `nevcl_niscope.py`

PyQtの画面部品はパネル側、ボタン後の処理やパネル間の受け渡しは `nevcl.py` にあります。
