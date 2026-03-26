# Poland Speed Cameras

Automated daily snapshot of speed cameras and traffic enforcement points across Poland.

## Data source

All data is fetched from the **[NaviExpert Traffic Map](https://traffic.naviexpert.pl)** via its public API (`api-traffic.naviexpert.pl`).

Credit: **NaviExpert** – [https://traffic.naviexpert.pl](https://traffic.naviexpert.pl)

## POI type legend

| `type` | `iconId` | Description (PL)                  | Description (EN)                     |
|--------|----------|-----------------------------------|--------------------------------------|
| 0      | 2311     | Fotoradar                         | Speed camera (photo radar)           |
| 4      | 2324     | Odcinkowy pomiar prędkości        | Average speed check (direction A)    |
| 5      | 2325     | Odcinkowy pomiar prędkości        | Average speed check (direction B)    |
| 7      | 2331     | Kamera                            | Traffic camera                       |

- Types **4** and **5** come in pairs at the same position – they represent the same average-speed section measuring **opposite directions** of traffic. The `direction` field indicates which way each camera faces.
- Type **0** covers both uni- and bidirectional photo radars (description says *dwukierunkowy* for bidirectional).
- Type **7** entries most likely refer to cameras mounted on traffic lights that monitor and detect red-light violations. 

## Usage

```bash
# create venv & install deps
python3 -m venv .venv
.venv/bin/pip install requests

# run
.venv/bin/python3 fetch_radars.py
```

Output is saved to `radars-poland.json`. A GitHub Actions workflow runs this daily at 04:30 UTC and auto-commits when changes are detected.

## Configuration

Edit `config.toml` to change the bounding box, zoom level, tile group size, or request delay.
