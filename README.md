# Crypto Trend MVP

FastAPI + SQLAlchemy tabanlı, kripto mum (candlestick) verilerini yerel veritabanından okuyup API ile sunan MVP proje.

## Özellikler

- FastAPI ile REST API
- SQLAlchemy ORM ile veritabanı erişimi
- Sembol/interval bazlı son mumları çekme
- Basit debug endpoint’leri
- Swagger UI (`/docs`) ile interaktif test

---

## Proje Yapısı

```text
crypto-trend-mvp/
├─ app/
│  ├─ db/
│  │  ├─ models/
│  │  │  └─ candle.py
│  │  └─ session.py
│  └─ main.py
├─ requirements.txt
└─ README.md
```

---

## Kurulum

### 1) Depoyu klonla
```bash
git clone git@github.com:bilalozok/crypto-trend-mvp.git
cd crypto-trend-mvp
```

### 2) Sanal ortam oluştur/aktif et
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Bağımlılıkları yükle
```bash
pip install -r requirements.txt
```

---

## Uygulamayı Çalıştırma

```bash
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

Uygulama açıldıktan sonra:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

> Eğer `Address already in use` hatası alırsan:
```bash
lsof -ti:8000 | xargs kill -9
```
ve tekrar çalıştır.

---

## Endpointler

## 1) Health Check
### `GET /`
API’nin ayakta olup olmadığını kontrol eder.

**Örnek cevap**
```json
{
  "message": "API ayakta 🚀"
}
```

---

## 2) Son mumları getir (filtreli)
### `GET /candles/latest`

Belirli bir `symbol` ve `interval` için en güncel mum kayıtlarını döner.

### Query Parametreleri

- `symbol` (zorunlu, string)  
  Örnek: `BTCUSDT`
- `interval` (opsiyonel, string, varsayılan: `1h`)
- `limit` (opsiyonel, integer, varsayılan: `5`, min: `1`, max: `500`)

**Örnek istek**
```text
GET /candles/latest?symbol=BTCUSDT&interval=1h&limit=5
```

**Örnek cevap**
```json
[
  {
    "id": 101,
    "symbol": "BTCUSDT",
    "interval": "1h",
    "open_time": 1727443200000,
    "open": 64000.5,
    "high": 64210.0,
    "low": 63850.2,
    "close": 64120.7,
    "volume": 1234.56
  }
]
```

---

## 3) Ham son kayıtlar
### `GET /candles/latest_raw`

Filtre uygulamadan, veritabanındaki en güncel kayıtları döner.

### Query Parametreleri

- `limit` (opsiyonel, integer, varsayılan: `5`)

**Örnek istek**
```text
GET /candles/latest_raw?limit=5
```

---

## 4) Veritabanı debug
### `GET /debug/db`

Toplam kayıt sayısını ve en güncel örnek kaydı döner.

**Örnek cevap**
```json
{
  "count": 2500,
  "sample": {
    "id": 2500,
    "symbol": "BTCUSDT",
    "interval": "1h",
    "open_time": 1727443200000,
    "open": 64000.5,
    "high": 64210.0,
    "low": 63850.2,
    "close": 64120.7,
    "volume": 1234.56
  }
}
```

---

## Kullanışlı Geliştirici Komutları

## Import kontrolü
```bash
python3 -c "from app.main import app; print('MAIN OK')"
```

## Çalışan uvicorn süreçlerini kapatma (port 8000)
```bash
lsof -ti:8000 | xargs kill -9
```

---

## Notlar

- `symbol` filtrelemesi endpoint içinde `upper()` ile yapılır. (`btcusdt` gönderilse de `BTCUSDT` olarak aranır.)
- Bu sürümde veri çekme (fetch) endpoint’i henüz ekli değil; mevcut yapı veritabanında bulunan mumları servis eder.
- Sonraki adım olarak Binance entegrasyonu ile `POST /candles/fetch/{symbol}` eklenebilir.

---

## Lisans

Bu proje MVP/demo amaçlıdır. Lisans ihtiyacına göre güncellenebilir.
