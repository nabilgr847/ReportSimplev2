# Colab: run these cells in order

## Cell 1: install
```
!apt-get -qq install -y tesseract-ocr tesseract-ocr-ben poppler-utils
!pip -q install streamlit openai pytesseract pdfplumber pdf2image pillow
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared
```

## Cell 2: upload files (app.py, rules.py, kb.py, patterns.py, eval.py)
```
from google.colab import files
files.upload()
```

## Cell 3: API key (use Colab Secrets: key icon on the left, name FEATHERLESS_API_KEY)
```
import os
from google.colab import userdata
os.environ["FEATHERLESS_API_KEY"] = userdata.get("FEATHERLESS_API_KEY")
```

## Cell 4: run the app
```
import subprocess, time
subprocess.Popen(["streamlit","run","app.py","--server.port","8501","--server.headless","true"])
time.sleep(5)
!./cloudflared tunnel --url http://localhost:8501
```
Open the https://....trycloudflare.com link it prints.

## Cell 5 (separate): evaluation numbers for your write-up
```
!python eval.py
```
