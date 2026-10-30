# 開發說明

## 現況

Songferry（原名 Playlist Bridge）是一個使用 Flask、原生 JavaScript 與 ytmusicapi 的個人本機應用程式，原始碼在 https://github.com/SamLiaoP/Songferry 。Python 3.10 以上可執行，建議 3.12。它支援 Spotify → YouTube Music 與 YouTube Music → Spotify，兩個方向都建立新的私人歌單。

目前以單一使用者及單一 Python 伺服器程序為前提。Spotify 登入與比對紀錄都保存在 `.local/`，重啟後由 `restore()` 讀回。介面提供繁體中文與英文。

## 檔案結構

| 檔案 | 用途 |
|---|---|
| `app.py` | Flask 路由、CSRF/Host 檢查、Spotify PKCE、工作管理、`.local` 持久化與轉移流程 |
| `matching.py` | 文字正規化、歌名/歌手/長度評分、版本差異警示 |
| `reverse.py` | YouTube 歌單解析、Spotify 搜尋、每批 100 首寫入與讀回核對 |
| `connect_youtube.py` | 登入標頭解析（cURL / fetch / 原始標頭）、帳號驗證、安全存檔；供 `/api/youtube/connect` 與 `.command` 共用 |
| `index.html` / `style.css` / `app.js` | 介面、左側比對紀錄、方向切換、比對結果、人工調整、工作恢復與語言切換 |
| `i18n/en.json` | 英文翻譯表：以繁中原文為 key，含 `{name}` 佔位與處理動態訊息的 regex patterns |
| `test_app.py` | 原始方向、請求保護、Spotify 登入與建立歌單測試 |
| `test_reverse.py` | 反向讀取、Spotify 搜尋、權限、批次與部分失敗測試 |
| `test_connect_youtube.py` | 登入資料解析、驗證失敗保留舊設定與檔案權限測試 |
| `test_persistence.py` | Spotify 登入與比對紀錄的保存、重啟恢復、中斷標記、刪除與選擇同步測試 |
| `test_i18n.py` | 翻譯表涵蓋所有介面字串與伺服器中文訊息、佔位符一致、翻譯檔路由 |
| `啟動.command` | macOS 啟動；只在第一次建立 `.venv` 時安裝依賴 |
| `連接YouTubeMusic.command` | macOS 剪貼簿匯入本機登入資料，完成後清空剪貼簿 |

## 環境與執行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Windows 可使用 `.venv\Scripts\python.exe` 取代 `.venv/bin/python`。`.command` 啟動器與剪貼簿 helper 針對 macOS；其他系統可使用 README 的 ytmusicapi 終端機設定流程。

網址固定為 `http://127.0.0.1:8876`。Spotify App 的 Redirect URI 必須設定成 `http://127.0.0.1:8876/callback`。如果要改連接埠，需同步修改 `app.py`、介面設定教學、啟動器及 README。

使用者在介面填入自己的 Spotify Client ID；儲存庫沒有預先設定任何帳號。YouTube Music 登入檔由本機使用者自行建立。

## 流程

1. 前端選擇方向並提交來源 URL，後端驗證 URL 與登入狀態。
2. `POST /api/jobs` 建立工作並立即寫入 `.local/jobs/<id>.json`，背景執行緒讀取來源並逐首搜尋，每 10 首及結束時存檔。
3. 前端輪詢工作結果。每首預設使用第一個候選；使用者可以換選、搜尋或略過，選擇與歌單名稱經 `POST /api/jobs/<id>/selections` 保存到該工作。
4. 使用者按下建立按鈕，前端傳送工作 ID、名稱與候選 ID。
5. 後端驗證選擇來自該工作，建立私人歌單並讀回核對歌曲 ID 與順序。

## HTTP API

所有 API 僅限本機 Host。POST 與 DELETE 需要由 `GET /api/status` 取得的 `X-CSRF-Token`；瀏覽器 Origin 需符合本機網址。

| 方法 / 路徑 | 用途 |
|---|---|
| `GET /i18n/<lang>.json` | 翻譯表；目前只有 `en` |
| `GET /api/status` | 取得連接狀態、Client ID、CSRF token 與 Spotify 私人歌單寫入權限狀態 |
| `POST /api/config` | 儲存 `client_id`，並清除已保存的 Spotify 登入 |
| `POST /api/logout` | 登出 Spotify，刪除 `.local/spotify_token.json` |
| `POST /api/youtube/connect` | 接收 `text`（瀏覽器複製的 cURL、fetch 或標頭），本機解析、驗證並存成 `browser.json`，回傳帳號名稱 |
| `GET /api/youtube/browsers` | 偵測本機有哪些瀏覽器設定檔可匯入（只看路徑，不讀檔） |
| `POST /api/youtube/import` | 接收 `browser`，用 browser_cookie3 讀取該瀏覽器的 YouTube Cookie，驗證並存檔；macOS 會觸發鑰匙圈授權 |
| `POST /api/youtube/disconnect` | 刪除 `browser.json` 與 `youtube_account.json` |
| `POST /api/login` | 接收 `direction`，回傳 Spotify PKCE 授權網址 |
| `GET /callback` | 接收並驗證 Spotify OAuth state、交換 token |
| `GET /api/jobs` | 列出所有比對紀錄摘要（不含候選內容），新的在前 |
| `POST /api/jobs` | 接收 `url`、`direction`，建立比對工作 |
| `GET /api/jobs/<id>` | 取得進度與候選結果 |
| `DELETE /api/jobs/<id>` | 刪除一筆紀錄與其檔案；進行中的工作不能刪除 |
| `POST /api/jobs/<id>/selections` | 接收 `selections`（可為前綴）與 `title`，保存使用者的選擇 |
| `POST /api/search` | 接收 `job`、`index`、`query`，補充候選結果 |
| `POST /api/create` | 接收 `job`、`title`、`selections`、`lang`，建立目標歌單；`lang` 只影響歌單描述文字 |

