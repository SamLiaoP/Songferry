# 參與開發

儲存庫在 https://github.com/SamLiaoP/Songferry 。歡迎用 Issue 回報問題或先討論想做的改動，再送 Pull Request。

## 開始修改

1. 依 README 建立 Python 虛擬環境並安裝依賴。
2. 執行全部單元測試，確認環境正常。
3. 修改相關模組；平台 API、權限、分頁或寫入流程改動應補上對應測試。
4. 執行 Python 測試（`.venv/bin/python -m unittest discover -s tests`）及 `node --check static/app.js`，並以本機介面確認操作流程。新增介面或錯誤字串時，同時更新 `static/i18n/en.json`；`tests/test_i18n.py` 會檢查漏翻。
5. 提交說明寫明行為變化、驗證方式及仍待驗證的外部 API 行為。

## 初始化自己的 Git 儲存庫

```sh
git init
git add .
git status --short
```

檢查加入的檔案後再提交。帳號設定保留在忽略的 `.local/` 目錄；請勿把 Cookie、OAuth token、真實登入標頭或含敏感內容的截圖放入測試案例、issue 或提交中。範例與測試使用假資料。

## 對外發佈

專案授權以儲存庫根目錄的 `LICENSE` 為準；依賴套件保留其各自的授權。
