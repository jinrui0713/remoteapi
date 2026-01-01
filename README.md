# Windows yt-dlp API Server

Windows上で動作する `yt-dlp` のAPIサーバーです。
Windowsの起動時に自動的にバックグラウンドで開始するように設定できます。

## 必要要件

- Windows 10/11
- Python 3.8以上 (PATHに通っていること)

## 自動セットアップ（推奨）

以下のスクリプトを実行すると、環境構築から公開設定までを全自動で行います。

1. PowerShellを**管理者権限**で開きます。
2. 以下のコマンドを実行します。

```powershell
# スクリプトの実行ポリシーエラーが出る場合は、以下のコマンドで実行してください
powershell -ExecutionPolicy Bypass -File .\setup_full.ps1
```

または、現在のセッションのみ実行を許可する場合：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup_full.ps1
```

## 手動インストール

1. このリポジトリを適当なフォルダに配置します（例: `C:\Tools\remoteapi`）。
2. コマンドプロンプトまたはPowerShellを開き、そのフォルダに移動します。
3. 以下のコマンドを実行して、必要なライブラリをインストールします。

```bash
pip install -r requirements.txt
```

## 使い方（手動実行）

テストなどで手動で起動する場合は、以下のバッチファイルをダブルクリックするか、コマンドラインから実行してください。

```bash
start_server.bat
```

サーバーが起動すると `http://localhost:8000` でアクセス可能になります。
APIドキュメントは `http://localhost:8000/docs` で確認できます。

## 自動起動の設定（推奨）

Windowsの起動時に自動的にサーバーが立ち上がるように設定するには、以下の手順を行います。

1. PowerShellを**管理者権限**で開きます（スタートメニューでPowerShellを検索し、右クリックして「管理者として実行」）。
2. プロジェクトのフォルダに移動します。
   ```powershell
   cd C:\path\to\remoteapi
   ```
3. セットアップスクリプトを実行します。
   ```powershell
   .\setup_autostart.ps1
   ```

成功すると、「タスク 'YtDlpApiServer' が正常に登録されました。」と表示されます。
次回Windowsを再起動した際、ログインしなくてもバックグラウンドでサーバーが自動的に起動します。

## API仕様

APIの仕様書は `openapi.json` として出力されています。
Swagger UIなどで読み込むことで詳細を確認できます。

### 主なエンドポイント

- **POST /download**: 動画のダウンロードをバックグラウンドで開始します。
  - Body: `{"url": "https://www.youtube.com/watch?v=..."}`
- **GET /info**: 動画の情報を取得します（ダウンロードはしません）。
  - Query: `?url=https://www.youtube.com/watch?v=...`

## ログ

サーバーの動作ログは `server.log` に出力されます。エラーが発生した場合などはここを確認してください。

## ダウンロード先

ダウンロードされた動画は、プロジェクトフォルダ内の `downloads` フォルダに保存されます。

## インストーラーの作成（ビルド）

Python環境がないWindows PCでも動作する「完全な配布パッケージ（ffmpeg同梱）」を作成するには、以下の手順を行います。

1. Windows上で `build.bat` を実行します。
   - 自動的にPython環境を作成し、必要なライブラリをインストールします。
   - **FFmpeg** も自動的にダウンロードして同梱します。
2. ビルドが完了すると `release` フォルダが作成されます。
3. この `release` フォルダをそのまま配布してください。

### 配布パッケージのインストール方法

1. `release` フォルダを任意の場所（例: `C:\Program Files\YtDlpApi` など）に配置します。
2. フォルダ内の `setup.ps1` を**管理者権限**で実行します。
   - これにより、自動起動タスクが登録されます。
3. PCを再起動するか、手動で `YtDlpApiServer.exe` を実行すればサーバーが開始されます。
   - `ffmpeg.exe` が同梱されているため、別途インストールの必要はありません。
