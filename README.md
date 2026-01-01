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

## 利用方法

### 1. Web UI

ブラウザで `http://localhost:8000` にアクセスすると、シンプルなWebインターフェースが表示されます。
ここから動画のURLを入力してダウンロードしたり、ダウンロード状況を確認したりできます。

### 2. APIの利用

外部プログラムからAPIを呼び出して利用することもできます。

#### 動画情報の取得

```http
GET /info?url={video_url}
```

**レスポンス例:**
```json
{
  "title": "Video Title",
  "duration": 120,
  "uploader": "Channel Name",
  "view_count": 1000,
  "url": "https://..."
}
```

#### ダウンロードの開始

```http
POST /download
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=...",
  "type": "video",       // "video" or "audio"
  "quality": "best",     // "best", "1080", "720", "480"
  "audio_format": "mp3"  // "mp3", "m4a", "wav" (type=audioの場合)
}
```

**レスポンス例:**
```json
{
  "job_id": "uuid-string",
  "message": "Queued"
}
```

#### ジョブ状態の確認

```http
GET /jobs/{job_id}
```

### 3. Pythonクライアントの例

`client_example.py` にPythonからAPIを利用するサンプルコードが含まれています。
必要に応じて `BASE_URL` を変更して利用してください。

```python
# client_example.py の一部
import requests

BASE_URL = "http://localhost:8000"

def test_download(video_url):
    payload = {"url": video_url, "type": "video", "quality": "best"}
    response = requests.post(f"{BASE_URL}/download", json=payload)
    print(response.json())
```

## 高度な設定：Cloudflare Tunnel（独自ドメイン）

Cloudflareのアカウントとドメインをお持ちの場合、固定ドメインで外部公開し、Windowsサービスとして常駐させることができます。
これにより、PCを再起動しても自動的に接続が復帰し、常に同じURLでアクセス可能になります。

### 手順

1.  **Cloudflare Zero Trustの設定**
    *   [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/) にアクセスします。
    *   左メニューから `Networks` > `Tunnels` を選択し、`Create a tunnel` をクリックします。
    *   Connectorとして `Cloudflared` を選択します。
    *   OS選択画面で `Windows` を選択すると、インストールコマンドが表示されます。
    *   そのコマンド内にある **トークン** （`eyJh...` で始まる長い文字列）だけをコピーして控えておきます。

2.  **サービス化スクリプトの実行**
    *   このフォルダにある `setup_cloudflared_service.ps1` を右クリックし、「PowerShellで実行」を選択するか、管理者権限のPowerShellから実行します。
    *   「トークンを入力してください」と表示されたら、先ほどコピーしたトークンを貼り付けてEnterキーを押します。

3.  **公開ホスト名の設定**
    *   Cloudflareのダッシュボードに戻り、`Next` をクリックします。
    *   `Public Hostname` タブで、公開したいドメイン（例: `api.yourdomain.com`）を設定します。
    *   `Service` の設定で、Typeを `HTTP`、URLを `localhost:8000` に設定します。
    *   `Save Tunnel` をクリックして完了です。

これで、設定したドメイン経由でAPIサーバーにアクセスできるようになります。

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

## インターネットへの公開方法

作成された `start_public.bat` を使用することで、ローカルのサーバーを安全にインターネットに公開できます。

1. フォルダ内の `start_public.bat` をダブルクリックして実行します。
2. 黒い画面（コマンドプロンプト）が開き、サーバーと通信トンネルが起動します。
3. 画面内に以下のようなURLが表示されます。
   ```
   https://xxxx-xxxx-xxxx.trycloudflare.com
   ```
4. このURLをスマホや他のPCから開くと、APIサーバーにアクセスできます。

※ このURLは起動するたびに変わります。
※ 画面を閉じると公開も終了します。

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