方向值為 `spotify-to-youtube` 或 `youtube-to-spotify`。

前端透過 `candidateId()` 相容兩種結果格式：原始方向使用 `videoId`，反向使用 Spotify `id` 及 `url`。後續若整理資料模型，可統一候選 ID 與平台欄位。

## 語言

繁體中文是原始語言：`index.html` 與 `app.js` 裡的字串都是中文，伺服器的錯誤訊息也是。英文由 `i18n/en.json` 提供，前端的 `t()` 以中文原文查表，`{name}` 佔位由前端填入；伺服器回傳的中文訊息（含存在 job 檔裡的 `row.error`、`warnings`、`result.warning`）也在前端翻譯，因此後端與測試不需要知道語言。含數字的動態訊息由 `patterns` 的 regex 處理。靜態 HTML 以 `data-i18n`（文字）與 `data-i18n-attr`（屬性）標記；語言選擇存在 localStorage，預設依瀏覽器語言。新增字串時在兩邊都加上，`test_i18n.py` 會檢查漏翻。

## 帳號與狀態

- `.local/config.json`：使用者的 Spotify Client ID。
- `.local/browser.json`：含 YouTube Music 登入資訊，建立時限制為 `0600`，目錄為 `0700`。`youtube_account.json` 只存驗證時取得的帳號名稱，供介面顯示。
- 貼上的請求由 `connect_youtube.parse_headers` 解析：支援 Copy as cURL（`-H`/`-b`，含 `\\` 續行）、Copy as fetch (Node.js) 的 JSON、原始標頭行。只保留登入相關標頭，Cookie 需含 `__Secure-3PAPISID`（ytmusicapi 用它計算 SAPISIDHASH），`x-goog-authuser` 缺少時預設 `0`。存檔前 `complete_headers` 會補上 ytmusicapi 的標準標頭與 `authorization: SAPISIDHASH …`；ytmusicapi 1.12 只有看到這個標頭才會把設定當成瀏覽器登入，否則會當成 OAuth 而失敗。
- `.local/spotify_token.json`：Spotify access/refresh token，`0600`。登入、更新 token 時寫入；登出、更換 Client ID 或 refresh 失敗時刪除。
- `.local/jobs/<id>.json`：每個比對工作的完整內容（來源歌曲、候選、使用者選擇、建立結果）。所有寫入都用暫存檔加 `os.replace` 原子取代。啟動時 `restore()` 讀回；狀態仍為 `matching` 的工作標記為 `error`（中斷），`creating` 為真的工作保持 `attempted`，不會重送建立。
- OAuth 待完成狀態：僅存在伺服器記憶體。
- 分頁的 sessionStorage：只保存目前工作 ID 與方向；歌曲選擇以伺服器為準。
- 所有 `.local` 路徑都從模組變數 `LOCAL` 推導，測試以 `patch.object(module, 'LOCAL', …)` 導向暫存資料夾，不會碰到真實資料。
- 來源內容及 API 使用受平台存取限制影響；詳見 README 中的平台文件連結。

## 比對與寫入行為

評分使用歌名 55%、歌手 35%、長度 10%，遇版本標記不同再扣分。這是啟發式評分，不是機率。依目前產品設定，第一個候選會先選取，低分或版本警示仍顯示。

Spotify 每次最多加入 100 首，保留順序與重複歌曲。若部分請求失敗，會保留可取得的目標連結，顯示未確認內容，且不自動重送可能已被接受的寫入。同一工作僅允許一次建立嘗試；記憶體中的結果可重複查詢。這不等同於跨程序或跨重啟的持久化冪等機制。

## 測試

```sh
.venv/bin/python -B -m unittest -v test_app.py test_connect_youtube.py test_reverse.py test_persistence.py test_i18n.py
node --check app.js
```

Python 測試可離線執行，使用模擬 API，不需帳號，也不會建立真實歌單。目前 50 項測試通過。實際端到端驗證需另用個人帳號與小型測試歌單完成。

## 後續開發方向

- 把平台 API 與 Flask 路由分離，統一來源歌曲及候選結果模型。
- 為比對工作加入取消功能，以及中斷後從已完成的歌曲續跑。
- 改進藝人別名、中英文歌名及版本辨識；增加實際案例回歸測試。
- 支援更多平台（例如 Apple Music）；目前只有 Spotify 與 YouTube Music。
- 加入批次建立進度與可確認的部分失敗恢復。
- 將個人本機版的帳號、工作與儲存模型重新設計後，再考慮多人網站部署。
