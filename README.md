# Yt-Dlp API Server (Multi-Server Edition)

Windows用の高機能な動画ダウンロードサーバーです。
複数台のPCを連携させ、1つの管理画面からまとめてダウンロード指示やファイル管理を行うことができます。

> 📖 **詳細な機能一覧は [FEATURES.md](FEATURES.md) をご覧ください。**

## 主な機能

*   **GUIインストーラー**: 複雑なコマンド操作不要で、簡単にセットアップできます。
*   **マルチサーバー対応**: 複数のPCを「サーバーノード」として追加し、負荷分散やストレージの分散が可能です。
*   **自動リカバリー**: サーバープロセスが停止しても、自動的に再起動する監視機能が組み込まれています。
*   **統合ライブラリ**: 接続されている全サーバーのダウンロード済みファイルを1つのリストで管理・再生・ダウンロードできます。
*   **スマートダウンロード**: 「Auto」モードを選択すると、現在最も空いているサーバーに自動的にダウンロードジョブを割り振ります。

## インストール方法

### 1. インストーラーのダウンロード
[Releasesページ](https://github.com/jinrui0713/remoteapi/releases) から最新の `Setup.exe` をダウンロードしてください。

### 2. インストール
`Setup.exe` を実行すると、セットアップ画面が表示されます。

#### 役割の選択 (Role Selection)
*   **Host / Manager (推奨)**:
    *   メインで操作するPC向けです。
    *   サーバー機能に加え、デスクトップにショートカットを作成し、インストール後にブラウザを自動で開きます。
*   **Server Node (Worker)**:
    *   ダウンロード処理専用のサブPC向けです。
    *   バックグラウンドで静かに動作します。

#### 設定 (Configuration)
*   **Install Path**: インストール先フォルダ（通常はそのままでOK）。
*   **Server Port**: 使用するポート番号（デフォルト: `8000`）。

「Install」ボタンを押すと、必要なファイルの配置、自動起動タスクの登録、ファイアウォールの設定が自動的に行われます。

## 使い方

### 1. 管理画面へのアクセス
インストールしたPC（Host）で、デスクトップのショートカット「YtDlp Manager」を開くか、ブラウザで `http://localhost:8000` にアクセスします。

### 2. サーバーの追加（マルチサーバー運用）
他のPCもサーバーとして利用する場合：
1.  そのPCでも `Setup.exe` を実行しインストールします。
2.  メインPCの管理画面右上の **"Servers"** ボタンをクリックします。
3.  **"Add Server"** に、追加したいPCのURLを入力します。
    *   例: `http://192.168.1.15:8000`
4.  追加されると、そのPCのステータスやジョブ状況が表示されるようになります。

### 3. ダウンロード
1.  動画のURLを入力します。
2.  **Target Server** を選択します。
    *   **Auto (Least Busy)**: アクティブなジョブが最も少ないサーバーを自動選択します。
    *   特定のサーバーを指定することも可能です。
3.  「Start Download」をクリックします。

### 4. クッキーの管理 (Cookies)
YouTubeなどの会員限定動画をダウンロードする場合：
1.  `cookies.txt` を用意します。
2.  画面右上の **"Upload Cookies"** からアップロードします。
3.  接続されている**全てのオンラインサーバー**にクッキーが同期されます。

## 開発者向け情報 (Development)

ソースコードからビルドする場合：

### 必要要件
*   Python 3.12+
*   PowerShell

### ビルド手順
```powershell
# 仮想環境の作成と依存関係のインストール
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# ビルドスクリプトの実行
.\build.bat
```
`release` フォルダに `Setup.exe` が生成されます。

## ライセンス
MIT License
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

### 自動設定（推奨）

前述の通り、`cloudflare_token.txt` にトークンを保存してから `setup_full.ps1` を実行するのが最も簡単です。

### 手動設定

もし後から設定したい場合は、以下の手順で行えます。

1.  **Cloudflare Zero Trustの設定**
    *   [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/) にアクセスし、Tunnelを作成してトークンを取得します。

2.  **サービス化スクリプトの実行**
    *   `cloudflare_token.txt` にトークンを保存します。
    *   `setup_cloudflared_service.ps1` を管理者権限で実行します。
    *   自動的にトークンが読み込まれ、サービスとしてインストールされます。

3.  **公開ホスト名の設定**
    *   Cloudflareのダッシュボードで、`Public Hostname` を設定します（Service URL: `http://localhost:8000`）。

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
