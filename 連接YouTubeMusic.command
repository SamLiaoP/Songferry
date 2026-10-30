#!/bin/zsh
cd -- "${0:A:h}" || exit 1
print 'YouTube Music 本機登入設定'
print '此程式會讀取你剛複製的請求標頭，向 YouTube Music 驗證，並將登入資訊存到本機。'
print '請先在 YouTube Music 登入，複製 browse 請求的標頭，再執行這個檔案。'
if [[ ! -x .venv/bin/python ]]; then
  print '請先執行「啟動.command」完成環境設定。'
else
  /usr/bin/pbpaste | .venv/bin/python connect_youtube.py
  # The copied request contains your YouTube cookies; clear the clipboard so it cannot be pasted elsewhere by accident.
  printf '' | /usr/bin/pbcopy
  print '已清除剪貼簿中的登入資料。'
fi
read '?按 Enter 關閉此視窗'
